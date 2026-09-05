# Gate A — Credential QA checklist

本 Gate 验证账户绑定、凭据存储、账户选择和清理流程。只使用 Webull Sandbox 凭据；本 Gate 不连接 LIVE，也不提交任何订单。Gate A 未 PASS 前不得开始 Gate B。

## 记录规则

每个步骤只能标记一个状态：`PASS`、`FAIL` 或 `BLOCKED`。

- `PASS`：前置条件满足，步骤实际执行并有对应证据。
- `FAIL`：实际行为与预期不符，或发现凭据、令牌、完整账户标识泄漏；立即停止后续步骤。
- `BLOCKED`：外部条件（例如 Sandbox 权限、Windows Credential Manager、网络或测试账号不可用）使步骤无法执行；不得用猜测或自动化模拟结果替代。

Gate A 只有在所有必需步骤为 `PASS`、没有 `FAIL`/`BLOCKED`，且第二人复核证据后才能判定 `PASS`。

## 前置条件

- [ ] 使用专用 Windows 测试用户或隔离 VM；记录 Windows 版本、测试日期、操作者和构建版本。
- [ ] 准备仅用于 Sandbox 的 Webull OpenAPI App Key、App Secret 和测试账户；不得把值粘贴到工单、截图或聊天中。
- [ ] 核对 Sandbox QA EXE 内嵌的只读 `config/production.json`：`environment=SANDBOX`、`liveAllowed=false`；正式打包程序不得依赖开发环境变量。
- [ ] 关闭正在运行的 FastOrder 实例，并对 `%LOCALAPPDATA%\FastOrder` 做受保护的 QA 前状态记录（不复制原始内容）。
- [ ] 确认测试者不会输入 Webull 手机号、交易密码、LIVE 凭据或访问令牌。

## 步骤

| ID | 人工操作与预期结果 | 状态 | 证据引用/备注 |
|---|---|---|---|
| A-CRED-001 | 启动前检查环境：Sandbox 开关为 `true`、LIVE 开关为 `false`，没有把 LIVE 凭据作为后备值提供给测试；页面和启动日志均不得显示秘密值。 |  |  |
| A-CRED-002 | 在本机页面选择“绑定账户”，输入 Sandbox App Key/App Secret；验证成功后不得要求手机号或交易密码，且凭据值不回显、不出现在 URL、浏览器存储、进程参数或普通日志中。 |  |  |
| A-CRED-003 | 账户候选验证：单账户可自动选择；多账户必须人工选择；页面只显示脱敏标识/尾号和一次性引用，不显示完整 Account ID。 |  |  |
| A-CRED-004 | 同时勾选“记住凭据”和“自动连接”，正常退出并重新启动：无需再次输入秘密即可自动连接；确认凭据只由 Windows Credential Manager 服务 `极速下单.WebullOpenAPI` 提供；`runtime/account_profile.json` 只允许 `profile_id/account_mask/environment/auto_connect`。 |  |  |
| A-CRED-005 | 检查脱敏持久化范围：`runtime/`、`logs/`、进程环境和可读配置中不得出现 App Key、App Secret、完整 Account ID、访问/刷新令牌或 Authorization 头；只记录文件名、字段名和 hash/大小。 |  |  |
| A-CRED-006 | 执行“断开连接”：交易能力恢复安全状态，行情/订单事件停止；Credential Manager 条目、脱敏 profile 和 SDK 会话按产品约定保留。不得为了验证而提交订单。 |  |  |
| A-CRED-007 | 执行“切换账户”：确认先禁止新订单并完整停止旧 Gateway 的行情、订单事件和交易连接，状态回到未连接后才允许新绑定；浏览器只接收短生命周期 `bindingSessionId + accountRef`，不得出现完整 Account ID。 |  |  |
| A-CRED-008 | 执行“忘记此账户”：确认顺序为禁止交易 → 停止行情/订单事件 → Gateway disconnect → 清内存凭据 → 删除 Credential Manager → 删除 profile 元数据 → 清 SDK token → `UNBOUND`；历史订单数据库必须保留。 |  |  |
| A-CRED-009 | 运行 `scan_release_credentials.py --exact-credentials` 扫描安装目录及 `%LOCALAPPDATA%\FastOrder`；扫描器只输出命中路径/规则，不输出录入值。App Key、App Secret、完整 Account ID 任一明文命中即 `FAIL`。 |  |  |
| A-CRED-010 | 再次启动，必须保持 `UNBOUND` 且不得自动连接；再以缺失/错误 Sandbox 凭据验证绑定失败不会产生可交易状态或明文错误详情。 |  |  |
| A-CRED-011 | 由第二人复核证据包：确认 Credential Manager 为真实 Windows backend（不是模拟/文件后端），截图已裁剪，文本已脱敏，且未附加秘密文件或原始响应。 |  |  |

## 脱敏证据规范

证据文件名必须包含 Gate 和步骤 ID，例如 `A-CRED-004-PASS-20260905.png`；同时记录测试时间（含时区）、操作者、OS/build、产品版本、环境（只能写 `SANDBOX`）、结果和证据存放位置。推荐使用裁剪后的截图、命令退出码、脱敏日志片段和文件 SHA-256。

- Account ID、profile ID、order/client ID 只保留末 4 位，例如 `••••1234`；App Key、App Secret、token、cookie、手机号、密码、证书私钥和 Authorization 头全部替换为 `[REDACTED]`，不要只做部分遮挡。
- 不附加 `.env`、`runtime/webull_tokens/`、`runtime/account_profile.json` 原文、数据库、完整日志、Credential Manager 导出或带秘密的屏幕录像。路径中的用户名、机器名和外部 IP 也应裁剪或替换为 `[HOST]`。
- 证据只存放在受访问控制的发布证据目录，不提交 Git；若发现泄漏，立即标记 `FAIL`，撤销/重置受影响凭据并重新生成证据。

## Gate 决策

- Gate A：`PASS` / `FAIL` / `BLOCKED`
- QA 操作者：
- 第二人复核：
- 日期（含时区）：
- 关联证据包：
- 未决风险/后续动作：
