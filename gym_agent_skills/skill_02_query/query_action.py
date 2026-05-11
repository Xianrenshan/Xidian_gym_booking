import argparse
import json
import requests
import sys

ORDER_LIST_URL = "https://tybsouthgym.xidian.edu.cn/Field/GetFieldOrder"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cookie", required=True, help="有效的登录凭证")
    args = parser.parse_args()

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
        "Cookie": args.cookie,
        "Accept": "*/*",
        "X-Requested-With": "XMLHttpRequest"
    }

    try:
        params = {"PageNum": 1, "PageSize": 20, "Condition": ""}
        resp = requests.get(ORDER_LIST_URL, headers=headers, params=params, timeout=10)
        
        # 处理非 200 或登录失效的情况
        if resp.status_code != 200 or "datatable" not in resp.text:
            print(json.dumps({"status": "error", "msg": f"接口请求异常，可能Cookie失效，状态码：{resp.status_code}"}, ensure_ascii=False))
            sys.exit(0)

        data = json.loads(resp.text)
        raw_orders = data.get("datatable", [])
        
        valid_orders = []
        for o in raw_orders:
            # LeftTime > 0 表示未过期且待支付/已支付的有效订单
            if o.get("LeftTime", 0) > 0:
                valid_orders.append({
                    "field_name": o.get("Field", "未知场地"),
                    "order_time": f"{o.get('BeginTime', '')}-{o.get('EndTime', '')}",
                    "left_time_seconds": o.get("LeftTime")
                })
                
        result = {
            "status": "success",
            "total_valid": len(valid_orders),
            "orders": valid_orders
        }
        print(json.dumps(result, ensure_ascii=False))

    except Exception as e:
        print(json.dumps({"status": "error", "msg": f"查询崩溃: {str(e)}"}, ensure_ascii=False))

if __name__ == "__main__":
    main()