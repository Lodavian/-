# Gate D — Signing QA checklist

本 Gate 验证 EXE 和安装器的 Authenticode 签名、SHA-256 摘要、RFC 3161 时间戳、发布人、证书链、PE 元数据和最终 manifest。只验证制品与签名，不连接 LIVE、不提交任何订单。

## 记录规则

每个步骤只能标记 `PASS`、`FAIL` 或 `BLOCKED`。证书链、时间戳、签名者或最终 hash 任一不符合预期即为 `FAIL`；签名服务、硬件密钥、Windows SDK 或干净 VM 不可用则为 `BLOCKED`。不得通过重新签名后遗漏重跑验证来判定 PASS。

Gate D 只有所有必需步骤为 `PASS`，且 `signatureVerification` 证据为 `PASS`，才能判定 `PASS`。

## 前置条件

- [ ] 准备受控代码签名证书及私钥访问授权；证书具备 Code Signing EKU，未过期、未吊销，发布人名称与候选元数据一致。
- [ ] 记录证书 thumbprint（可公开记录）和发布人；私钥、PIN、密钥文件和签名服务凭据不得进入证据或仓库。
- [ ] 安装匹配的 Windows SDK `signtool.exe`，准备 HTTPS RFC 3161 时间戳服务和干净 Windows x64 VM。
- [ ] 取得未被后处理改写的 standalone `FastOrder.exe`、installer `FastOrder-Setup-1.0.0.exe`、源 commit/tree、UI hash、策略 hash 和 evidence JSON。

## 步骤

| ID | 人工操作与预期结果 | 状态 | 证据引用/备注 |
|---|---|---|---|
| D-SIGN-001 | 在受控签名主机确认证书 subject、thumbprint、Code Signing EKU、有效期和链状态；与发布人/批准记录一致，私钥不离开受控签名流程。 |  |  |
| D-SIGN-002 | 先使用 `release/Sign-Release.ps1` 对 `FastOrder.exe` 签名，再以 `FormalSigned` 构建安装器并由 Inno SignTool 签署 setup/uninstaller；两处均使用 `/fd SHA256`、`/tr https://...`、`/td SHA256`。 |  |  |
| D-SIGN-003 | 对两个制品运行 `signtool verify /pa /all /v` 及 `Get-AuthenticodeSignature`；必须为 `Valid`，签名者 thumbprint 匹配，且存在可验证的 RFC 3161 timestamp certificate。 |  |  |
| D-SIGN-004 | 核对 PE/安装器元数据：ProductVersion `1.0.0`、FileVersion `1.0.0.0`、OriginalFilename `FastOrder.exe`（EXE）/安装器文件名、Publisher/CompanyName 和 `FastOrder.ico` 图标正确。 |  |  |
| D-SIGN-005 | 在干净 VM 重新验证证书链、时间戳和签名状态；安装器可通过验证并提示预期发布人，未出现未知发布人、链错误或签名损坏。整个过程不绑定账户、不提交订单。 |  |  |
| D-SIGN-006 | 签名完成后冻结制品；运行 `Write-SHA256.ps1`、独立 hash 校验和 `New-BuildManifest.ps1`，确认 manifest 的 source commit/tree、输入 hash、artifact hash、签名者和时间戳与实际一致。 |  |  |
| D-SIGN-007 | 对签名后的 EXE/安装器再次执行 artifact、安装器生命周期和证据门禁检查；不得在生成 manifest 后替换、压缩、修复或重新打包文件。 |  |  |
| D-SIGN-008 | 第二人独立读取签名输出、证书链、timestamp、最终 hash 和 manifest，复核 thumbprint 与审批记录相符，并签署发布批准。 |  |  |

## 脱敏证据规范

每个证据文件名必须包含 Gate 和步骤 ID，例如 `D-SIGN-003-PASS-20260905.txt`；记录时间（含时区）、签名主机/VM（以 `[HOST]` 表示）、制品文件名、版本、文件 SHA-256、签名状态、证书 subject（可保留公司名）、thumbprint 和时间戳状态。

- 私钥、PFX、PIN、硬件令牌序列号、签名服务访问令牌、App Key/App Secret、Account ID、token、cookie 和 Authorization 头必须替换为 `[REDACTED]`。证书 thumbprint 和公共链信息可保留，但不要附带私钥材料。
- `signtool /v` 输出只保留验证结论、发布人、thumbprint、链/时间戳状态和退出码；删除个人路径、用户名、网络地址和未脱敏环境变量。不要上传整个证书存储、私钥文件或原始 CI 环境。
- 证据存放在访问受控目录，不提交 Git；任何签名或 hash 不一致均不得以截图覆盖，必须标记 `FAIL` 并重新生成完整签名证据。

## Gate 决策

- Gate D：`PASS` / `FAIL` / `BLOCKED`
- 签名操作者：
- 第二人复核：
- 发布人/证书 thumbprint：
- EXE SHA-256：
- 安装器 SHA-256：
- `signatureVerification` 证据引用：
- 未决风险/后续动作：
