# Gate B — Sandbox QA checklist

本 Gate 验证真实 Webull Sandbox 连接、行情、限价单事件和撤单闭环，以及交易禁用边界。只允许使用 Sandbox 构建和 Sandbox 账户；严禁连接 LIVE、严禁提交 LIVE 订单、严禁以任何 LIVE 订单“验证”功能。

## 记录规则

每个步骤只能标记 `PASS`、`FAIL` 或 `BLOCKED`。`FAIL` 包括误连 LIVE、订单意外成交、账户/订单信息未脱敏或交易禁用边界失效；发生时立即停止下单并升级处理。`BLOCKED` 只能用于真实外部依赖不可用的情况，不能用来掩盖未执行。

Gate B 必须所有必需步骤 `PASS`，且 `sandboxClosure` 证据为 `PASS`，才能进入安装器 QA。

## 前置条件

- [ ] Gate A 已 `PASS`，且使用同一测试日期的脱敏凭据证据。
- [ ] 通过 `packaging/build_windows.ps1 -SandboxQa` 生成独立目录 `build\windows-x64-sandbox-qa`；不得复用正式 LIVE 构建目录或混用其中的 runtime 数据。
- [ ] QA 制品内嵌 `packaging/sandbox-qa-policy.json`：`environment=SANDBOX`、`liveAllowed=false`；记录其 SHA-256。
- [ ] Sandbox 账户有行情、持仓、订单和撤单权限；准备最小整股数量和远离当前盘口的 DAY LIMIT 价格。
- [ ] 连接前已确认无 LIVE 账户会话、无未完成的 Sandbox 旧订单；本 Gate 仅执行下述四个彼此隔离的最小限价单场景，每次最多保留一笔活动订单。

## 步骤

| ID | 人工操作与预期结果 | 状态 | 证据引用/备注 |
|---|---|---|---|
| B-SBX-001 | 检查 Sandbox QA 制品：策略文件显示 `SANDBOX`/`liveAllowed=false`，目录名和启动入口为 Sandbox QA；发现 LIVE 配置或无法确认来源时停止。 |  |  |
| B-SBX-002 | 启动制品并访问本机健康检查；确认仅监听 `127.0.0.1`，页面可加载，启动日志无凭据/令牌，且没有自动提交订单。 |  |  |
| B-SBX-003 | 绑定 Sandbox 账户并选择目标账户；确认账户候选、连接状态和错误详情均为脱敏显示，页面没有 LIVE 标识或 LIVE 解锁路径。 |  |  |
| B-SBX-004 | 在 AAPL 前台连续观察至少 2 分钟，记录 QUOTE/SNAPSHOT/TICK、买卖一档和新鲜度变化；核对购买力及持仓六项字段。持仓应约每 15 秒刷新，页面后台暂停，返回前台立即刷新。 |  |  |
| B-SBX-005 | 执行 `AAPL → MSFT → AAPL`：每次先清空旧报价、取消旧订阅并在切换期间禁单；最终始终只有当前股票的一组订阅和行情。 |  |  |
| B-SBX-006 | 场景 1：在 CORE 时段提交 1 股 Sandbox BUY LIMIT，限价低于当时 best bid、避免市价化；记录脱敏 client order ID，验证订单事件，再撤单并完成 broker-side 对账。若意外成交，仅记录并另建一笔未成交限价单完成撤单，不做补偿交易。 |  |  |
| B-SBX-007 | 场景 2：对一次提交动作快速双击；本地账本和 Broker 端必须都只有一个 client order ID/一笔订单。验证后撤销该笔未成交订单。出现第二笔即 `FAIL`。 |  |  |
| B-SBX-008 | 场景 3：保留一笔未决限价单后正常退出并重启；确认按原 client order ID 恢复和查询，绝不重新提交。恢复完成后撤销并对账。 |  |  |
| B-SBX-009 | 场景 4：用受控断网制造一次提交结果不确定；必须进入 `UNKNOWN`。恢复网络后只查询原 client order ID，Broker 端不得出现第二笔订单；若原单仍活动则人工撤销。 |  |  |
| B-SBX-010 | 受控断网分别记录 `MARKET_READY=false` 与 `ORDER_EVENTS_READY=false`，确认任一状态都立即禁止 BUY/SELL；不得通过新订单排查。 |  |  |
| B-SBX-011 | 将页面置于后台：确认页面后台健康门禁禁止交易；回到前台后只有行情和订单事件等全部健康条件恢复才重新允许交易。 |  |  |
| B-SBX-012 | 验证行情过期、股票切换、账户离线、交易开关关闭等其余边界均禁单并显示脱敏原因；恢复健康本身不得产生订单。 |  |  |
| B-SBX-013 | 清理全部 Sandbox 未决订单；脱敏保存 Broker 订单列表、应用状态和最小日志证据，确认本地账本可对账且无 LIVE 请求。 |  |  |
| B-SBX-014 | 第二人复核四个隔离场景、门禁和 broker-side 记录；确认无自动重发、重复订单、未解释状态或 LIVE 请求。 |  |  |

## 绝对安全边界

- 本 Gate 允许四个彼此隔离的 Sandbox 场景，各场景只用 1 股、远离盘口的 `LIMIT`/`DAY` 订单，且任一时刻最多一笔活动订单；禁止市价单、期权、做空、真实账户订单和任何“补偿单”。
- 任何环境显示 `LIVE`、`liveAllowed=true`、账户无法确认是 Sandbox、订单价格接近可成交范围或状态不确定时，立即停止并标记 `FAIL` 或 `BLOCKED`，不得通过再次下单排查。
- Sandbox 订单意外成交时只做 broker-side 对账和人工升级，不发送相反方向订单，不尝试用新订单“冲销”。

## 脱敏证据规范

每个证据文件名必须包含 Gate 和步骤 ID，例如 `B-SBX-008-PASS-20260905.json`；记录时间（含时区）、操作者、OS/build、产品版本、Sandbox policy hash、环境和结果。订单/券商证据只保留股票代码、方向、数量、限价、状态和时间等完成判断所必需字段。

- Account ID、订单 ID、client order ID、profile ID 只保留末 4 位，例如 `••••1234`；App Key、App Secret、token、cookie、手机号、密码、Authorization 头、完整 broker 回执和 SDK debug trace 替换为 `[REDACTED]`。
- 截图必须裁剪浏览器地址栏、用户名、机器名、余额细节和秘密；日志只提交最小脱敏片段。不得附加 `.env`、`runtime/`、数据库、token 目录或原始网络包。
- 证据存放在访问受控的发布证据目录，不提交 Git；若脱敏无法确认，证据不合格，Gate 不得 PASS。

## Gate 决策

- Gate B：`PASS` / `FAIL` / `BLOCKED`
- QA 操作者：
- 第二人复核：
- Sandbox 账户（仅末 4 位）：
- Sandbox policy SHA-256：
- broker-side 订单/撤单证据引用：
- 未决风险/后续动作：
