import argparse
import json
import time
from datetime import datetime
import requests
import sys

BASE_URL = "https://tybsouthgym.xidian.edu.cn/Field/OrderField"
ORDER_LIST_URL = "https://tybsouthgym.xidian.edu.cn/Field/GetFieldOrder"
FIELD_TYPE_NO = "006"
START_FIELD = 31
END_FIELD = 13

# 内部查询订单工具，供抢票逻辑进行闭环验证
def check_has_order(target_field: str, headers: dict) -> bool:
    params = {"PageNum": 1, "PageSize": 20, "Condition": ""}
    try:
        resp = requests.get(ORDER_LIST_URL, headers=headers, params=params, timeout=7)
        data = json.loads(resp.text)
        target_name = target_field.replace("JSP", "健身房")
        for o in data.get("datatable", []):
            if target_name in o.get("Field", "") and o.get("LeftTime", 0) > 0:
                print(f"    ✅ 验证通过：查到新订单 {target_name}", file=sys.stderr)
                return True
        return False
    except Exception:
        return False

def build_targets(begin_time: str, end_time: str, price: str):
    targets = []
    for i in range(START_FIELD, END_FIELD - 1, -1):
        targets.append({
            "FieldNo": f"JSP{i:03d}",
            "FieldTypeNo": FIELD_TYPE_NO,
            "BeginTime": begin_time,
            "Endtime": end_time,
            "Price": price
        })
    return targets

def try_order(item: dict, headers: dict, date_add: int, max_retry=10) -> tuple:
    field = item["FieldNo"]
    begin = item["BeginTime"]
    end = item["Endtime"]
    
    params = {
        "checkdata": json.dumps([item], ensure_ascii=False),
        "dateadd": date_add,
        "VenueNo": "01"
    }
    
    FINAL_FAIL_KEYWORDS = ["已满", "已预订", "已预约", "已过期", "不可预约", "不存在"]
    
    for attempt in range(max_retry):
        now_str = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[{now_str}] 🔄 [{field}] 第{attempt+1}次突击 | {begin}~{end}", file=sys.stderr)
        
        try:
            resp = requests.get(BASE_URL, headers=headers, params=params, timeout=10)
            res = resp.text
            
            for keyword in FINAL_FAIL_KEYWORDS:
                if keyword in res:
                    print(f"    ❌ [{field}] 终态判定: {keyword}，果断放弃并切换目标", file=sys.stderr)
                    return False, True
        except requests.exceptions.Timeout:
            print(f"    ⏱️ [{field}] 遭遇网络超时", file=sys.stderr)
        except Exception as e:
            print(f"    💥 [{field}] 异常拦截: {str(e)[:40]}", file=sys.stderr)
            
        print(f"    🔍 [{field}] 交叉验证订单数据库...", file=sys.stderr)
        if check_has_order(field, headers):
            return True, True
            
        if attempt < max_retry - 1:
            wait = 4.5 + attempt * 1.5
            time.sleep(wait)
            
    return False, True

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cookie", required=True)
    parser.add_argument("--date_add", type=int, required=True)
    parser.add_argument("--begin_time", required=True)
    parser.add_argument("--end_time", required=True)
    parser.add_argument("--price", default="2.00")
    args = parser.parse_args()

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Cookie": args.cookie,
        "Accept": "*/*",
        "X-Requested-With": "XMLHttpRequest"
    }

    print(f"⏳ 任务已加载。倒计时进入潜伏状态，等待 12:00:00 激活...", file=sys.stderr)
    while True:
        now = datetime.now()
        if now.hour == 12 and now.minute == 0:
            print(f"\n🚀 时间到！全速执行抢票矩阵...", file=sys.stderr)
            break
        # 优化：11:59:50 后高频探测，其他时间休眠长一点以节省资源
        if now.hour == 11 and now.minute == 59 and now.second > 50:
            time.sleep(0.1)
        else:
            time.sleep(3)

    targets = build_targets(args.begin_time, args.end_time, args.price)
    
    success_field = None
    for item in targets:
        success, _ = try_order(item, headers, args.date_add)
        if success:
            success_field = item["FieldNo"]
            break
        time.sleep(2)

    # ！！！核心关键点：只有最终结果能输出到 stdout，供 LLM 读取 ！！！
    if success_field:
        result = {
            "status": "success",
            "msg": "战术执行成功",
            "details": f"场地 {success_field} | 时间 {args.begin_time}-{args.end_time}"
        }
    else:
        result = {
            "status": "fail",
            "msg": "所有攻击矩阵均失败，场地可能已被秒空",
            "details": "None"
        }
    
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()