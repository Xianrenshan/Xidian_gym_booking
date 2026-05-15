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

# --- 业务 URL 与常量 ---
LOGIN_URL = "https://tybsouthgym.xidian.edu.cn/"            
HOME_URL = "https://tybsouthgym.xidian.edu.cn/"             
BASE_URL = "https://tybsouthgym.xidian.edu.cn/Field/OrderField"
ORDER_LIST_URL = "https://tybsouthgym.xidian.edu.cn/Field/GetFieldOrder"
STATE_FILE = "auth_state.json"

DATE_ADD = 2
FIELD_TYPE_NO = "006"
PRICE = "2.00"
TARGET_TIME = {"begin": "14:00", "end": "17:00"}
START_FIELD = 31
END_FIELD = 27 

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
    "Accept": "*/*",
    "X-Requested-With": "XMLHttpRequest",
    "Connection": "keep-alive"
}

# --- AI 指挥官配置 ---
# 强烈推荐使用 DeepSeek-V3 或 通义千问（兼容OpenAI接口），又快又便宜
LLM_API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxx" 
LLM_BASE_URL = "https://api.deepseek.com/v1" # 根据你的大模型服务商修改
LLM_MODEL = "deepseek-chat"

client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

# 全局动态 Cookie，由后勤部队更新，前线部队随时读取
GLOBAL_COOKIE = ""


# ============================================================================
# 🛡️ 第二部分：后勤保障部队 (原版的 Playwright Cookie 提取器)
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
            print("请在打开的浏览器页面中用手机微信扫码并完成登录。")
            print("登录成功后（页面跳转到预约界面），回到这里按回车键继续...")
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
            logger.info("唤起无头浏览器静默获取新鲜 Cookie...")
            
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)

            # 关闭各类弹窗 (原封不动保留你的优秀逻辑)
            close_selectors = ['.layui-layer-close', '.dialog-close', '.close', '#close', 'button:has-text("关闭")', 'button:has-text("×")']
            for sel in close_selectors:
                try:
                    el = page.locator(sel).first
                    if el.count() > 0 and el.is_visible():
                        el.click(timeout=1000)
                except:
                    pass

            new_state = context.storage_state()
            self._state = new_state
            with open(self.state_file, "w") as f:
                json.dump(new_state, f)
            browser.close()

        cookies = self._state.get("cookies", [])
        return "; ".join([f"{c['name']}={c['value']}" for c in cookies])

auth_keeper = GymAuthKeeper()


# ============================================================================
# 🧠 第三部分：全局战术沙盘 (State) & 事件总线 (Event Bus)
# ============================================================================
class BattleState:
    def __init__(self):
        self.lock = threading.Lock()
        self.is_victory = False
        self.auth_status = "VALID" # VALID, INVALID, RECOVERING
        self.active_troops = {}    # {"JSP031": stop_event_object, ...}
        self.fields_status = {}    # 场地的战况记录

    def update_field(self, field_no, status, msg=""):
        with self.lock:
            if field_no not in self.fields_status:
                self.fields_status[field_no] = {"status": "IDLE", "msg": msg, "fails": 0}
            self.fields_status[field_no]["status"] = status
            self.fields_status[field_no]["msg"] = msg
            if status in ["DEAD", "WAF_BLOCK"]:
                self.fields_status[field_no]["fails"] += 1

    def get_summary(self):
        with self.lock:
            return json.dumps({
                "time": datetime.now().strftime('%H:%M:%S'),
                "auth_status": self.auth_status,
                "troops_count": len(self.active_troops),
                "fields_status": self.fields_status
            }, ensure_ascii=False)

state = BattleState()
event_bus = queue.Queue()


# ============================================================================
# ⚔️ 第四部分：前线肌肉部队 (只管发请求，不判断战术)
# ============================================================================
def killer_worker(field_no, stop_event):
    """【敢死队】携带动态 Cookie 发起冲击"""
    session = requests.Session()
    session.headers.update(HEADERS)
    
    item = {"FieldNo": field_no, "FieldTypeNo": FIELD_TYPE_NO, "BeginTime": TARGET_TIME["begin"], "Endtime": TARGET_TIME["end"], "Price": PRICE}
    params = {"checkdata": json.dumps([item], ensure_ascii=False), "dateadd": DATE_ADD, "VenueNo": "01"}

    event_bus.put({"type": "DEPLOYED", "field": field_no, "msg": "刺客已就位"})

    while not stop_event.is_set() and not state.is_victory:
        # 随时读取全局最新的 Cookie (防止中途刷新了 Cookie 导致这里还在用旧的)
        session.headers.update({"Cookie": GLOBAL_COOKIE})
        
        try:
            # Timeout设为10秒，防止长连接假死导致无法接收指挥官的撤退命令
            resp = session.get(BASE_URL, params=params, timeout=10)
            res_text = resp.text

            if "人数过多" in res_text:
                event_bus.put({"type": "WAF_BLOCK", "field": field_no, "msg": "被Nginx阻挡"})
                time.sleep(1.5)
            elif "已被" in res_text or "已满" in res_text or "被其他人" in res_text:
                event_bus.put({"type": "DEAD", "field": field_no, "msg": "已被抢跑，阵亡"})
                break 
            elif "未登录" in res_text or "重新登录" in res_text:
                event_bus.put({"type": "AUTH_FAILED", "field": field_no, "msg": "401未登录，Cookie已死"})
                time.sleep(3) # 遇到未登录，挂起等待后勤刷新
            elif "成功" in res_text:
                event_bus.put({"type": "VICTORY", "field": field_no, "msg": "疑似命中成功特征！"})
                break
            else:
                event_bus.put({"type": "UNKNOWN", "field": field_no, "msg": "未知返回结果"})
                time.sleep(1)
                
        except requests.exceptions.ReadTimeout:
            event_bus.put({"type": "PENDING", "field": field_no, "msg": "请求超时阻塞(极可能已排上队!)"})
        except Exception as e:
            event_bus.put({"type": "ERROR", "field": field_no, "msg": str(e)[:20]})
            time.sleep(1)


def scout_worker():
    """【高空侦察兵】巡逻订单系统"""
    session = requests.Session()
    session.headers.update(HEADERS)
    
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
                        event_bus.put({"type": "VICTORY", "field": field_name, "msg": f"订单已生成！剩 {left_time}s 付款"})
                        return
        except Exception:
            pass
        time.sleep(3)


def emergency_cookie_refresh():
    """【后勤急救】中途被踢出时，启动静默重登"""
    global GLOBAL_COOKIE
    try:
        GLOBAL_COOKIE = auth_keeper.get_valid_cookie()
        state.auth_status = "VALID"
        print("✅ [后勤情报] Cookie 紧急补充完毕！前线将自动换弹夹！")
    except Exception as e:
        print(f"❌ [后勤情报] 自动刷新失败: {e}")
        state.auth_status = "INVALID"


# ============================================================================
# 📡 第五部分：雷达总线枢纽 (更新沙盘)
# ============================================================================
def radar_event_loop():
    while not state.is_victory:
        try:
            event = event_bus.get(timeout=1)
            e_type, field, msg = event.get("type"), event.get("field"), event.get("msg")
            
            if e_type == "VICTORY":
                state.is_victory = True
                print(f"\n🏆🏆🏆 绝密喜报：{msg} 🏆🏆🏆\n")
                continue

            if e_type == "AUTH_FAILED":
                if state.auth_status != "RECOVERING":
                    state.auth_status = "RECOVERING"
                    print("🚨 [警报] 发现 Session 断开！正在呼叫后勤 Playwright 紧急抢修...")
                    threading.Thread(target=emergency_cookie_refresh, daemon=True).start()

            state.update_field(field, e_type, msg)
            color = "🔴" if e_type in ["DEAD", "WAF_BLOCK", "AUTH_FAILED"] else "🟡" if e_type == "PENDING" else "🔵"
            print(f"{color} [{datetime.now().strftime('%H:%M:%S')}] {field} -> {e_type}: {msg}")
            
        except queue.Empty:
            pass


# ============================================================================
# 🤖 第六部分：LLM 大脑决策层 (Function Calling)
# ============================================================================
LLM_TOOLS = [{
    "type": "function",
    "function": {
        "name": "issue_tactical_command",
        "description": "调度兵力：撤销无望的场地，进攻新的备用场地。",
        "parameters": {
            "type": "object",
            "properties": {
                "halt_fields": {"type": "array", "items": {"type": "string"}, "description": "要撤回兵力的场地编号"},
                "deploy_fields": {"type": "array", "items": {"type": "string"}, "description": "要新派兵力的场地编号"},
                "reason": {"type": "string", "description": "战术调整理由"}
            },
            "required": ["halt_fields", "deploy_fields", "reason"]
        }
    }
}]

def execute_tactical_command(halt_fields, deploy_fields, reason):
    print(f"\n🧠 [指挥官发令] 理由：{reason}")
    with state.lock:
        # 1. 撤销
        for f in halt_fields:
            if f in state.active_troops:
                print(f"    🚫 召回兵力: {f}")
                state.active_troops[f].set()
                del state.active_troops[f]
        # 2. 部署
        for f in deploy_fields:
            if f not in state.active_troops:
                print(f"    🚀 空投刺客: {f}")
                stop_evt = threading.Event()
                t = threading.Thread(target=killer_worker, args=(f, stop_evt))
                t.daemon = True
                t.start()
                state.active_troops[f] = stop_evt

def llm_commander_loop():
    print("🤖 [AI 参谋部] 上线！接管战场宏观微操。")
    system_prompt = """你是一个抢票战术指挥官。
规则：
1. 你的最大兵力并发数为 5，不要超出。
2. 遇到 'DEAD' (已死)的场地，必须加入 halt_fields 撤军。
3. 遇到 'PENDING' (超时阻塞)的场地，极大可能在锁数据库，绝对禁止撤军，让它继续挂着！
4. 遇到 'WAF_BLOCK' (Nginx限制)，如果持续太久，可以考虑撤换到新场地。
5. 必须使用 issue_tactical_command 工具下发具体编号（如 JSP025）。"""

    while not state.is_victory:
        time.sleep(4) # 大脑每4秒看一次大屏
        if state.auth_status != "VALID":
            print("🧠 [指挥官] 正在抢修后勤认证，本轮按兵不动...")
            continue
            
        report = state.get_summary()
        try:
            res = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"当前战报沙盘：\n{report}\n请下令！"}
                ],
                tools=LLM_TOOLS,
                tool_choice="auto",
                timeout=12
            )
            msg = res.choices[0].message
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc.function.name == "issue_tactical_command":
                        args = json.loads(tc.function.arguments)
                        execute_tactical_command(args.get("halt_fields", []), args.get("deploy_fields", []), args.get("reason", ""))
        except Exception as e:
            print(f"⚠️ [通讯干扰] 无法联系 LLM 指挥部: {e}")


# ============================================================================
# 🏁 第七部分：时间控制与主战场入口
# ============================================================================
def start_war():
    global GLOBAL_COOKIE
    print("\n" + "="*50)
    print("====== 🌟 AI 驱动体育馆抢票智能体系统 (V2完整版) 🌟 ======")
    
    # [1] 战前准备：获取第一口新鲜 Cookie
    print("\n[第一阶段：后勤筹备]")
    if not os.path.exists(STATE_FILE):
        auth_keeper.first_login()
    GLOBAL_COOKIE = auth_keeper.get_valid_cookie()
    print("✅ 后勤保障就绪，获取合法通行证完成！")

    # [2] 部署雷达与侦察兵
    threading.Thread(target=radar_event_loop, daemon=True).start()
    threading.Thread(target=scout_worker, daemon=True).start()

    # [3] 精准时间打击
    print("\n[第二阶段：战术静默等待]")
    print("⏳ 正在等待 12:00:00 开闸...")
    while True:
        now = datetime.now()
        if now.hour >= 12:
            break
        time.sleep(0.01)

    print(f"\n💥 【{datetime.now().strftime('%H:%M:%S')}】 门已开！全军出击！")
    
    # 生成初始攻击名单 (按照你的逻辑 JSP031 -> JSP027)
    initial_targets = [f"JSP{i:03d}" for i in range(START_FIELD, END_FIELD - 1, -1)]
    execute_tactical_command(halt_fields=[], deploy_fields=initial_targets, reason="首波冲锋：对最热门场地展开饱和式攻击")

    # [4] 移交最高指挥权给 LLM
    llm_commander_loop()

if __name__ == "__main__":
    if "xxxxxxxxxxxx" in LLM_API_KEY:
        print("❌ 严重错误：请先在脚本顶部配置大模型的 LLM_API_KEY！")
    else:
        start_war()