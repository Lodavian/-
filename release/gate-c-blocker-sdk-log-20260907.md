# Gate C Release Blocker — Webull 行情 SDK 安装目录日志

- 发现日期：2026-09-07（Asia/Hong_Kong）
- 发现环境：全新 Windows 11，安装版 FastOrder，普通用户
- 状态：`BLOCKED — FIX IMPLEMENTED, CLEAN-MACHINE RETEST REQUIRED`
- 影响：行情线程无法启动，账户连接/重启恢复失败；正式发布 `NO-GO`

## 现场现象

`Async-Quotes-Client` 在启动时反复尝试创建：

```text
C:\Program Files\FastOrder\webull_data_streaming_sdk.log
PermissionError: [Errno 13] Permission denied
```

现场截图作为外部受控证据保留，不提交仓库；本记录不包含凭据、Account ID、Token 或券商响应。

## 根因

锁定依赖 `webull-openapi-python-sdk==2.0.18` 的 Data Streaming 实现提供
`connect_and_loop_start(..., logger_enable=False, customer_logger=None)`，但其
`connect_and_loop_async` 建立线程时只把 `timeout` 传给
`connect_and_loop_forever`。后两个参数被丢弃，`logger_enable` 在行情线程中
恢复默认 `True`，随后 SDK 用相对路径创建
`webull_data_streaming_sdk.log`。安装器正确地保持 Program Files 对普通用户
只读，因此触发 `PermissionError`。

## 最小修复

不修改第三方 SDK 文件，不改变 SDK 版本，不提升权限，不放宽安装目录 ACL。
FastOrder 在唯一 Gateway 内使用一个薄的 DataStreamingClient 子类适配器，
覆盖公开的 `connect_and_loop_forever` 入口并强制向原 SDK 传递
`logger_enable=False`。应用自身经过脱敏和轮转的日志仍写入
`%LOCALAPPDATA%\FastOrder\logs\fast_order.log`。

## 关闭条件

- 全部源码测试、Nuitka LIVE standalone、真实 EXE smoke 和 RC installer 重建通过。
- 从只读安装目录以普通用户启动，不在 Program Files 生成任何日志。
- 行情客户端可启动；`/livez` 正常，未满足交易条件时 `/readyz` 继续 fail-closed。
- 全新 Windows 11 测试机卸载旧 RC、安装新 RC、连接、关闭、再次启动后行情恢复。
- 更新 Gate C 脱敏 evidence；在完成上述干净机复测前不得标记 Gate C PASS。
