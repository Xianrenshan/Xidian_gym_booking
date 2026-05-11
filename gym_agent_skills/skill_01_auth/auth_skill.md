---
name: verify_and_get_cookie
description: 西电南校区体育馆系统的身份认证与 Cookie 获取工具。
version: 1.0.0
---

# 🎯 技能意图
你是一个严谨的 AI 助手。由于抢票系统具有严格的会话时效性，在执行任何[订单查询]或[自动抢票]操作**之前**，你必须优先调用此技能获取最新的安全凭证（Cookie）。

# 🛡️ 护栏规则 (Guardrails)
1. **不要伪造 Cookie：** 绝对不要尝试自己捏造或拼凑 JWT Token，必须完全依赖此脚本的返回结果。
2. **处理扫码异常：** 如果脚本返回的 status 为 "auth_required"，这意味着持久化状态已失效。你必须立刻停止当前工作流，并用人类语言明确告知用户：“请打开终端，手动运行 `python gym_agent_skills/skill_01_auth/auth_action.py --manual_login` 进行微信扫码登录”。不要尝试自己处理扫码。

# 💻 调用方式
获取 JSON 格式的凭证：
```bash
python gym_agent_skills/skill_01_auth/auth_action.py