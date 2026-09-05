# 极速下单

一个完全独立、只监听本机的 Webull HK OpenAPI 美股单票交易网页。界面使用原生 HTML/CSS/JavaScript，后端使用 Python、aiohttp 和 Webull 官方 SDK。

> **重要：** 实盘模式必须同时设置 `FAST_ORDER_SANDBOX=false`、`FAST_ORDER_LIVE_ALLOWED=true`，并填写全部实盘风险上限。买入和卖出按钮会在单击后直接提交实盘订单，没有二次确认。

## 首次运行

1. 在 PowerShell 中运行：

```powershell
.\run.ps1
```

脚本会在本项目内创建 `.venv`、安装固定依赖并打开 `http://127.0.0.1:8765`。首次打开后点击“绑定账户”，输入 Webull OpenAPI App Key 和 App Secret；不要输入 Webull 手机号或交易密码。

凭据验证成功后，单账户会自动选择，多账户必须从脱敏候选中手动选择。勾选“记住凭据”时，App Key、App Secret 和完整 Account ID 只写入 Windows Credential Manager；`runtime/account_profile.json` 只保存脱敏显示信息。未勾选时凭据只在本次运行的内存中使用。

`.env` 继续作为开发者和高级部署后备。页面安全凭据优先于 `.env`，但本轮仍由 `.env` 管理 Sandbox/LIVE 环境以及数量、金额等风控上限。

使用 `.env` 后备且凭据关联多个账户时，可设置掩码形式的 `FAST_ORDER_WEBULL_ACCOUNT_HINT`；也可设置完整 `FAST_ORDER_WEBULL_ACCOUNT_ID`。Webull SDK 访问令牌仍保存在 `runtime/webull_tokens/`，目录会在连接前收紧为当前 Windows 用户权限。SDK 要求该令牌采用其原生文件格式，因此它是权限保护而非加密存储。

“断开连接”会保留安全凭据和 SDK 会话；“忘记此账户”会先禁用交易并停止行情/订单事件，再删除 Credential Manager 条目、脱敏元数据和 SDK token。

## 实盘门禁与安全维护

LIVE 模式除了显式设置 `FAST_ORDER_LIVE_ALLOWED=true`，还必须设置以下四个上限，缺少任何一项都会拒绝启动：

- `FAST_ORDER_MAX_MARKET_QTY`：单笔市价单最大股数。
- `FAST_ORDER_MAX_MARKET_ESTIMATED_VALUE`：市价单按最新有效行情计算的估算金额上限。
- `FAST_ORDER_MAX_DAILY_GROSS_NOTIONAL`：当日累计买卖成交名义金额上限。
- `FAST_ORDER_MAX_OPEN_ORDERS`：最多同时存在的未完成订单数。

所有上限和网络 deadline 都会严格校验，不能使用负数、零、NaN、Infinity 或超出合理范围的值。订单网络超时不会自动重发；应用会先按原 `client_order_id` 对账，以避免重复成交。

如果 App Secret、访问令牌或日志意外暴露，应立即在 Webull OpenAPI 管理后台撤销/重置对应凭据，重新生成 App Secret，并删除本项目内的旧令牌与日志后再启动。不要把 `.env`、`runtime/`、`logs/` 或 `*.db` 复制给其他人。

本项目按要求保留“裸市价单”且不调用 Preview：市价单最终成交价可能因滑点高于本地估算金额，因此 `FAST_ORDER_MAX_MARKET_ESTIMATED_VALUE` 只是提交前的估算门禁，不是成交价保证。上线前必须由操作者明确接受该剩余风险。

页面处于前台时，实时推送静默约 3 秒会自动用 REST 快照和一档买卖盘校验行情，避免低活跃股票被误判为过期。切换到其他标签页或最小化后会暂停当前股票订阅和恢复请求；回到前台时自动重新订阅并立即恢复行情。后台期间下单保持禁用。

## 固定交易规则

- 仅美股整股；任一时刻只订阅一只股票。
- 仅 BUY/SELL、MARKET/LIMIT、DAY、CORE 常规时段。
- SELL 不允许超过当前可卖持仓，不提供做空。
- 行情断线、行情过期、股票切换或账户离线时禁止下单。
- 每个请求使用唯一 `client_order_id`；提交结果不确定时先查询订单，绝不自动重发。
- 最大数量和最大金额由 `.env` 配置，但不能从网页绕过。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

测试全部使用模拟网关，不会访问 Webull 或提交订单。
