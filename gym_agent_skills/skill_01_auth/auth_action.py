###auth_action.py脚本 
#这个脚本已经包含了所有的最新逻辑，完全独立运行，状态文件 `auth_state.json` 会自动保存在这个脚本所在的目录下。
import json
import os
import time
import base64
import logging
import argparse
import sys
from typing import Optional

from playwright.sync_api import sync_playwright

# 强制将日志输出到 stderr，绝不污染 stdout，保证大模型 JSON 解析成功
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stderr)
logger = logging.getLogger(__name__)

# ---------- 配置 ----------
LOGIN_URL = "https://tybsouthgym.xidian.edu.cn/"
HOME_URL = "https://tybsouthgym.xidian.edu.cn/"
TEST_API_URL = "https://tybsouthgym.xidian.edu.cn/Field/GetFieldOrder?PageNum=1&PageSize=1"
# 将状态文件保存在当前 skill 目录下，实现完全解耦
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth_state.json")
# --------------------------

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
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.2 Safari/605.1.15"
            )
            page = context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded")

            # 打印到 stderr
            print("\n" + "=" * 50, file=sys.stderr)
            print("请在打开的浏览器页面中用手机微信扫码并完成登录。", file=sys.stderr)
            print("登录成功后（页面跳转到预约界面或显示正常内容），", file=sys.stderr)
            print("回到这里按回车键继续...", file=sys.stderr)
            print("=" * 50 + "\n", file=sys.stderr)
            
            input(">> 按回车确认登录完成 <<")

            context.storage_state(path=self.state_file)
            browser.close()

        logger.info(f"长期状态已保存到 {self.state_file}")
        self._load_state()

    def _load_state(self) -> Optional[dict]:
        if os.path.exists(self.state_file):
            with open(self.state_file, "r") as f:
                self._state = json.load(f)
            logger.debug("已从文件加载认证状态。")
            return self._state
        else:
            logger.warning("状态文件不存在，需要先执行首次登录。")
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
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.2 Safari/605.1.15"
            )
            page = context.new_page()

            logger.info("正在访问首页...")
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

            logger.info("点击‘场地预订’...")
            try:
                page.get_by_text("场地预订", exact=True).first.click(timeout=5000)
            except Exception:
                page.goto("https://tybsouthgym.xidian.edu.cn/Field/OrderField", wait_until="domcontentloaded")

            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(3000)

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

        cookies = self._state.get("cookies", [])
        cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])

        jwt_token = None
        for c in cookies:
            if c["name"] == "JWTUserToken":
                jwt_token = c["value"]
                break

        if jwt_token:
            exp_time = self._decode_jwt_exp(jwt_token)
            if exp_time != 0:
                remaining = exp_time - time.time()
                logger.info(f"JWT 有效，剩余 {remaining:.0f} 秒")
        else:
            logger.warning("未找到 JWTUserToken，登录可能仍失效")

        return cookie_str


# ---------- 为大模型封装的执行入口 ----------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manual_login", action="store_true", help="人类介入：手动弹出窗口扫码登录")
    args = parser.parse_args()

    keeper = GymAuthKeeper()

    # 1. 如果人类手动执行该脚本进行扫码
    if args.manual_login:
        keeper.first_login()
        print("✅ 状态已保存，请重新让 AI 执行任务。", file=sys.stderr)
        sys.exit(0)

    # 2. 大模型静默调用该脚本获取 Cookie
    try:
        cookie_str = keeper.get_valid_cookie()
        result = {
            "status": "success",
            "cookie": cookie_str,
            "msg": "Cookie 刷新并获取成功"
        }
        # 【唯一输出到 stdout 的数据，提供给大模型解析】
        print(json.dumps(result, ensure_ascii=False))

    except RuntimeError as e:
        if "没有可用" in str(e):
            result = {
                "status": "auth_required",
                "msg": "状态文件不存在，请通知人类执行 --manual_login"
            }
            print(json.dumps(result, ensure_ascii=False))
        else:
            result = {"status": "error", "msg": f"运行错误: {str(e)}"}
            print(json.dumps(result, ensure_ascii=False))
            
    except Exception as e:
        result = {"status": "error", "msg": f"发生意外错误: {str(e)}"}
        print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()