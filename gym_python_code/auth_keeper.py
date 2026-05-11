# auth_keeper.py
"""
西电南校区体育馆自动登录维持器。
第一次运行时弹出浏览器窗口，你需要用手机微信扫一扫完成登录，
之后程序自动保存长期凭证，后续每次获取Cookie都会自动续期。
"""

import json
import os
import time
import base64
import logging
from typing import Optional

from playwright.sync_api import sync_playwright
import requests

# ---------- 配置 ----------
LOGIN_URL = "https://tybsouthgym.xidian.edu.cn/"            # 登录首页
HOME_URL = "https://tybsouthgym.xidian.edu.cn/"             # 刷新会话时访问的首页
TEST_API_URL = "https://tybsouthgym.xidian.edu.cn/Field/GetFieldOrder?PageNum=1&PageSize=1"   # 用于最终验证
STATE_FILE = "auth_state.json"                              # 保存浏览器长期状态的文件
JWT_EXPIRE_BUFFER = 3 * 60                                  # 在JWT过期前多少秒就主动刷新（秒）
# --------------------------

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class GymAuthKeeper:
    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self._state = None

    # ---------- 首次登录 ----------
    def first_login(self) -> None:
        """弹出真实浏览器窗口，等你扫码完成登录，然后保存长期状态。"""
        logger.info("正在启动浏览器进行首次登录...")
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,  # 必须有画面，你需要扫二维码
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                            "Version/26.2 Safari/605.1.15"
            )
            page = context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded")

            print("\n" + "=" * 50)
            print("请在打开的浏览器页面中用手机微信扫码并完成登录。")
            print("登录成功后（页面跳转到预约界面或显示正常内容），")
            print("回到这里按回车键继续...")
            print("=" * 50 + "\n")
            input(">> 按回车确认登录完成 <<")

            # 保存完整浏览器状态（包括cookies, localStorage, session等）
            context.storage_state(path=self.state_file)
            browser.close()

        logger.info(f"长期状态已保存到 {self.state_file}")
        self._load_state()

    # ---------- 加载状态 ----------
    def _load_state(self) -> Optional[dict]:
        """从文件加载长期状态。"""
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
        """解码JWT的过期时间戳（返回秒级unix时间戳），解码失败返回0。"""
        try:
            payload = token.split(".")[1]
            # 补齐base64 padding
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
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                        "Version/26.2 Safari/605.1.15"
            )
            page = context.new_page()

            # 1. 访问首页
            logger.info("正在访问首页...")
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)

            # 2. 关闭弹窗（通用）
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

            # 3. 点击“场地预订”
            logger.info("点击‘场地预订’...")
            try:
                page.get_by_text("场地预订", exact=True).first.click(timeout=5000)
            except Exception:
                page.goto("https://tybsouthgym.xidian.edu.cn/Field/OrderField", wait_until="domcontentloaded")

            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(3000)

            # 4. 检查是否被重定向到用户类型选择页
            page_title = page.title()
            if "用户类型选择" in page_title or "用户类型" in page_title:
                logger.info("检测到用户类型选择页面，正在处理...")
                # 再次关闭可能遮罩的弹窗
                page.wait_for_timeout(2000)
                for sel in close_selectors:
                    try:
                        el = page.locator(sel).first
                        if el.count() > 0 and el.is_visible():
                            el.click(timeout=2000)
                            page.wait_for_timeout(1500)
                    except Exception:
                        pass

                # 点击“校内用户”按钮（尝试多种定位）
                page.wait_for_timeout(1000)
                try:
                    # 方法1：精确文本
                    page.get_by_text("校内用户", exact=True).first.click(timeout=3000)
                    logger.info("已点击‘校内用户’按钮")
                except Exception:
                    try:
                        # 方法2：只包含“校内”
                        page.get_by_text("校内", exact=False).first.click(timeout=3000)
                        logger.info("已点击包含‘校内’的按钮")
                    except Exception:
                        # 方法3：通过 class 或 input 类型
                        page.click('input[value*="校内"]', timeout=3000)
                        logger.info("通过 input 点击了校内选项")
                
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(3000)

            # 5. 此时应该已经进入预订页面，再等一会儿确保 JWT 写入
            page.wait_for_timeout(3000)

            # 6. 保存最新状态
            new_state = context.storage_state()
            self._state = new_state
            with open(self.state_file, "w") as f:
                json.dump(new_state, f)

            browser.close()

        # 7. 提取 Cookie 并检查 JWT
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

    # ---------- 守护模式（可选） ----------
    def keep_alive(self, interval: int = 600):
        """
        守护模式：每隔 interval 秒自动刷新一次Cookie，
        可以在后台一直运行，确保抢票时永远有效。
        interval 建议设置为600秒（10分钟），远小于JWT的20分钟有效期。
        """
        logger.info(f"开始守护模式，每 {interval} 秒自动刷新一次会话...")
        while True:
            try:
                self.get_valid_cookie()
                logger.info("会话刷新成功。")
            except Exception as e:
                logger.error(f"守护刷新失败: {e}")
            time.sleep(interval)


# ---------- 命令行入口 ----------
if __name__ == "__main__":
    keeper = GymAuthKeeper()

    # 如果状态文件不存在，执行首次登录
    if not os.path.exists(STATE_FILE):
        keeper.first_login()

    # 示例：获取一次有效Cookie并打印
    print("\n开始获取最新Cookie...")
    cookie = keeper.get_valid_cookie()
    print("\n--- 当前有效Cookie ---")
    print(cookie[:200] + "..." if len(cookie) > 200 else cookie)

    # 验证：用这个Cookie发一个请求看看
    print("\n验证Cookie是否正常工作...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.2 Safari/605.1.15",
        "Cookie": cookie,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*"
    }
    try:
        resp = requests.get(TEST_API_URL, headers=headers, timeout=10)
        print(f"状态码: {resp.status_code}")
        print(f"响应内容(前200字符): {resp.text[:200]}")
        if resp.status_code == 200 and "datatable" in resp.text:
            print("✅ Cookie有效，请求成功！")
        else:
            print("⚠️ 响应异常，可能需要检查登录状态。")
    except Exception as e:
        print(f"❌ 请求失败: {e}")

    # 如果想启动守护模式，取消下面一行的注释
    # keeper.keep_alive()