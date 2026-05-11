import json
import os
import time
import base64
import argparse
import sys
from playwright.sync_api import sync_playwright

LOGIN_URL = "https://tybsouthgym.xidian.edu.cn/"
TEST_API_URL = "https://tybsouthgym.xidian.edu.cn/Field/GetFieldOrder?PageNum=1&PageSize=1"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth_state.json")
JWT_EXPIRE_BUFFER = 3 * 60

class GymAuthKeeper:
    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self._state = None

    def first_login(self) -> None:
        print("====== 首次登录模式 ======", file=sys.stderr)
        print("正在启动浏览器，请在弹出的窗口中使用微信扫码...", file=sys.stderr)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
            context = browser.new_context(viewport={"width": 1280, "height": 720})
            page = context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            input(">> 扫码并确认页面跳转后，请按回车键完成保存 <<")
            context.storage_state(path=self.state_file)
            browser.close()
        print("✅ 状态已保存，请重新让 AI 执行任务。", file=sys.stderr)

    def _load_state(self):
        if os.path.exists(self.state_file):
            with open(self.state_file, "r") as f:
                self._state = json.load(f)
            return self._state
        return None

    def get_valid_cookie(self) -> str:
        if self._load_state() is None:
            raise PermissionError("NO_STATE_FILE")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=self._state)
            page = context.new_page()
            try:
                page.goto(TEST_API_URL, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)
            except Exception:
                pass
            new_state = context.storage_state()
            self._state = new_state
            with open(self.state_file, "w") as f:
                json.dump(new_state, f)
            browser.close()

        cookies = self._state.get("cookies", [])
        return "; ".join([f"{c['name']}={c['value']}" for c in cookies])

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manual_login", action="store_true", help="人类介入：手动弹出窗口扫码登录")
    args = parser.parse_args()

    keeper = GymAuthKeeper()

    if args.manual_login:
        keeper.first_login()
        sys.exit(0)

    try:
        cookie_str = keeper.get_valid_cookie()
        result = {
            "status": "success",
            "cookie": cookie_str,
            "msg": "Cookie 刷新并获取成功"
        }
        # 唯一输出到 stdout 给大模型解析的 JSON
        print(json.dumps(result, ensure_ascii=False))
    except PermissionError:
        result = {
            "status": "auth_required",
            "msg": "状态文件不存在或已失效，请通知人类执行 --manual_login"
        }
        print(json.dumps(result, ensure_ascii=False))
    except Exception as e:
        result = {"status": "error", "msg": f"发生意外错误: {str(e)}"}
        print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()