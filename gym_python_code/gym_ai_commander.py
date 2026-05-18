import json
import os
import time
import threading
import base64
import logging
from datetime import datetime
from typing import Optional, Dict

from playwright.sync_api import sync_playwright
import requests
from openai import OpenAI
from colorama import init, Fore, Style

# 初始化终端颜色
init(autoreset=True)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============================================================================
# ⚙️ [军规与配置] 物理法则与常数
# ============================================================================
# --- 业务常量 ---
LOGIN_URL = "https://tybsouthgym.xidian.edu.cn/"            
HOME_URL = "https://tybsouthgym.xidian.edu.cn/"             
BASE_URL = "https://tybsouthgym.xidian.edu.cn/Field/OrderField"
ORDER_LIST_URL = "https://tybsouthgym.xidian.edu.cn/Field/GetFieldOrder"
STATE_FILE = "auth_state.json"
MEMORY_FILE = "battle_memory.txt"
TEST_API_URL = "https://tybsouthgym.xidian.edu.cn/Field/GetFieldOrder?PageNum=1&PageSize=1"

DATE_ADD = 2
PRICE = "2.00"
TARGET_TIME = {"begin": "14:00", "end": "17:00"}
ALL_FIELDS = [f"JSP{i:03d}" for i in range(1, 41)] # 全量目标场地 JSP001-JSP040

# --- 物理风控法则 ---
SNIPER_COOLDOWN = 3.1  # 主炮绝对冷却时间 (秒)
SCOUT_INTERVAL = 1.0   # 侦察无人机轮询时间 (秒)
JWT_EXPIRE_BUFFER = 3 * 60  # JWT过期缓冲时间

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
    "Accept": "*/*",
    "X-Requested-With": "XMLHttpRequest",
    "Connection": "keep-alive"
}

# --- AI 将军配置 ---
LLM_API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxx" # 【填入你的API KEY】
LLM_BASE_URL = "https://api.deepseek.com/v1" 
LLM_MODEL = "deepseek-chat"
client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

# 全局信号与共享弹药
GLOBAL_COOKIE = ""
VICTORY_FLAG = threading.Event() 
FIRE_EVENT = threading.Event() 

# ============================================================================
# 🛡️ [后勤部队] 完整版 Cookie 保活与防封机制 (你提供的稳定版)
# ============================================================================
class GymAuthKeeper:
    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self._state = None

    # ---------- 首次登录 ----------
    def first_login(self) -> None:
        logger.info("正在启动浏览器进行首次登录...")
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=HEADERS["User-Agent"]
            )
            page = context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded")

            print("\n" + "=" * 50)
            print("请在打开的浏览器页面中用手机微信扫码并完成登录。")
            print("登录成功后（页面跳转到预约界面或显示正常内容），")
            print("回到这里按回车键继续...")
            print("=" * 50 + "\n")
            input(">> 按回车确认登录完成 <<")

            context.storage_state(path=self.state_file)
            browser.close()

        logger.info(f"长期状态已保存到 {self.state_file}")
        self._load_state()

    # ---------- 加载状态 ----------
    def _load_state(self) -> Optional[dict]:
        if os.path.exists(self.state_file):
            with open(self.state_file, "r") as f:
                self._state = json.load(f)
            logger.debug("已从文件加载认证状态。")
            return self._state
        else:
            logger.warning("状态文件不存在，需要先执行首次登录。")
            return None

    # ---------- 解码JWT获取过期时间 ----------
    @staticmethod
    def _decode_jwt_exp(token: str) -> float:
        try:
            payload = token.split(".")[1]
            payload += "=" * (4 - len(payload) % 4)
            decoded = base64.urlsafe_b64decode(payload)
            data = json.loads(decoded)
            return data.get("exp", 0)
        except Exception:
            return 0

    def get_valid_cookie(self) -> str:
        if self._state is None:
            self._load_state()
        if self._state is None:
            raise RuntimeError("没有可用的认证状态，请先执行 first_login()。")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                storage_state=self._state,
                user_agent=HEADERS["User-Agent"]
            )
            page = context.new_page()

            logger.info("正在访问首页...")
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)

            # 关闭所有弹窗
            close_selectors = [
                'button:has-text("关闭")', 'button:has-text("确定")',
                'a:has-text("关闭")', 'span:has-text("关闭")',
                '.layui-layer-close', '.dialog-close', '.close', '#close',
                'button:has-text("×")', 'text=✕'
            ]
            for _ in range(3):
                closed = False
                for sel in close_selectors:
                    try:
                        el = page.locator(sel).first
                        if el.count() > 0 and el.is_visible():
                            el.click(timeout=2000)
                            closed = True
                            page.wait_for_timeout(1500)
                            break
                    except Exception:
                        continue
                if not closed:
                    break

            # 点击场地预订
            logger.info("点击‘场地预订’...")
            try:
                page.get_by_text("场地预订", exact=True).first.click(timeout=5000)
            except Exception:
                page.goto("https://tybsouthgym.xidian.edu.cn/Field/OrderField", wait_until="domcontentloaded")

            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(3000)

            # 处理用户类型选择页
            page_title = page.title()
            if "用户类型选择" in page_title or "用户类型" in page_title:
                logger.info("检测到用户类型选择页面，正在处理...")
                page.wait_for_timeout(2000)
                for sel in close_selectors:
                    try:
                        el = page.locator(sel).first
                        if el.count() > 0 and el.is_visible():
                            el.click(timeout=2000)
                            page.wait_for_timeout(1500)
                    except Exception:
                        pass

                page.wait_for_timeout(1000)
                try:
                    page.get_by_text("校内用户", exact=True).first.click(timeout=3000)
                    logger.info("已点击‘校内用户’按钮")
                except Exception:
                    try:
                        page.get_by_text("校内", exact=False).first.click(timeout=3000)
                        logger.info("已点击包含‘校内’的按钮")
                    except Exception:
                        page.click('input[value*="校内"]', timeout=3000)
                        logger.info("通过 input 点击了校内选项")
                
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(3000)

            page.wait_for_timeout(3000)
            new_state = context.storage_state()
            self._state = new_state
            with open(self.state_file, "w") as f:
                json.dump(new_state, f)
            browser.close()

        # 提取Cookie并验证JWT
        cookies = self._state.get("cookies", [])
        cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])

        jwt_token = None
        for c in cookies:
            if c["name"] == "JWTUserToken":
                jwt_token = c["value"]
                break

        if jwt_token:
            exp_time = self._decode_jwt_exp(jwt_token)
            if exp_time == 0:
                logger.warning("无法解析 JWT 过期时间")
            else:
                remaining = exp_time - time.time()
                logger.info(f"JWT 有效，剩余 {remaining:.0f} 秒")
        else:
            logger.warning("未找到 JWTUserToken，登录可能仍失效")

        return cookie_str

# ============================================================================
# 🗺️ [战争沙盘] Battlefield State (世界模型)
# ============================================================================
class BattlefieldState:
    def __init__(self):
        self.lock = threading.Lock()
        self.shot_history = []
        self.field_state = {f: {"status": "UNKNOWN", "heat": 0.0, "dead_count": 0} for f in ALL_FIELDS}
        self.waf_strikes = 0
        
    def update_shot_result(self, target: str, semantic_result: str):
        with self.lock:
            self.shot_history.insert(0, {"time": datetime.now().strftime("%H:%M:%S"), "target": target, "result": semantic_result})
            if len(self.shot_history) > 10:
                self.shot_history.pop()
                
            self.field_state[target]["status"] = semantic_result
            if semantic_result == "DEAD":
                self.field_state[target]["dead_count"] += 1
            elif semantic_result == "WAF":
                self.waf_strikes += 1

    def get_sandbox_json(self):
        with self.lock:
            active_fields = {k: v for k, v in self.field_state.items() if v["status"] != "UNKNOWN"}
            return json.dumps({
                "waf_level": self.waf_strikes,
                "recent_shots": self.shot_history[:5],
                "known_fields": active_fields
            }, ensure_ascii=False)

sandbox = BattlefieldState()

# ============================================================================
# 🧠 [AI 参谋部] OODA 推演与战术下达
# ============================================================================
class AICommander:
    def __init__(self):
        self.memory = self._load_memory()
        
    def _load_memory(self):
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return f.read()[-1000:]
        return "无先验记忆。常识：30-40为红海热门，15-25为中区盲区。"

    def draft_opening_target(self) -> str:
        sys_prompt = f"""你是战场指挥官。你要决定12:00:00开闸后的第一枪打哪个场地。
        可选范围: JSP001 到 JSP040。
        【历史记忆】：{self.memory}
        请直接输出一个你认为最有战略意义的场地编号（如 JSP031 或 JSP018）。只输出编号，不要废话。"""
        
        try:
            res = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "system", "content": sys_prompt}]
            )
            target = res.choices[0].message.content.strip().upper()
            if target in ALL_FIELDS:
                return target
        except: pass
        return "JSP031"

    def decide_next_target(self, current_target: str, html_text: str) -> dict:
        semantic_result = "UNKNOWN"
        if "已满" in html_text or "已被" in html_text or "其他人" in html_text:
            semantic_result = "DEAD"
        elif "成功" in html_text or "待支付" in html_text:
            semantic_result = "SUCCESS"
        elif "频繁" in html_text or "人数过多" in html_text:
            semantic_result = "WAF"
        elif "timeout" in html_text.lower():
            semantic_result = "TIMEOUT"

        sandbox.update_shot_result(current_target, semantic_result)
        
        color = Fore.RED if semantic_result in ["DEAD", "WAF"] else Fore.YELLOW if semantic_result == "TIMEOUT" else Fore.GREEN
        print(f"🎯 [狙击手报告] 目标 {current_target} -> {color}{semantic_result}{Style.RESET_ALL}")

        if semantic_result == "SUCCESS":
            return {"target": current_target, "strategy": "VICTORY", "reason": "疑似命中"}

        state_json = sandbox.get_sandbox_json()
        sys_prompt = f"""你是抢票赛博将军。每次只能打一枪，冷却 3 秒。
        当前世界沙盘状态：{state_json}
        【将军权限与战略模式】：
        1. HOT_ZONE_BLITZ (死磕头尾热门区 30-40, 1-5)
        2. MID_ZONE_SWEEP (转战中区盲区 15-25)
        3. STICKY_ATTACK (发现 TIMEOUT 时，你有权下令继续赌同一个场地)
        
        【强制约束】：
        1. 返回 JSON 格式。包含 "strategy", "next_target" (必须是 JSP001-JSP040), "reason"。
        2. 极力避免安排打状态已是 DEAD 的场地。
        """

        try:
            res = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "system", "content": sys_prompt}],
                response_format={"type": "json_object"},
                timeout=2.0
            )
            decision = json.loads(res.choices[0].message.content)
            return decision
        except Exception as e:
            idx = ALL_FIELDS.index(current_target)
            safe_target = ALL_FIELDS[max(0, idx - 1)]
            return {"next_target": safe_target, "strategy": "DEGRADED_FALLBACK", "reason": "参谋部通讯中断，机械后撤"}

    def write_post_battle_memory(self):
        print(Fore.CYAN + "\n🧠 [参谋部] 战斗结束。正在撰写《战役回忆录》...")
        sys_prompt = f"""分析以下今日战局沙盘，输出一句话的核心经验，指导明天的首发策略。
        {sandbox.get_sandbox_json()}"""
        try:
            res = client.chat.completions.create(model=LLM_MODEL, messages=[{"role": "system", "content": sys_prompt}])
            summary = res.choices[0].message.content.strip()
            with open(MEMORY_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d')}] {summary}\n")
            print(Fore.MAGENTA + f"📜 记忆已烙印: {summary}")
        except: pass

# ============================================================================
# 🚁 [侦察无人机] 订单嗅探器
# ============================================================================
def scout_drone_worker():
    session = requests.Session()
    session.headers.update(HEADERS)
    FIRE_EVENT.wait()
    print(Fore.CYAN + "🚁 [侦察机] 已升空，开始高频扫描战损订单...")
    
    while not VICTORY_FLAG.is_set():
        session.headers.update({"Cookie": GLOBAL_COOKIE})
        try:
            resp = session.get(ORDER_LIST_URL, params={"PageNum": 1, "PageSize": 5, "Condition": ""}, timeout=4)
            if resp.status_code == 200:
                orders = resp.json().get("datatable", [])
                for o in orders:
                    if "健身房" in o.get("Field", "") and int(o.get("LeftTime", 0)) > 0:
                        print(Fore.GREEN + f"\n🏆🏆🏆 [最高捷报] 截获待支付订单: 【{o['Field']}】 剩余时间: {o['LeftTime']}s 🏆🏆🏆\n")
                        VICTORY_FLAG.set()
                        return
        except: pass
        time.sleep(SCOUT_INTERVAL)

# ============================================================================
# ⚔️ [战地引擎] 严格 3 秒回合作战
# ============================================================================
def tactical_guardrail(ai_target: str, current_target: str) -> str:
    if ai_target not in ALL_FIELDS:
        return current_target
    if sandbox.field_state.get(ai_target, {}).get("dead_count", 0) >= 2:
        print(Fore.YELLOW + "⚠️ [护栏拦截] AI 指令疯狂。目标已被鞭尸多次，强制跳过。")
        idx = ALL_FIELDS.index(ai_target)
        return ALL_FIELDS[max(0, idx - 1)]
    return ai_target

def battle_engine_loop(ai: AICommander, initial_target: str):
    session = requests.Session()
    session.headers.update(HEADERS)
    
    current_target = initial_target
    last_shot_time = 0.0

    FIRE_EVENT.wait()
    
    while not VICTORY_FLAG.is_set():
        now = time.perf_counter()
        elapsed = now - last_shot_time
        if elapsed < SNIPER_COOLDOWN:
            time.sleep(SNIPER_COOLDOWN - elapsed)
        
        session.headers.update({"Cookie": GLOBAL_COOKIE})
        item = {"FieldNo": current_target, "FieldTypeNo": "006", "BeginTime": TARGET_TIME["begin"], "Endtime": TARGET_TIME["end"], "Price": PRICE}
        params = {"checkdata": json.dumps([item], ensure_ascii=False), "dateadd": DATE_ADD, "VenueNo": "01"}
        
        html_text = ""
        try:
            resp = session.get(BASE_URL, params=params, timeout=10)
            html_text = resp.text
        except requests.exceptions.ReadTimeout:
            html_text = "timeout"
        except Exception as e:
            html_text = "waf_or_error"
            
        last_shot_time = time.perf_counter()

        decision = ai.decide_next_target(current_target, html_text)
        
        if VICTORY_FLAG.is_set(): 
            break
            
        ai_next = decision.get("next_target", current_target)
        strategy = decision.get("strategy", "UNKNOWN")
        reason = decision.get("reason", "无可奉告")

        print(Fore.MAGENTA + f"🧠 [指挥官] 策略: {strategy} | 下一枪: {ai_next} | 逻辑: {reason}")
        
        current_target = tactical_guardrail(ai_next, current_target)

# ============================================================================
# 🏁 统帅部入口
# ============================================================================
def start_war():
    global GLOBAL_COOKIE
    print(Fore.WHITE + "="*60)
    print(Fore.CYAN + "   🤖 OODA Turn-Based AI Cyber Commander (西电稳定版)")
    print(Fore.WHITE + "="*60)

    # 1. 完整版后勤登录
    logistics = GymAuthKeeper()
    if not os.path.exists(STATE_FILE):
        logistics.first_login()
    GLOBAL_COOKIE = logistics.get_valid_cookie()
    print(Fore.GREEN + "✅ [后勤] 粮草充足，合法 Cookie 已装配。")

    # 2. 唤醒将军
    ai = AICommander()
    initial_target = ai.draft_opening_target()
    print(Fore.MAGENTA + f"🧠 [战前推演] 将军指示首发目标瞄准：{initial_target}")

    # 3. 部署侦察机
    threading.Thread(target=scout_drone_worker, daemon=True).start()

    # 4. 等待12点
    print(Fore.YELLOW + "⏳ 主炮上膛，静息伪装，等待 12:00:00 时钟溢出...")
    while True:
        if datetime.now().hour >= 12:
            break
        time.sleep(0.01)

    print(Fore.RED + f"\n💥💥💥 【{datetime.now().strftime('%H:%M:%S.%f')[:-3]}】 闸门大开！战争开始！ 💥💥💥\n")
    FIRE_EVENT.set()

    # 5. 开始抢票
    try:
        battle_engine_loop(ai, initial_target)
    except KeyboardInterrupt:
        print(Fore.YELLOW + "\n⚠️ 人类强制终止了战争。")

    # 6. 战后记忆
    if VICTORY_FLAG.is_set():
        print(Fore.GREEN + "🏆 战争以人类的胜利告终，请速归微信支付。")
    ai.write_post_battle_memory()

if __name__ == "__main__":
    if "xxxxxxxxxxxx" in LLM_API_KEY:
        print(Fore.RED + "❌ 致命错误：请填写大模型 API_KEY！")
    else:
        start_war()