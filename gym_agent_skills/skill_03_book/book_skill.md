---
name: execute_sniper_booking
description: 定时触发的高并发场地抢夺执行器（Sniper Mode）。
version: 1.0.0
---

# 🎯 技能意图
此技能封装了极致的抢票逻辑（包含自动等待至 12:00、场地轮询、毫秒级并发验证）。当用户的抢票意图（时间、日期）完全明确，并且你已经拿到了合法 Cookie 后，即可发射此工具。

# ⚠️ 极其重要的行为准则
1. **这是阻塞任务：** 此脚本内置了等待机制（一直挂起到 12:00:00），调用后你的进程将被阻塞。**因此，在调用之前，你必须回复用户：“报告，参数已设定完毕，我已进入静默潜伏状态，将在 12:00 准点展开行动。”**
2. **意图转化：** 用户说的“明天”对应 `--date_add 1`，“后天”对应 `--date_add 2`。用户说的“下午”通常指 `--begin_time 14:00 --end_time 17:00`，请准确推理。

# 💻 调用方式
```bash
python gym_agent_skills/skill_03_book/book_action.py --cookie "xxx" --date_add 2 --begin_time "14:00" --end_time "17:00" --price "2.00"