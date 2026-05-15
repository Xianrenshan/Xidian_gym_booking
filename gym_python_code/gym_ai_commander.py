import json
import os
import time
import threading
import queue
import logging
from datetime import datetime
from typing import Optional

from playwright.sync_api import sync_playwright
import requests
from openai import OpenAI

# ============================================================================
# ⚙️ 第一部分：系统与业务全局配置
# ============================================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 业务常量 ---
LOGIN_URL = "https://tybsouthgym.xidian.edu.cn/"            
HOME_URL = "https://tybsouthgym.xidian.edu.cn/"             
BASE_URL = "https://tybsouthgym.xidian.edu.cn/Field/OrderField"
ORDER_LIST_URL = "https://tybsouthgym.xidian.edu.cn/Field/GetFieldOrder"
STATE_FILE = "auth_state.json"
MEMORY_FILE = "battle_memory.txt"  # 🧠 大模型的隔日记忆文件

DATE_ADD = 2
PRICE = "2.00"
TARGET_TIME = {"begin": "14:00", "end": "17:00"}
MAX_TOTAL_THREADS = 12 # 系统允许的最大总并发兵力

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
    "Accept": "*/*",
    "X-Requested-With": "XMLHttpRequest",
    "Connection": "keep-alive"
}

# --- AI 指挥官配置 ---
LLM_API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxx" # 填入你的大模型API Key
LLM_BASE_URL = "https://api.deepseek.com/v1" 
LLM_MODEL = "deepseek-chat"

client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

GLOBAL_COOKIE = ""
FIRE_EVENT = threading.Event() # 12:00:00 开火的全局信号枪


# ============================================================================
# 🛡️ 第二部分：后勤保障部队 (Playwright 提取 Cookie)
# ============================================================================
class GymAuthKeeper:
    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self._state = None

    def first_login(self) -> None:
        logger.info("启动浏览器进行首次登录...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
            context = browser.new_context(viewport={"width": 1280, "height": 720}, user_agent=HEADERS["User-Agent"])
            page = context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded")

            print("\n" + "=" * 50)
            print("请在浏览器中用微信扫码登录。登录成功（跳转到预约界面）后，按回车键继续...")
            print("=" * 50 + "\n")
            input(">> 按回车确认登录完成 <<")

            context.storage_state(path=self.state_file)
            browser.close()
        self._load_state()

    def _load_state(self) -> Optional[dict]:
        if os.path.exists(self.state_file):
            with open(self.state_file, "r") as f:
                self._state = json.load(f)
            return self._state
        return None

    def get_valid_cookie(self) -> str:
        if self._state is None and self._load_state() is None:
            raise RuntimeError("无可用认证状态，请先执行首次登录。")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            context = browser.new_context(storage_state=self._state, user_agent=HEADERS["User-Agent"])
            page = context.new_page()
            
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)

            for sel in ['.layui-layer-close', '.dialog-close', '.close', '#close', 'button:has-text("关闭")', 'button:has-text("×")']:
                try:
                    el = page.locator(sel).first
                    if el.count() > 0 and el.is_visible():
                        el.click(timeout=1000)
                except:
                    continue

            new_state = context.storage_state()
            self._state = new_state
            with open(self.state_file, "w") as f:
                json.dump(new_state, f)
            browser.close()

        cookies = self._state.get("cookies", [])
        return "; ".join([f"{c['name']}={c['value']}" for c in cookies])

auth_keeper = GymAuthKeeper()


# ============================================================================
# 🧠 第三部分：全局战术沙盘 (State) & 兵力微操控制器
# ============================================================================
class BattleState:
    def __init__(self):
        self.lock = threading.Lock()
        self.is_victory = False
        self.auth_status = "VALID"
        
        # 记录场地的战况特征 (供大模型推演)
        self.fields_intel = {} 
        
        # 动态兵力与油门控制器
        # 结构: {"JSP031": {"delay_ms": 500, "threads": [stop_event_1, stop_event_2]}}
        self.troops_config = {}

    def update_field_intel(self, field_no, status, msg=""):
        with self.lock:
            if field_no not in self.fields_intel:
                self.fields_intel[field_no] = {"status": "IDLE", "msg": msg, "fails": 0}
            self.fields_intel[field_no]["status"] = status
            self.fields_intel[field_no]["msg"] = msg
            if status in ["DEAD", "WAF_BLOCK"]:
                self.fields_intel[field_no]["fails"] += 1

    def get_summary(self):
        """生成供大模型决策的态势感知 JSON"""
        with self.lock:
            deployment_summary = {}
            for field, conf in self.troops_config.items():
                active_count = len([e for e in conf["threads"] if not e.is_set()])
                if active_count > 0:
                    deployment_summary[field] = {"active_threads": active_count, "delay_ms": conf["delay_ms"]}
            
            return json.dumps({
                "time": datetime.now().strftime('%H:%M:%S'),
                "auth_status": self.auth_status,
                "current_deployments": deployment_summary,
                "fields_intel": self.fields_intel
            }, ensure_ascii=False)

state = BattleState()
event_bus = queue.Queue()


# ============================================================================
# ⚔️ 第四部分：前线肌肉部队 (动态油门刺客 & 侦察兵)
# ============================================================================
def killer_worker(field_no, stop_event, config_dict):
    """【刺客线程】遵守指挥官下发的 delay_ms，并在 12:00:00 统一开火"""
    session = requests.Session()
    session.headers.update(HEADERS)
    
    item = {"FieldNo": field_no, "FieldTypeNo": "006", "BeginTime": TARGET_TIME["begin"], "Endtime": TARGET_TIME["end"], "Price": PRICE}
    params = {"checkdata": json.dumps([item], ensure_ascii=False), "dateadd": DATE_ADD, "VenueNo": "01"}

    # 等待全局发令枪 (12:00:00)
    FIRE_EVENT.wait()
    event_bus.put({"type": "DEPLOYED", "field": field_no, "msg": f"刺客开火!"})

    while not stop_event.is_set() and not state.is_victory:
        session.headers.update({"Cookie": GLOBAL_COOKIE})
        try:
            resp = session.get(BASE_URL, params=params, timeout=10)
            res_text = resp.text

            if "人数过多" in res_text:
                event_bus.put({"type": "WAF_BLOCK", "field": field_no, "msg": "被Nginx阻挡"})
            elif "已被" in res_text or "已满" in res_text or "被其他人" in res_text:
                event_bus.put({"type": "DEAD", "field": field_no, "msg": "已被抢跑，阵亡"})
                break 
            elif "未登录" in res_text or "重新登录" in res_text:
                event_bus.put({"type": "AUTH_FAILED", "field": field_no, "msg": "401未登录"})
                time.sleep(3)
            elif "成功" in res_text:
                event_bus.put({"type": "VICTORY", "field": field_no, "msg": "疑似成功！"})
                break
            else:
                event_bus.put({"type": "UNKNOWN", "field": field_no, "msg": "未知返回结果"})
                
        except requests.exceptions.ReadTimeout:
            event_bus.put({"type": "PENDING", "field": field_no, "msg": "超时阻塞(极可能排队成功)"})
        except Exception as e:
            event_bus.put({"type": "ERROR", "field": field_no, "msg": str(e)[:20]})
            
        # ⚠️ 动态油门：大模型随时会修改 config_dict["delay_ms"]
        current_delay = config_dict.get("delay_ms", 1000) / 1000.0
        time.sleep(current_delay)

def scout_worker():
    session = requests.Session()
    session.headers.update(HEADERS)
    FIRE_EVENT.wait() # 12:00 准时升空
    
    while not state.is_victory:
        session.headers.update({"Cookie": GLOBAL_COOKIE})
        try:
            resp = session.get(ORDER_LIST_URL, params={"PageNum": 1, "PageSize": 5, "Condition": ""}, timeout=5)
            if resp.status_code == 200:
                orders = resp.json().get("datatable", [])
                for o in orders:
                    field_name = o.get("Field", "")
                    left_time = int(o.get("LeftTime", 0))
                    if "健身房" in field_name and left_time > 0:
                        event_bus.put({"type": "VICTORY", "field": field_name, "msg": f"订单生成！剩 {left_time}s 付款"})
                        return
        except: pass
        time.sleep(2.5)

def emergency_cookie_refresh():
    global GLOBAL_COOKIE
    try:
        GLOBAL_COOKIE = auth_keeper.get_valid_cookie()
        state.auth_status = "VALID"
        print("✅ [后勤情报] Cookie 紧急补充完毕！")
    except Exception as e:
        state.auth_status = "INVALID"


# ============================================================================
# 📡 第五部分：雷达总线枢纽 (更新战况)
# ============================================================================
def radar_event_loop():
    while not state.is_victory:
        try:
            event = event_bus.get(timeout=1)
            e_type, field, msg = event.get("type"), event.get("field"), event.get("msg")
            
            if e_type == "VICTORY":
                state.is_victory = True
                print(f"\n🏆🏆🏆 战报：{msg} 🏆🏆🏆\n")
                continue

            if e_type == "AUTH_FAILED" and state.auth_status != "RECOVERING":
                state.auth_status = "RECOVERING"
                print("🚨 发现 Session 断开！紧急抢修...")
                threading.Thread(target=emergency_cookie_refresh, daemon=True).start()

            state.update_field_intel(field, e_type, msg)
            color = "🔴" if e_type in ["DEAD", "WAF_BLOCK", "AUTH_FAILED"] else "🟡" if e_type == "PENDING" else "🔵"
            print(f"{color} [{datetime.now().strftime('%H:%M:%S')}] {field} -> {e_type}: {msg}")
        except queue.Empty:
            pass


# ============================================================================
# 🤖 第六部分：LLM 大脑指挥系统 (规划、微操、复盘)
# ============================================================================
# 工具定义：统一的兵力微操工具
LLM_TOOLS = [{
    "type": "function",
    "function": {
        "name": "update_battle_plan",
        "description": "调整战术部署：分配目标场地、兵力(线程数)和攻击油门(delay_ms)。",
        "parameters": {
            "type": "object",
            "properties": {
                "deployments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field_no": {"type": "string", "description": "场地编号(如 JSP031)"},
                            "thread_count": {"type": "integer", "description": "分配兵力，0表示撤军"},
                            "delay_ms": {"type": "integer", "description": "请求间隔毫秒数(油门控制)"}
                        }
                    }
                },
                "reason": {"type": "string", "description": "战术调整理由"}
            },
            "required": ["deployments", "reason"]
        }
    }
}]

def execute_tactical_command(deployments, reason):
    """【微操执行官】动态增删线程，修改休眠间隔"""
    print(f"\n🧠 [指挥官决断] 理由：{reason}")
    with state.lock:
        total_requested = sum([d["thread_count"] for d in deployments])
        if total_requested > MAX_TOTAL_THREADS:
            print(f"⚠️ 警告：指挥官要求的兵力({total_requested})超过限制({MAX_TOTAL_THREADS})，系统强制拦截！")
            return

        for cmd in deployments:
            field = cmd["field_no"]
            target_threads = cmd["thread_count"]
            delay_ms = cmd["delay_ms"]
            
            # 初始化场地的配置字典
            if field not in state.troops_config:
                state.troops_config[field] = {"delay_ms": delay_ms, "threads": []}
            
            # 1. 动态调节油门
            state.troops_config[field]["delay_ms"] = delay_ms
            
            # 2. 动态调节兵力
            current_threads = state.troops_config[field]["threads"]
            active_events = [e for e in current_threads if not e.is_set()]
            current_count = len(active_events)
            
            if target_threads > current_count:
                # 增兵
                diff = target_threads - current_count
                print(f"    🚀 增兵指令: [{field}] 新增 {diff} 个刺客，油门 {delay_ms}ms")
                for _ in range(diff):
                    stop_evt = threading.Event()
                    t = threading.Thread(target=killer_worker, args=(field, stop_evt, state.troops_config[field]))
                    t.daemon = True
                    t.start()
                    state.troops_config[field]["threads"].append(stop_evt)
            elif target_threads < current_count:
                # 裁军
                diff = current_count - target_threads
                print(f"    🚫 撤退指令: [{field}] 撤回 {diff} 个刺客")
                for e in active_events[:diff]:
                    e.set()

def read_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return f.read()
    return "无历史作战记录。"

def ai_drafting_phase():
    """战前推演：大模型根据隔日记忆，制定初始排兵布阵"""
    memory = read_memory()
    print("\n🧠 [AI 战前推演室] 正在读取《历史战役日记》...")
    
    sys_prompt = f"""你是一位天才战术指挥官。任务：抢占校园健身房(场地编号:JSP001 到 JSP040)。
    【昨日历史日记】：
    {memory}
    
    【任务】：
    1. 结合历史经验、人类心理学（人们倾向抢首尾，中间可能没人抢），制定首批攻击目标。
    2. 总兵力必须小于 {MAX_TOTAL_THREADS}。
    3. 热门场地可以给 2-3 个线程并发，冷门场地 1 个线程捡漏。
    4. 初始请求间隔(delay_ms)建议设为 1000ms-2000ms 防止刚开局就被封。
    5. 调用 update_battle_plan 下发布署。
    """
    
    res = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "system", "content": sys_prompt}],
        tools=LLM_TOOLS,
        tool_choice={"type": "function", "function": {"name": "update_battle_plan"}}
    )
    msg = res.choices[0].message
    if msg.tool_calls:
        args = json.loads(msg.tool_calls[0].function.arguments)
        execute_tactical_command(args.get("deployments", []), args.get("reason", "首发阵型"))

def ai_mid_battle_loop():
    """战中微操：动态油门与换防"""
    sys_prompt = """你是战中微操指挥官。每4秒查看一次雷达。
    1. 若场地 DEAD(阵亡)，立刻设其兵力为 0，并将兵力转移至 JSP001-JSP040 中的冷门随机场地捡漏。
    2. 若场地 PENDING(超时排队)，【绝对不要撤军】，那是马上成功的征兆！
    3. 若连续 WAF_BLOCK，将该场地兵力减至1，且 delay_ms 调大至 3000ms，静默规避风控。
    4. 必须调用 update_battle_plan。"""

    while not state.is_victory:
        time.sleep(4)
        if state.auth_status != "VALID": continue
        
        try:
            res = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": state.get_summary()}
                ],
                tools=LLM_TOOLS,
                tool_choice="auto",
                timeout=12
            )
            msg = res.choices[0].message
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc.function.name == "update_battle_plan":
                        args = json.loads(tc.function.arguments)
                        execute_tactical_command(args.get("deployments", []), args.get("reason", ""))
        except Exception as e:
            print(f"⚠️ AI 参谋部通讯异常: {e}")

def ai_post_battle_summary():
    """赛后复盘：总结经验写入日记，实现隔日进化"""
    print("\n🧠 [AI 赛后复盘室] 战斗结束，正在分析战损，撰写战役日记...")
    report = state.get_summary()
    
    sys_prompt = """战斗已结束。你作为指挥官，需要结合战报总结一条 50 字以内的短经验。
    例如指出：哪个场地神仙打架、哪个场地容易捡漏、WAF风控严不严。
    你的回复将直接存入记忆文件，供明天的你读取。直接输出经验文字，不要任何多余废话。"""
    
    try:
        res = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": f"今日战果及雷达大盘：{report}"}
            ]
        )
        summary = res.choices[0].message.content.strip()
        print(f"📝 获得新进化经验：{summary}")
        
        with open(MEMORY_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {summary}\n")
    except Exception as e:
        print("写入记忆失败", e)


# ============================================================================
# 🏁 第七部分：主战场入口
# ============================================================================
def start_war():
    global GLOBAL_COOKIE
    print("\n" + "="*50)
    print("====== 🌟 AI 驱动体育馆抢票智能体系统 (V3 赛博进化版) 🌟 ======")
    
    # 1. 战前筹备
    if not os.path.exists(STATE_FILE):
        auth_keeper.first_login()
    GLOBAL_COOKIE = auth_keeper.get_valid_cookie()
    print("✅ 后勤保障就绪！")

    # 2. 雷达兵与侦察兵上线
    threading.Thread(target=radar_event_loop, daemon=True).start()
    threading.Thread(target=scout_worker, daemon=True).start()

    # 3. 战前推演 (大脑根据日记制定阵型)
    ai_drafting_phase()

    # 4. 精准打击准备
    print("\n⏳ 所有兵力潜伏就绪，等待 12:00:00 发令枪...")
    while True:
        now = datetime.now()
        if now.hour >= 12: # 测试时可改为 now.minute >= x
            break
        time.sleep(0.01)

    print(f"\n💥 【{datetime.now().strftime('%H:%M:%S.%f')[:-3]}】 门已开！全军出击！")
    FIRE_EVENT.set() # 释放信号枪，所有阻塞的刺客瞬间开火

    # 5. 移交微操指挥权
    ai_mid_battle_loop()

    # 6. 赛后复盘 (记录进本地知识库)
    ai_post_battle_summary()
    print("\n🎉🎉🎉 系统运行结束。干得漂亮，指挥官。 🎉🎉🎉\n")

if __name__ == "__main__":
    if "xxxxxxxxxxxx" in LLM_API_KEY:
        print("❌ 严重错误：请先在脚本顶部配置大模型的 LLM_API_KEY！")
    else:
        start_war()