# Gate A/B — 10～15 分钟人工验收单

历史用途：一张表完成 Windows 凭据安全和 Broker Sandbox 主链路验收。V1.0 已由项目负责人取消 Sandbox 正式门禁；当前仅 `AB-01～AB-05` 继续作为 Gate A，`AB-06～AB-12` 保留为未来可选回归，不得据此伪造本版本 Sandbox PASS。发布自动化严禁 LIVE 下单。

> 预计人工操作 10～15 分钟；行情、订单事件及受控断网的传播等待不计入操作时间。任何秘密泄漏、重复订单、自动重发或 LIVE 请求都立即判定 `FAIL`。外部条件无法验证则记 `BLOCKED`，不得勾选代替。

## 开始前（1 分钟）

- [ ] 使用普通交互式 Windows 用户；关闭其他 FastOrder 实例。
- [ ] 运行 `build\windows-x64-sandbox-qa\FastOrder.dist\FastOrder.exe`，核对内嵌策略为 `SANDBOX`、`liveAllowed=false`。
- [ ] Broker Sandbox 当前没有遗留未决订单；准备 App Key、App Secret，但不要截图、复制到日志或保存到文本文件。
- [ ] 建立本次证据目录，只保存裁剪截图、命令结果和 SHA256；Account/Order ID 只保留末四位。

## 一页验收表

| # | 预计 | 操作 | 必须看到 | 结果 |
|---|---:|---|---|---|
| AB-01 | 1 分钟 | 点击“绑定账户”，录入 Sandbox App Key/App Secret，同时勾选“记住凭据”和“以后自动连接”，选择脱敏账户并连接。 | 页面只显示脱敏账户；不回显 Secret 或完整 Account ID；账户、购买力和连接状态正常。 | ☐ PASS ☐ FAIL ☐ BLOCKED |
| AB-02 | 1 分钟 | 正常退出 EXE，再重新启动。 | 无需重新输入凭据并自动连接；Windows Credential Manager 存在 `极速下单.WebullOpenAPI` 条目；`account_profile.json` 仅有 `profile_id/account_mask/environment/auto_connect`。 | ☐ PASS ☐ FAIL ☐ BLOCKED |
| AB-03 | 1 分钟 | 从账户菜单依次测试“断开连接”→“连接账户”→“切换账户”。多账户时选择另一脱敏候选；单账户时重新绑定同一候选以验证生命周期。 | 断开立即禁单且保留凭据；切换必须先完整断开旧连接；浏览器从未显示完整 Account ID。 | ☐ PASS ☐ FAIL ☐ BLOCKED |
| AB-04 | 1 分钟 | 点击“忘记此账户”并确认，然后重启一次。 | 状态为 `UNBOUND`、不自动连接；Credential Manager 条目、profile 和 SDK token 已删除；`orders.db` 仍保留。 | ☐ PASS ☐ FAIL ☐ BLOCKED |
| AB-05 | 1 分钟 | 重新绑定 Sandbox 账户后，在 PowerShell 运行下方“凭据泄漏扫描”。 | 最终只显示 `Credential leak scan passed`；安装目录和 `%LOCALAPPDATA%\FastOrder` 均无 App Key、App Secret、完整 Account ID 明文。 | ☐ PASS ☐ FAIL ☐ BLOCKED |
| AB-06 | 2 分钟 | 前台观察 AAPL 行情、购买力和持仓；执行 `AAPL → MSFT → AAPL`。将页面切到后台再返回。 | 行情持续更新；持仓六项可读；切换时旧报价清空且禁单；后台禁单，回前台后行情/持仓恢复。 | ☐ PASS ☐ FAIL ☐ BLOCKED |
| AB-07 | 1 分钟 | 明确确认使用 Sandbox 后，开启交易开关，提交 1 股、DAY/CORE、低于 best bid 的非市价化 BUY LIMIT。 | 只生成一个 client order ID；订单事件到达；随后撤单成功，Broker 端与本地账本一致。 | ☐ PASS ☐ FAIL ☐ BLOCKED |
| AB-08 | 1 分钟 | 再建同类 1 股限价单，并对提交按钮快速双击一次。 | 本地账本和 Broker 端都只有一笔订单，没有重复提交；随后撤单。 | ☐ PASS ☐ FAIL ☐ BLOCKED |
| AB-09 | 1 分钟 | 再建一笔未决限价单，正常退出并重启。 | 使用原 client order ID 恢复/查询，Broker 端没有第二笔订单；验证后撤单。 | ☐ PASS ☐ FAIL ☐ BLOCKED |
| AB-10 | 2 分钟 | 使用隔离 VM/受控网络，在一次 Sandbox 提交发出后立即断网，制造结果不确定；随后恢复网络。 | 状态进入 `UNKNOWN`；恢复后只查询原 client order ID，绝不自动重发；若原单活动则撤单。无法可靠制造时记 `BLOCKED`，不得猜测 PASS。 | ☐ PASS ☐ FAIL ☐ BLOCKED |
| AB-11 | 1 分钟 | 断网期间观察健康状态；另将页面置于后台。 | `MARKET_READY=false` 或 `ORDER_EVENTS_READY=false` 时、以及页面后台时，BUY/SELL 都被禁用；恢复全部健康条件后才能重新启用。 | ☐ PASS ☐ FAIL ☐ BLOCKED |
| AB-12 | 1 分钟 | 检查 Broker Sandbox 订单列表，清理所有未决订单并整理脱敏证据。 | 没有活动订单、没有 LIVE 请求；第二人能凭证据复核 AB-01～AB-11。 | ☐ PASS ☐ FAIL ☐ BLOCKED |

## 凭据泄漏扫描命令

在项目目录执行；三个值均为隐藏输入，脚本只报告命中路径和规则，不会打印输入值：

```powershell
.\.venv\Scripts\python.exe .\release\scan_release_credentials.py `
  "C:\Program Files\FastOrder" `
  "$env:LOCALAPPDATA\FastOrder" `
  --exact-credentials
```

若本轮直接运行未安装的 Sandbox standalone，把第一个路径替换为：

```text
build\windows-x64-sandbox-qa\FastOrder.dist
```

## 最小证据包

- AB-01/02：绑定成功及重启自动连接截图；只显示脱敏账户。
- AB-04/05：忘记后 `UNBOUND` 截图、泄漏扫描 PASS 文本。
- AB-06：AAPL/MSFT 切换与前后台恢复截图。
- AB-07～10：Broker 端脱敏订单列表，证明撤单、双击单笔、重启同 ID、UNKNOWN 不重发。
- AB-11/12：健康门禁截图及最终“无未决订单”截图。

禁止保存 App Secret、完整 Account ID、SDK token、Authorization header、Credential Manager 导出、数据库、完整日志或未脱敏 Broker 响应。

## Gate 结论

- AB-01～AB-05 为 `PASS`：Gate A=`PASS`。Gate B 按 V1.0 负责人决定记录 `WAIVED`，并另附 `LIVE_MANUAL_QA=PASS`。
- 任一步 `FAIL`：Gate A/B 至少一个为 `FAIL`，停止后续 Gate。
- 任一步 `BLOCKED`：对应 Gate 保持 `BLOCKED`，不得进入 Gate C。

操作者：__________　第二复核人：__________　日期/时区：__________

Sandbox EXE SHA256：________________________________________________

策略 SHA256：________________________________________________________
