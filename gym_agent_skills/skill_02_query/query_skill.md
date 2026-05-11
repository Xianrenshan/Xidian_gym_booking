---
name: query_my_orders
description: 查询当前账号在体育馆系统中已成功预约的订单列表。
version: 1.0.0
---

# 🎯 技能意图
1. **验重与阻断：** 在为用户执行抢票任务前，用于确认用户是否已经有了订单，避免重复占用资源。
2. **战果确认：** 在抢票任务执行完毕后，调用此接口进行二次核验，确认订单是否真实写入系统。

# 💻 调用方式
调用前必须拥有合法的 Cookie。
```bash
python gym_agent_skills/skill_02_query/query_action.py --cookie "你的有效Cookie"