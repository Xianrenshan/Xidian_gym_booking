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

from playwright.sync_api import sync_playwright, BrowserContext
import requests

# ---------- 配置 ----------
LOGIN_URL = "https://tybsouthgym.xidian.edu.cn/"          # 登录首页，可能会重定向到微信扫码页
TEST_API_URL = "https://tybsouthgym.xidian.edu.cn/Field/GetFieldOrder?PageNum=1&PageSize=1"  # 用来刷新cookie的轻量接口
STATE_FILE = "auth_state.json"                            # 保存浏览器长期状态的文件
JWT_EXPIRE_BUFFER = 3 * 60                                # 在JWT过期前多少秒就主动刷新（秒）
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

    # ---------- 获取有效Cookie ----------
    def get_valid_cookie(self) -> str:
        """
        对外接口：始终返回一个可直接用于请求头部的 Cookie 字符串。
        内部会自动检测JWT是否即将过期，并通过已保存的状态刷新token。
        """
        if self._state is None:
            self._load_state()
        if self._state is None:
            raise RuntimeError("没有可用的认证状态，请先执行 first_login()。")

        # 使用保存的状态创建一个临时浏览器上下文，用来执行静默刷新
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)  # 后台静默运行，无需显示窗口
            context = browser.new_context(storage_state=self._state)
            page = context.new_page()

            # 访问一个需要登录的接口，触发服务器可能的Set-Cookie续期
            logger.info("正在使用长期状态刷新短期token...")
            try:
                response = page.goto(TEST_API_URL, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)  # 等待可能的异步js执行
            except Exception as e:
                logger.warning(f"刷新请求出现问题: {e}")

            # 重新提取所有cookie，并更新状态文件
            new_state = context.storage_state()
            self._state = new_state
            with open(self.state_file, "w") as f:
                json.dump(new_state, f)

            browser.close()

        # 从状态文件中提取所有cookie，拼接成Cookie字符串
        cookies = self._state.get("cookies", [])
        cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        logger.debug(f"生成Cookie字符串，包含 {len(cookies)} 个cookie。")

        # 额外检查JWT是否真的刷新了（如果存在JWTUserToken）
        jwt_token = None
        for c in cookies:
            if c["name"] == "JWTUserToken":
                jwt_token = c["value"]
                break
        if jwt_token:
            exp_time = self._decode_jwt_exp(jwt_token)
            if exp_time == 0:
                logger.warning("无法解析JWT过期时间，Cookie可能无效。")
            else:
                remaining = exp_time - time.time()
                if remaining < JWT_EXPIRE_BUFFER:
                    logger.warning(f"JWT剩余时间仅 {remaining:.0f} 秒，但刷新后仍不足，请关注。")
                else:
                    logger.info(f"JWT有效，剩余 {remaining:.0f} 秒。")
        else:
            logger.warning("未在Cookie中找到JWTUserToken，登录状态可能已失效！")

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