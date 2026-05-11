import requests

# ===================== 你的 Cookie =====================
COOKIE = (
    "UqZBpDn3iPIDwJU9B6mtG2SWO4d859GPsHNv4iJpUKcSRiMD+8_=v12KFbQwSD9II; "
    "ASP.NET_SessionId=ktlz1pb0wzi1ucf441ryj5sb; "
    "JWTUserToken=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJuYW1lIjoiN2E5MTJjOWQtMmE0Mi00MGIxLWE0NGMtNjAyZjFkZjNhYTg3IiwiZXhwIjoxNzc3NTQ5NjE5LjAsImp0aSI6ImxnIiwiaWF0IjoiMjAyNi0wNC0yMyAxMTo0Njo1OCJ9.Z4CbG0o1BQ2ZK512G3OZwQCaHy_4iAOhkz0Xl48TPAE; "
    "LoginType=1; UserId=7a912c9d-2a42-40b1-a44c-602f1df3aa87; "
    "WXOpenId=20009200870; LoginSource=2; VenueNo=01"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Cookie": COOKIE,
    "X-Requested-With": "XMLHttpRequest"
}

# ===================== 订单接口（已找到） =====================
ORDER_URL = "https://tybsouthgym.xidian.edu.cn/Field/GetFieldOrder"

def get_my_orders():
    params = {
        "PageNum": 1,
        "PageSize": 20,
        "Condition": ""
    }
    resp = requests.get(ORDER_URL, headers=HEADERS, params=params)
    print("状态码：", resp.status_code)
    print("\n你的订单列表：")
    print(resp.text)
    return resp.text

# 执行查询
get_my_orders()