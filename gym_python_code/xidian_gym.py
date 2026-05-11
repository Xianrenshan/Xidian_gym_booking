import requests
import time
import json
from datetime import datetime

# ===================== 固定配置 =====================
BASE_URL = "https://tybsouthgym.xidian.edu.cn/Field/OrderField"

COOKIE = (
    "ASP.NET_SessionId=4tw4zyslqvpq3mrzpvatd0df; "
    "JWTUserToken=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJuYW1lIjoiN2E5MTJjOWQtMmE0Mi00MGIxLWE0NGMtNjAyZjFkZjNhYTg3IiwiZXhwIjoxNzc4MDY4MDE0LjAsImp0aSI6ImxnIiwiaWF0IjoiMjAyNi0wNC0yOSAxMTo0Njo1MyJ9.0osvAuAgJFA8BISDY2CCZxMomIDpbGWHmOdCgj_qo4Q; "
    "LoginSource=2; "
    "LoginType=1; "
    "UqZBpD3n3iPIDwJU9B6mtG2SWO4d859GPsHNv4iJpUKcSRiMD+8_=v12KFbQwSD9II; "
    "UserId=7a912c9d-2a42-40b1-a44c-602f1df3aa87; "
    "VenueNo=01; "
    "WXOpenId=20009200870"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.2 Safari/605.1.15",
    "Cookie": COOKIE,
    "Accept": "*/*",
    "X-Requested-With": "XMLHttpRequest"
}

# ===================== 订单查询函数 =====================
ORDER_LIST_URL = "https://tybsouthgym.xidian.edu.cn/Field/GetFieldOrder"

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


# ===================== 抢场策略 =====================
DATE_ADD = 2
FIELD_TYPE_NO = "006"
START_FIELD = 31
END_FIELD = 13

TIME_PLAN = [
    {"begin": "14:00", "end": "17:00", "price": "2.00"},
    {"begin": "18:00", "end": "21:00", "price": "2.00"},
]

# ===================== 自动生成场地 =====================
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

# ===================== 预约函数（以订单查询为核心） =====================
def try_order(item, max_retry=10, base_timeout=15):
    """
    核心逻辑：订单查询是唯一成功标准
    - 请求接口 -> 打印响应 -> 查订单 -> 查到=成功，没查到=重试
    - 只有明确"已满"等终态失败才换场地
    """
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
        
        # 打印场次分隔线和尝试信息
        if attempt == 0:
            print(f"\n{'='*50}")
            print(f"[{now_str}] 🎯 [{field}] 第{attempt_num}次尝试 | {begin}~{end}")
        else:
            print(f"\n[{now_str}] 🔄 [{field}] 第{attempt_num}次尝试 | {begin}~{end}")
        
        # ========== 第1步：请求抢场接口 ==========
        res = ""  # 初始化，避免异常时未定义
        try:
            resp = requests.get(BASE_URL, headers=HEADERS, params=params, timeout=base_timeout)
            res = resp.text
            now_str = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"[{now_str}] 📡 [{field}] 状态码:{resp.status_code} 响应:{res[:120]}...")
            
            # 检查是否终态失败（确定没票了，换场地）
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
        
        # ========== 第2步：查订单（唯一成功标准） ==========
        print(f"    🔍 [{field}] 开始查询订单...")
        
        if check_has_order(field):
            print(f"\n{'='*50}")
            print(f"🎉 [{field}] {begin}~{end} 预约成功！（订单确认）")
            print(f"{'='*50}")
            return (True, True)
        
        # 没查到，准备重试
        if attempt < max_retry - 1:
            wait = 4.5 + attempt * 1.5
            print(f"    ⏳ [{field}] 未成功，等待{wait:.1f}s后第{attempt_num+1}次尝试...")
            time.sleep(wait)
        else:
            print(f"    ⚠️ [{field}] 重试耗尽，放弃此场地")
            return (False, True)
    
    return (False, True)


# ===================== 等待12点 =====================
def wait_for_12():
    print("⏳ 等待 12:00 开始抢场...")
    while True:
        now = datetime.now()
        if now.hour == 12 and now.minute == 0:
            print("\n🚀 12点整，开始抢场！")
            break
        time.sleep(3.12)

# ===================== 主逻辑 =====================
if __name__ == "__main__":
    wait_for_12()

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
                exit()
            time.sleep(3.9)

    print("\n⛔ 所有场次抢完，未抢到可预约场地")