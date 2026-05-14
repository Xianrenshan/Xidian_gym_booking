import json
import os
import time
import base64
import logging
import threading
from typing import Optional
from datetime import datetime

from playwright.sync_api import sync_playwright
import requests

# ===================== 第一部分：全局配置 =====================
LOGIN_URL = "https://tybsouthgym.xidian.edu.cn/"            
HOME_URL = "https://tybsouthgym.xidian.edu.cn/"             
STATE_FILE = "auth_state.json"                              

BASE_URL = "https://tybsouthgym.xidian.edu.cn/Field/OrderField"
ORDER_LIST_URL = "https://tybsouthgym.xidian.edu.cn/Field/GetFieldOrder"

# 业务参数
DATE_ADD = 2
FIELD_TYPE_NO = "006"
PRICE = "2.00"
TARGET_TIME = {"begin": "14:00", "end": "17:00"}

# =========================================================================
# ⚠️ 犀利策略：不要扫荡全场，那会触发 WAF 封禁！
# 集中火力，并发 5 个最高优先级的场地（比如 31, 30, 29, 28, 27）
# =========================================================================
START_FIELD = 31
END_FIELD = 27 

# 全局 Header，必须保持 Keep-Alive
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.2 Safari/605.1.15",
    "Accept": "*/*",
    "X-Requested-With": "XMLHttpRequest",
    "Connection": "keep-alive"  # 核心：复用 TCP 隧道
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 多线程通信标志位
SUCCESS_FLAG = False
ORDER_INFO = ""


# ===================== 第二部分：核心类 - 自动认证与 Cookie 提取 =====================
class GymAuthKeeper:
    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self._state = None

    def first_login(self) -> None:
        """首次弹出真实浏览器窗口，等待扫码并保存长期状态"""
        logger.info("正在启动浏览器进行首次登录...")
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

        logger.info(f"长期状态已保存到 {self.state_file}")
        self._load_state()

    def _load_state(self) -> Optional[dict]:
        if os.path.exists(self.state_file):
            with open(self.state_file, "r") as f:
                self._state = json.load(f)
            return self._state
        return None

    def get_valid_cookie(self) -> str:
        """无头浏览器静默刷新页面获取最新 Cookie"""
        if self._state is None:
            self._load_state()
        if self._state is None:
            raise RuntimeError("没有可用的认证状态，请先执行首次登录。")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            context = browser.new_context(storage_state=self._state, user_agent=HEADERS["User-Agent"])
            page = context.new_page()

            logger.info("正在唤起无头浏览器获取新鲜 Cookie...")
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)

            # 关闭各类可能的弹窗
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

            try:
                page.get_by_text("场地预订", exact=True).first.click(timeout=5000)
            except Exception:
                page.goto("https://tybsouthgym.xidian.edu.cn/Field/OrderField", wait_until="domcontentloaded")

            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(3000)

            page_title = page.title()
            if "用户类型选择" in page_title or "用户类型" in page_title:
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


# ===================== 第三部分：破局侦察与刺客逻辑 =====================

def check_order_scout(session):
    """
    【侦察兵线程】
    核心使命：无视具体的场地编号，全局扫描。
    只要名下出现任何“健身房”且属于“待支付”状态的订单，立刻判定成功！
    完美破解 029/29 字符串不匹配和假抢跑问题。
    """
    global SUCCESS_FLAG, ORDER_INFO
    params = {"PageNum": 1, "PageSize": 15, "Condition": ""}
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🚁 侦察兵已升空，开始无差别扫描待支付订单...")
    
    while not SUCCESS_FLAG:
        try:
            resp = session.get(ORDER_LIST_URL, params=params, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                orders = data.get("datatable", [])
                
                for o in orders:
                    field_name = o.get("Field", "")
                    left_time = int(o.get("LeftTime", 0))
                    
                    # 只要名字带“健身房”且还有倒计时需要付款，就是刚抢到的战利品！
                    if "健身房" in field_name and left_time > 0:
                        ORDER_INFO = f"场地: 【{field_name}】 | 剩余支付时间: {left_time}s"
                        SUCCESS_FLAG = True
                        print(f"\n{'='*60}")
                        print(f"🚨 侦察兵捷报：发现目标已入库！！")
                        print(f"🚨 详情：{ORDER_INFO}")
                        print(f"🚨 立即通知所有线程停止攻击，准备撤退去付款！")
                        print(f"{'='*60}\n")
                        return
                        
        except Exception as e:
            # 查询接口拥堵导致超时，忽略并继续查
            pass
            
        # 侦察频率，避免太快被封
        time.sleep(3.5)

def killer_strike(session, item):
    """
    【刺客线程】
    核心使命：建立超长连接，把请求死死塞进服务器队列里。
    不重试，不回头。
    """
    global SUCCESS_FLAG
    field_no = item["FieldNo"]
    params = {
        "checkdata": json.dumps([item], ensure_ascii=False),
        "dateadd": DATE_ADD,
        "VenueNo": "01"
    }

    # 最多试 2 次：主要为了应对被 Nginx 极速秒回“当前人数过多”的情况
    for attempt in range(2):
        if SUCCESS_FLAG:
            return
            
        try:
            now = datetime.now().strftime('%H:%M:%S.%f')[:-3]
            print(f"[{now}] 🎯 刺客 [{field_no}] 发起第 {attempt+1} 次冲锋...")
            
            # 【终极核心】：timeout = 300 秒，请求一旦没秒退，就在排队区死等！
            resp = session.get(BASE_URL, params=params, timeout=300)
            res_text = resp.text
            
            if "人数过多" in res_text:
                # 说明没挤进业务队列，被网关挡住了，休息 1.5 秒再挤
                print(f"    🛡️ [{field_no}] 撞上 Nginx 盾墙(人数过多)，稍后重试...")
                time.sleep(1.5)
                continue
            elif "已被" in res_text or "已被其他人抢跑" in res_text or "已满" in res_text:
                print(f"    ❌ 刺客 [{field_no}] 确认阵亡，场地已没坑位。")
                return
            elif "成功" in res_text:
                SUCCESS_FLAG = True
                return
            else:
                return

        except requests.exceptions.RequestException:
            # 遭遇 Read Timeout 是天大的好事，说明请求已经在后台数据库排上队了。
            # 直接结束此线程的重试循环，让侦察兵去检测最终结果即可。
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ⏳ 刺客 [{field_no}] 成功潜入后台(超时阻塞)，等待侦察兵确认战果...")
            break


# ===================== 第四部分：战前部署与主流程 =====================

def prepare_war():
    """认证准备与连接预热"""
    print("\n" + "="*50)
    print("====== 阶段一：预备与认证 ======")
    now = datetime.now()
    keeper = GymAuthKeeper()

    if not os.path.exists(STATE_FILE):
        print("⚠️ 未找到认证状态文件，开始执行首次登录...")
        keeper.first_login()

    # 如果时间尚早，先休眠
    if now.hour < 11 or (now.hour == 11 and now.minute < 55):
        target = now.replace(hour=11, minute=55, second=0, microsecond=0)
        wait_seconds = (target - now).total_seconds()
        print(f"⏳ 当前时间较早，将休眠 {wait_seconds:.0f} 秒至 11:55:00...")
        time.sleep(wait_seconds)

    print("🔄 获取最新鲜 Cookie...")
    cookie_str = keeper.get_valid_cookie()
    HEADERS["Cookie"] = cookie_str
    print("✅ 成功提取并应用最新 Cookie！")

    # 创建复用连接池的 Session
    s = requests.Session()
    s.headers.update(HEADERS)
    
    # 轻量请求预热 TCP 长连接隧道
    try:
        print("🔌 正在进行 TCP 握手预热...")
        s.get(HOME_URL, timeout=5)
        print("✅ TCP 连接池建立完毕，弹药上膛！")
    except Exception:
        pass
        
    return s

def build_targets():
    """构建最想要的刺杀目标名单"""
    targets = []
    # 逆向生成，比如从 JSP031 到 JSP027，只要前5个
    for i in range(START_FIELD, END_FIELD - 1, -1):
        targets.append({
            "FieldNo": f"JSP{i:03d}",
            "FieldTypeNo": FIELD_TYPE_NO,
            "BeginTime": TARGET_TIME["begin"],
            "Endtime": TARGET_TIME["end"],
            "Price": PRICE
        })
    return targets

def start_battle(session):
    """精准卡点并分发兵力"""
    targets = build_targets()
    print("\n====== 阶段二：精准倒计时阶段 ======")
    print(f"🎯 本次刺杀目标: {[t['FieldNo'] for t in targets]}")
    print("⏳ 等待 12:00:00 开闸...")

    while True:
        now = datetime.now()
        if now.hour >= 12:
            print(f"\n🚀 【{now.strftime('%H:%M:%S.%f')[:-3]}】 12点已到，全员开火！！")
            break
        time.sleep(0.01) # 极短睡眠，精准微秒级释放

    threads = []
    
    # 1. 瞬间释放所有刺客线程，齐头并进
    for target in targets:
        t = threading.Thread(target=killer_strike, args=(session, target))
        t.daemon = True
        threads.append(t)
        t.start()
        
    # 2. 释放侦察兵线程，俯瞰全局
    scout_thread = threading.Thread(target=check_order_scout, args=(session,))
    scout_thread.daemon = True
    scout_thread.start()

    # 3. 大本营主线程监控战况
    while not SUCCESS_FLAG:
        # 如果刺客和侦察兵全部阵亡/结束，退出循环
        if not any(t.is_alive() for t in threads) and not scout_thread.is_alive():
            break
        time.sleep(0.5)

    if SUCCESS_FLAG:
        print("\n🏆 战斗圆满结束！请立刻前往手机端完成付款！")
    else:
        print("\n⛔ 战局结束：未能抢到目标场地（已被全数抢空）。")


# ===================== 主程序入口 =====================
if __name__ == "__main__":
    try:
        # 第一步：准备粮草（认证与连接池预热）
        war_session = prepare_war()
        
        # 第二步：血战到底（并发占位抢票）
        start_battle(war_session)
        
    except KeyboardInterrupt:
        print("\n⚠️ 收到中止信号，脚本已退出。")
    except Exception as e:
        logger.error(f"发生严重错误: {e}")