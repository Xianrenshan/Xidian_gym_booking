---
name: verify_and_get_cookie
description: 西电南校区体育馆系统的身份认证与 Cookie 获取工具。
version: 1.1.0
---

# 🎯 技能意图
你是一个严谨的 AI 助手。由于抢票系统具有严格的会话时效性，在执行任何[订单查询]或[自动抢票]操作**之前**，你必须优先调用此技能获取最新的安全凭证（Cookie）。
此脚本会在后台启动浏览器自动处理弹窗和用户身份选择，请耐心等待其返回结果。

# 🛡️ 护栏规则
1. **绝不伪造 Cookie：** 必须完全依赖此脚本的返回结果。
2. **处理扫码异常：** 如果脚本返回的 status 为 "auth_required"，说明本地状态已过期。你必须立刻停止当前任务，并用人类语言告诉用户：“请打开终端，手动运行以下命令扫码登录：\n`python skill_01_auth/auth_action.py --manual_login`”。

# 💻 调用方式
无需传递任何参数：
```bash
python skill_01_auth/auth_action.py