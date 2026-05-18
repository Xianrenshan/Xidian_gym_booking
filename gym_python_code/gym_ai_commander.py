import json
import os
import time
import threading
import base64
import logging
from datetime import datetime
from typing import Optional, List, Dict

from playwright.sync_api import sync_playwright
import requests
from colorama import init, Fore, Style

# 初始化终端颜色
init(autoreset=True)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ===================== [军规配置] 物理常数与战区 =====================
LOGIN_URL = "https://tybsouthgym.xidian.edu.cn/"
HOME_URL = "https://tybsouthgym.xidian.edu.cn/"
BASE_URL = "https://tybsouthgym.xidian.edu.cn/Field/OrderField"
ORDER_LIST_URL = "https://tybsouthgym.xidian.edu.cn/Field/GetFieldOrder"
STATE_FILE = "auth_state.json"
MEMORY_FILE = "battle_memory.txt"

DATE_ADD = 2
PRICE = "2.00"
TARGET_TIME = {"begin": "14:00", "end": "17:00"}
ALL_FIELDS = [f"JSP{i:03d}" for i in range(1, 41)]

# --- 物理风控 ---
SNIPER_COOLDOWN = 3.1   # 单发物理强制冷却 (秒)
BATCH_SIZE = 5          # 作战批次大小
SCOUT_INTERVAL = 3.0    # 侦察机刷新频率
PENDING_WAIT_THRESHOLD = 3  # 排队场地超过3个自动等待
AUTO_WAIT_SECONDS = 2.0     # 自动等待时间

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
    "Accept": "*/*",
    "X-Requested-With": "XMLHttpRequest",
    "Connection": "keep-alive"
}

# 全局信号
GLOBAL_COOKIE = ""
VICTORY_FLAG = threading.Event()
FIRE_EVENT = threading.Event()

# ===================== [后勤] 完整版 Cookie 保活机制（无阉割！） =====================
class GymAuthKeeper:
    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self._state = None

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
            print("登录成功后（页面跳转到预约界面或显示正常内容），回到这里按回车键继续...")
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

            logger.info("正在访问首页更新令牌...")
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)

            # 完整版弹窗关闭逻辑（无阉割）
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
            try:
                page.get_by_text("场地预订", exact=True).first.click(timeout=5000)
            except Exception:
                page.goto("https://tybsouthgym.xidian.edu.cn/Field/OrderField", wait_until="domcontentloaded")

            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(3000)

            # 完整版校内用户选择处理（无阉割）
            page_title = page.title()
            if "用户类型选择" in page_title or "用户类型" in page_title:
                page.wait_for_timeout(2000)
                for sel in close_selectors:
                    try:
                        el = page.locator(sel).first
                        if el.count() > 0 and el.is_visible():
                            el.click(timeout=2000)
                            page.wait_for_timeout(1000)
                    except Exception:
                        pass
                try:
                    page.get_by_text("校内用户", exact=True).first.click(timeout=3000)
                except Exception:
                    try:
                        page.get_by_text("校内", exact=False).first.click(timeout=3000)
                    except Exception:
                        page.click('input[value*="校内"]', timeout=3000)

                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(3000)

            new_state = context.storage_state()
            self._state = new_state
            with open(self.state_file, "w") as f:
                json.dump(new_state, f)
            browser.close()

        cookies = self._state.get("cookies", [])
        return "; ".join([f"{c['name']}={c['value']}" for c in cookies])

# ===================== [沙盘] 实时全景地图（完整保留） =====================
class BattlefieldState:
    def __init__(self):
        self.lock = threading.Lock()
        self.map = {f: {"status": "UNKNOWN", "last_update": ""} for f in ALL_FIELDS}
        self.history = []

    def update(self, target: str, status: str):
        with self.lock:
            ts = datetime.now().strftime("%H:%M:%S")
            self.map[target] = {"status": status, "last_update": ts}
            self.history.insert(0, f"[{ts}] {target} -> {status}")
            if len(self.history) > 15:
                self.history.pop()

    def get_pending_count(self):
        with self.lock:
            return sum(1 for v in self.map.values() if v["status"] == "PENDING")

    def get_available_targets(self) -> List[str]:
        # 规则核心：只返回 未死、未排队 的场地，优先热门区
        with self.lock:
            available = []
            # 热门区：27-40
            hot_zone = [f"JSP{i:03d}" for i in range(27, 41)]
            # 中区：15-26
            mid_zone = [f"JSP{i:03d}" for i in range(15, 27)]

            for field in hot_zone + mid_zone:
                if self.map[field]["status"] not in ["PENDING", "DEAD"]:
                    available.append(field)
            return available

sandbox = BattlefieldState()

# ===================== [纯规则指挥部] 替代AI，本地硬规则决策 =====================
def plan_next_batch(batch_size: int = BATCH_SIZE) -> dict:
    """纯规则生成下一批次，完全替代大模型"""
    available = sandbox.get_available_targets()
    pending_count = sandbox.get_pending_count()

    # 规则1：排队过多 → 等待
    if pending_count >= PENDING_WAIT_THRESHOLD:
        return {
            "strategy": "WAIT",
            "next_batch": available[:batch_size],
            "pre_wait": AUTO_WAIT_SECONDS,
            "reason": f"排队场地{pending_count}个，自动等待{AUTO_WAIT_SECONDS}秒"
        }

    # 规则2：正常进攻 → 取可用场地
    strategy = "HOT_ZONE_ATTACK" if available else "FALLBACK"
    pre_wait = 0.0
    next_batch = available[:batch_size]

    # 兜底：无可用场地时使用默认列表
    if not next_batch:
        next_batch = [f"JSP{i:03d}" for i in range(30, 35)]

    return {
        "strategy": strategy,
        "next_batch": next_batch,
        "pre_wait": pre_wait,
        "reason": "优先攻击热门区可用场地"
    }

def write_post_battle_memory():
    """纯规则战后记忆，替代AI总结"""
    print(Fore.CYAN + "\n🧠 [参谋部] 战斗结束，生成作战记录...")
    try:
        dead_num = sum(1 for v in sandbox.map.values() if v["status"] == "DEAD")
        pending_num = sum(1 for v in sandbox.map.values() if v["status"] == "PENDING")
        summary = f"热门区场地{dead_num}个已抢空，排队场地{pending_num}个，下次优先攻击中区"
        with open(MEMORY_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d')}] {summary}\n")
        print(Fore.MAGENTA + f"📜 记忆已保存: {summary}")
    except:
        pass

# ===================== [战地执行] 幽灵刺客与侦察机（完整保留） =====================
def sniper_mission(session, target, result_box):
    """异步幽灵刺客：发射后不管，后台死等300秒（核心功能）"""
    item = {"FieldNo": target, "FieldTypeNo": "006", "BeginTime": TARGET_TIME["begin"], "Endtime": TARGET_TIME["end"], "Price": PRICE}
    params = {"checkdata": json.dumps([item], ensure_ascii=False), "dateadd": DATE_ADD, "VenueNo": "01"}
    try:
        resp = session.get(BASE_URL, params=params, timeout=300)
        res_text = resp.text
        if "成功" in res_text or "待支付" in res_text:
            result_box["status"] = "SUCCESS"
        elif "满" in res_text or "已被" in res_text or "其他人" in res_text:
            result_box["status"] = "DEAD"
        else:
            result_box["status"] = "WAF"
    except:
        result_box["status"] = "TIMEOUT_STILL_WAITING"

    sandbox.update(target, result_box["status"])
    if result_box["status"] == "SUCCESS":
        VICTORY_FLAG.set()

def scout_drone_worker():
    """侦察机：独立轮询订单列表（完整保留）"""
    session = requests.Session()
    session.headers.update(HEADERS)
    FIRE_EVENT.wait()
    while not VICTORY_FLAG.is_set():
        session.headers.update({"Cookie": GLOBAL_COOKIE})
        try:
            resp = session.get(ORDER_LIST_URL, params={"PageNum": 1, "PageSize": 5, "Condition": ""}, timeout=5)
            orders = resp.json().get("datatable", [])
            for o in orders:
                if "健身房" in o.get("Field", "") and int(o.get("LeftTime", 0)) > 0:
                    print(Fore.GREEN + f"\n🏆🏆🏆 [捷报] 侦察机确认成功：【{o['Field']}】 🏆🏆🏆\n")
                    VICTORY_FLAG.set()
                    return
        except:
            pass
        time.sleep(SCOUT_INTERVAL)

# ===================== [引擎] 批次循环战斗（完整保留） =====================
def battle_engine():
    session = requests.Session()
    session.headers.update(HEADERS)

    FIRE_EVENT.wait()
    current_plan = plan_next_batch()

    while not VICTORY_FLAG.is_set():
        # 1. 战术休整
        if current_plan.get("pre_wait", 0) > 0:
            print(Fore.YELLOW + f"🧠 [休整] {current_plan['reason']}，等待{current_plan['pre_wait']}秒...")
            time.sleep(current_plan["pre_wait"])

        # 2. 执行批次打击
        print(Fore.CYAN + f"\n🚀 [批次启动] 策略: {current_plan['strategy']} | 目标: {current_plan['next_batch']}")

        for target in current_plan["next_batch"]:
            if VICTORY_FLAG.is_set():
                break

            # 护栏：不打已排队/已死亡场地
            if sandbox.map[target]["status"] in ["PENDING", "DEAD"]:
                continue

            shot_start = time.perf_counter()
            result_box = {"status": "PENDING"}
            sandbox.update(target, "PENDING")

            # 发射刺客
            session.headers.update({"Cookie": GLOBAL_COOKIE})
            t = threading.Thread(target=sniper_mission, args=(session, target, result_box))
            t.daemon = True
            t.start()

            # 严格3.1秒冷却
            t.join(SNIPER_COOLDOWN)

            # 战报
            color = Fore.RED if result_box["status"] == "DEAD" else Fore.YELLOW
            print(f"  🎯 打击 {target} -> 状态: {color}{result_box['status']}")

            # 物理冷却兜底
            elapsed = time.perf_counter() - shot_start
            if elapsed < SNIPER_COOLDOWN:
                time.sleep(SNIPER_COOLDOWN - elapsed)

        if VICTORY_FLAG.is_set():
            break

        # 3. 规则生成下一批次
        print(Fore.MAGENTA + "📡 [复盘] 批次结束，生成下一轮指令...")
        current_plan = plan_next_batch()
        print(Fore.MAGENTA + f"🧠 [规则决策] {current_plan['reason']}")

# ===================== [入口] =====================
def start():
    global GLOBAL_COOKIE
    print(Fore.WHITE + "="*60)
    print(Fore.CYAN + "   🔫 西电体育馆纯规则抢票 · 异步幽灵刺客战阵")
    print(Fore.WHITE + "="*60)

    # 完整版登录
    logistics = GymAuthKeeper()
    if not os.path.exists(STATE_FILE):
        logistics.first_login()
    GLOBAL_COOKIE = logistics.get_valid_cookie()
    print(Fore.GREEN + "✅ [后勤] Cookie 已完成刷新，登录状态有效")

    # 启动侦察机
    threading.Thread(target=scout_drone_worker, daemon=True).start()

    # 等待12点
    print(Fore.YELLOW + "⏳ 等待 12:00:00 开闸...")
    while datetime.now().hour < 12:
        time.sleep(0.01)

    print(Fore.RED + f"\n💥💥💥 【{datetime.now().strftime('%H:%M:%S')}】 开战！💥💥💥\n")
    FIRE_EVENT.set()

    try:
        battle_engine()
    except KeyboardInterrupt:
        print(Fore.YELLOW + "\n⚠️ 手动终止程序")

    if VICTORY_FLAG.is_set():
        print(Fore.GREEN + "🏆 抢票成功！请在30分钟内微信支付！")

    # 战后记录
    write_post_battle_memory()

if __name__ == "__main__":
    start()