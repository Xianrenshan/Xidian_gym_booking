import json
import os
import time
import base64
import logging
from typing import Optional
from datetime import datetime

from playwright.sync_api import sync_playwright
import requests

# ===================== 第一部分：认证与自动维持配置 =====================
LOGIN_URL = "https://tybsouthgym.xidian.edu.cn/"            # 登录首页
HOME_URL = "https://tybsouthgym.xidian.edu.cn/"             # 刷新会话时访问的首页
STATE_FILE = "auth_state.json"                              # 保存浏览器长期状态的文件

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ===================== 第二部分：抢票参数配置 =====================
BASE_URL = "https://tybsouthgym.xidian.edu.cn/Field/OrderField"
ORDER_LIST_URL = "https://tybsouthgym.xidian.edu.cn/Field/GetFieldOrder"

DATE_ADD = 2
FIELD_TYPE_NO = "006"
START_FIELD = 31
END_FIELD = 13

TIME_PLAN = [
    {"begin": "14:00", "end": "17:00", "price": "2.00"},
    {"begin": "18:00", "end": "21:00", "price": "2.00"},
]

# 全局 Header，Cookie 会在预备阶段动态填入
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.2 Safari/605.1.15",
    "Accept": "*/*",
    "X-Requested-With": "XMLHttpRequest",
    "Cookie": ""  # 占位符
}


# ===================== 核心类：自动认证与 Cookie 提取 =====================
class GymAuthKeeper:
    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self._state = None

    def first_login(self) -> None:
        """弹出真实浏览器窗口，等你扫码完成登录，然后保存长期状态。"""
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
            raise RuntimeError("没有可用的认证状态，请先执行首次登录。")

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

            logger.info("正在唤起无头浏览器刷新认证状态...")
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)

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
        cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        return cookie_str


# ===================== 第三部分：核心抢票与订单检测逻辑 =====================

def check_has_order(target_field):
    params = {"PageNum": 1, "PageSize": 20, "Condition": ""}
    try:
        resp = requests.get(ORDER_LIST_URL, headers=HEADERS, params=params, timeout=7)
        data = json.loads(resp.text)
        orders = data.get("datatable", [])

        target_name = target_field.replace("JSP", "健身房")

        for o in orders:
            field_name = o.get("Field", "")
            left_time = o.get("LeftTime", 0)

            if target_name in field_name and left_time > 0:
                print(f"    ✅ 查到新订单：{field_name}，剩余支付时间：{left_time}s")
                return True

        print("    ℹ️ 未查询到新增订单")
        return False

    except Exception as e:
        print(f"    ⚠️ 查询订单异常: {e}")
        return False

def build_targets(begin_time, end_time, price):
    targets = []
    for i in range(START_FIELD, END_FIELD - 1, -1):
        field_no = f"JSP{i:03d}"
        targets.append({
            "FieldNo": field_no,
            "FieldTypeNo": FIELD_TYPE_NO,
            "BeginTime": begin_time,
            "Endtime": end_time,
            "Price": price
        })
    return targets

def try_order(item, max_retry=10, base_timeout=15):
    field = item["FieldNo"]
    begin = item["BeginTime"]
    end = item["Endtime"]
    
    params = {
        "checkdata": json.dumps([item], ensure_ascii=False),
        "dateadd": DATE_ADD,
        "VenueNo": "01"
    }
    
    FINAL_FAIL_KEYWORDS = ["已满", "已预订", "已预约", "已过期", "不可预约", "不存在"]
    
    for attempt in range(max_retry):
        attempt_num = attempt + 1
        now_str = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        
        if attempt == 0:
            print(f"\n{'='*50}")
            print(f"[{now_str}] 🎯 [{field}] 第{attempt_num}次尝试 | {begin}~{end}")
        else:
            print(f"\n[{now_str}] 🔄 [{field}] 第{attempt_num}次尝试 | {begin}~{end}")
        
        # 请求抢场接口
        res = ""
        try:
            resp = requests.get(BASE_URL, headers=HEADERS, params=params, timeout=base_timeout)
            res = resp.text
            now_str = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"[{now_str}] 📡 [{field}] 状态码:{resp.status_code} 响应:{res[:120]}...")
            
            for keyword in FINAL_FAIL_KEYWORDS:
                if keyword in res:
                    print(f"    ❌ [{field}] 终态失败: {keyword}，换场地")
                    return (False, True)
                    
        except requests.exceptions.Timeout:
            now_str = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"[{now_str}] ⏱️ [{field}] 请求超时({base_timeout}s)")
        except Exception as e:
            now_str = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"[{now_str}] 💥 [{field}] 请求异常: {str(e)[:40]}")
        
        # 查订单（唯一成功标准）
        print(f"    🔍 [{field}] 开始查询订单...")
        
        if check_has_order(field):
            print(f"\n{'='*50}")
            print(f"🎉 [{field}] {begin}~{end} 预约成功！（订单确认）")
            print(f"{'='*50}")
            return (True, True)
        
        if attempt < max_retry - 1:
            wait = 4.5 + attempt * 1.5
            print(f"    ⏳ [{field}] 未成功，等待{wait:.1f}s后第{attempt_num+1}次尝试...")
            time.sleep(wait)
        else:
            print(f"    ⚠️ [{field}] 重试耗尽，放弃此场地")
            return (False, True)
    
    return (False, True)


# ===================== 第四部分：智能融合等待逻辑 =====================
def prepare_and_wait_for_12():
    print("\n" + "="*50)
    print("====== 阶段一：预备与认证阶段 ======")
    now = datetime.now()
    keeper = GymAuthKeeper()

    # 1. 如果没有状态文件，不管几点先要求扫码登录
    if not os.path.exists(STATE_FILE):
        print("⚠️ 未找到认证状态文件，开始执行首次登录...")
        keeper.first_login()

    # 2. 核心时间调度：如果时间尚早(不到11:58)，则先休眠。防止太早获取导致12点时Cookie失效
    if now.hour < 11 or (now.hour == 11 and now.minute < 58):
        target = now.replace(hour=11, minute=58, second=0, microsecond=0)
        wait_seconds = (target - now).total_seconds()
        print(f"⏳ 当前时间较早，将休眠 {wait_seconds:.0f} 秒至 11:58:00 后再获取最新Cookie...")
        time.sleep(wait_seconds)

    # 3. 此时一定是临近抢票，获取最新鲜的 Cookie
    print("🔄 正在唤起浏览器获取最新有效 Cookie (大约需要10~20秒)...")
    cookie_str = keeper.get_valid_cookie()
    HEADERS["Cookie"] = cookie_str
    print("✅ 成功提取并应用最新 Cookie！")

    # 4. 精准蹲点 12:00:00
    print("\n====== 阶段二：精准倒计时阶段 ======")
    print("⏳ 等待 12:00 开始抢场...")
    while True:
        now = datetime.now()
        # 如果到达12点00分，或者你是在12点之后运行的脚本，直接开抢
        if (now.hour == 12 and now.minute == 0) or (now.hour >= 12):
            print(f"\n🚀 【{now.strftime('%H:%M:%S.%f')[:-3]}】 12点已到，全速开抢！")
            break
        # 极短睡眠时间，保证卡点误差极小
        time.sleep(0.05)


# ===================== 主程序入口 =====================
if __name__ == "__main__":
    # 融合后的智能等待与Cookie获取逻辑
    prepare_and_wait_for_12()

    # 原封不动的抢场循环逻辑
    for time_item in TIME_PLAN:
        b = time_item["begin"]
        e = time_item["end"]
        p = time_item["price"]
        print(f"\n{'='*50}")
        print(f"👉 开始抢场次：{b} ~ {e}")
        print(f"{'='*50}")

        targets = build_targets(b, e, p)
        for item in targets:
            success, _ = try_order(item)
            if success:
                print("🏆 抢票任务圆满结束，退出程序。请尽快去手机端付款！")
                exit(0)
            time.sleep(3.9)

    print("\n⛔ 所有场次抢完，未抢到可预约场地")