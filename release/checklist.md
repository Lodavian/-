# FastOrder 1.0.0 Windows Release Qualification

本页是发布总门禁。V1.0 经项目负责人批准，不再把 Sandbox QA 作为正式门禁；Gate B 必须记录为 `WAIVED`，并以脱敏的 `LIVE_MANUAL_QA=PASS` 负责人证明替代，严禁伪造 Sandbox PASS。

## Gate checklist 索引

实际执行 Gate A/B 时优先使用合并后的 [10～15 分钟人工验收单](gate-ab-quick-qa.md)；原 Gate A、Gate B 文档保留为详细说明和异常排查依据。

| Gate | 人工清单 | 必须满足的证据门禁 | 状态 |
|---|---|---|---|
| A — Credential QA | [Gate A/B 快速单](gate-ab-quick-qa.md)；[详细说明](gate-a-credential-qa.md) | `AB-01` 至 `AB-05` 全部 `PASS` | `NOT_RUN` |
| B — Sandbox QA | [负责人豁免；原详细说明保留](gate-b-sandbox-qa.md) | `WAIVED` + `sandboxQaWaiver=APPROVED` + `liveManualQa=PASS` | `WAIVED` |
| C — installer QA | [gate-c-installer-qa.md](gate-c-installer-qa.md) | `C-INS-001` 至 `C-INS-011` 全部 `PASS` | `BLOCKED`（待有效 Gate B 证据） |
| D — signing QA | [gate-d-signing-qa.md](gate-d-signing-qa.md) | `D-SIGN-001` 至 `D-SIGN-008` 全部 `PASS` | `BLOCKED`（待合法 Publisher/正式证书） |

## 自动化与正式发布前置项

生命周期证据索引必须明确覆盖 `reinstall`、`upgrade`、`uninstall`，并记录 `SHA256` 与 `Authenticode` 验证结论。

- [ ] 在 x64 Windows 10 22H2 或更新版本，用锁定的 Python/依赖和预置 wheelhouse 完成正式构建；`sourceTests=PASS`。
- [ ] standalone staging tree 含 `FastOrder.exe`、静态资源、配置、图标和必需运行时数据；不含 `.py`、`.env`、`runtime`、`logs`、tokens、数据库、测试文件、秘密或开发机绝对路径；`packagedSmoke=PASS`。
- [ ] 正式制品嵌入 LIVE fail-closed 生产策略；Sandbox QA 始终使用独立构建目录和 `packaging/sandbox-qa-policy.json`，不能混用。
- [ ] Inno Setup 7.1 x64 编译成功，稳定 AppId 和 `Program Files\FastOrder` 目标已核对；正式生产安装器构建前保存有效商业许可记录。
- [ ] `Write-SHA256.ps1`、`New-BuildManifest.ps1` 和独立 hash 校验完成；manifest 记录源 commit/tree、输入 hash、artifact hash、签名和所有 Gate 证据。
- [ ] 第二人批准四个 Gate、manifest、Sandbox broker-side 连接/撤单证据、签名输出和最终发布制品。

## 状态与证据规则

每个步骤只能是 `PASS`、`FAIL` 或 `BLOCKED`。任何 `FAIL` 或 `BLOCKED` 都是 release no-go；不能用未执行、模拟或未脱敏材料代替 PASS。四个 Gate 和自动化前置项全部 PASS 后，发布负责人才能把正式发布结论标记为 `GO`。

证据必须包含 Gate/步骤 ID、时间（含时区）、操作者、OS/build、产品版本、环境、结果、退出码或 broker-side 状态及必要的 SHA-256。Account ID、profile/order/client ID 只保留末 4 位；App Key、App Secret、token、cookie、密码、手机号、Authorization 头、私钥、完整 `.env`、`runtime/`、数据库、原始网络包和未裁剪截图均禁止进入证据。证据存放在访问受控目录，不提交 Git；发现泄漏立即标记 `FAIL` 并撤销/重置受影响凭据。

发布结论：`GO` / `NO-GO`

发布负责人：

日期（含时区）：

证据包索引：
