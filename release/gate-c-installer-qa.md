# Gate C — Installer QA checklist

本 Gate 验证 Windows x64 安装器、快捷方式、修复/升级、互斥锁、非管理员运行和卸载数据边界。只做安装生命周期验证，不绑定 LIVE 账户、不提交任何 LIVE 或 Sandbox 订单；涉及应用启动时使用无凭据状态或独立 Sandbox QA 制品。

## 记录规则

每个步骤只能标记 `PASS`、`FAIL` 或 `BLOCKED`。安装器退出码、实际路径、版本和文件 hash 必须有证据；无法访问干净 VM、Inno Setup 或商用许可证记录时标记 `BLOCKED`，不得以开发机截图代替。

Gate C 只有所有必需步骤为 `PASS`，且 `installerQa` 证据为 `PASS`，才能判定 `PASS`。

## 前置条件

- [ ] Gate A 已实际 `PASS`；Gate B 为 `PASS`，或具有项目负责人批准的 `WAIVED` 记录并附 `LIVE_MANUAL_QA=PASS`。安装器测试使用恢复到干净快照的专用一次性 VM。
- [ ] 已有完整 standalone staging tree；Gate C 的 `InternalUnsignedRc` 允许 EXE/安装器未签名，但必须明确标记内部候选，且 staging tree 不含源码、秘密或运行数据。
- [ ] 使用 x64 Windows 10 22H2 或更新版本；准备管理员安装凭据和普通非管理员运行账号。
- [ ] 已确认 Inno Setup 7.1.0 x64、固定 AppId `{5E1CC7C3-2A6F-4E71-9C2A-6F1BBE4D6D0A}` 和目标 `Program Files\FastOrder`。本 Gate 只允许 `UNSIGNED_INTERNAL_RC`；商业许可最迟必须在 Gate D 正式生产构建前购买并记录。
- [ ] 对 `%LOCALAPPDATA%\FastOrder` 和 `极速下单.WebullOpenAPI` 建立仅含文件名/条目名的受保护前状态；不导出原始用户数据。

## 步骤

| ID | 人工操作与预期结果 | 状态 | 证据引用/备注 |
|---|---|---|---|
| C-INS-001 | 以 `InternalUnsignedRc` 构建并核对文件名 `FastOrder-Setup-1.0.0-rc1.exe`；evidence 明确标记 `UNSIGNED_INTERNAL_RC`，安装器与 staging tree 的 commit/hash 已关联。 |  |  |
| C-INS-002 | 审查 staging tree 和安装包内容：包含 `FastOrder.exe`、`static`、`config`、`FastOrder.ico`、必需运行时数据和许可证；不包含源码、开发机绝对路径、凭据、token、数据库或日志。 |  |  |
| C-INS-003 | 内部 RC1 的 Authenticode 状态必须为 `NotSigned`、Publisher 为 `FastOrder Internal Candidate`、`SignedUninstaller=no`；不得把它标记为正式发布。`FormalSigned` 的 Valid 签名验证留到 Gate D。 |  |  |
| C-INS-004 | 在干净 VM 运行安装器：管理员确认后安装到 `C:\Program Files\FastOrder`，安装器和应用退出码为 0；开始菜单快捷方式存在，选择桌面任务时桌面快捷方式存在。 |  |  |
| C-INS-005 | 以普通非管理员用户启动已安装应用并访问本机页面；确认不需要写入 Program Files，不因权限不足崩溃，不自动打开或提交订单，且只监听本机。 |  |  |
| C-INS-006 | 运行同版本 repair/再次安装：安装成功，AppId、安装路径、快捷方式和版本不变；预置的 `%LOCALAPPDATA%\FastOrder` sentinel、脱敏 profile、订单账本和 Credential Manager 条目保持不变。 |  |  |
| C-INS-007 | 应用运行期间尝试 repair/覆盖：`AppMutex=Local\FastOrder.V1` 生效，安装器安全失败或等待，不覆盖运行中的文件；关闭应用后再次 repair 成功。 |  |  |
| C-INS-008 | 先安装仅供生命周期测试的同 AppId `0.9.9-qa`，再覆盖升级到 RC1；另验证更高版本不能被 RC1 降级。用户数据、Credential Manager、profile、token 和订单账本必须保留，且 QA fixture 不进入发布目录。 |  |  |
| C-INS-009 | 卸载安装器：二进制、安装目录和快捷方式被移除；`%LOCALAPPDATA%\FastOrder`、Credential Manager 服务 `极速下单.WebullOpenAPI` 和其用户数据保持存在。不得运行会删除用户数据的额外清理命令。 |  |  |
| C-INS-010 | 卸载后检查系统：无意外目录/快捷方式被删除；重新安装后应用版本、图标、OriginalFilename 和保留数据可读取。执行一次 Windows 重启，再从快捷方式启动并通过 `/livez`。 |  |  |
| C-INS-011 | 第二人复核安装/repair/升级/卸载记录、文件 hash、快捷方式和数据保留 sentinel；确认整个 Gate 没有绑定 LIVE 或提交任何订单。 |  |  |

## 脱敏证据规范

每个证据文件名必须包含 Gate 和步骤 ID，例如 `C-INS-009-PASS-20260905.txt`；记录时间（含时区）、VM/OS 版本、安装器版本、安装器 hash、安装路径、退出码和结果。可保留文件名、版本、签名状态和 hash，不保留用户数据内容。

- 用户名、机器名、完整路径中的个人目录、证书私钥、App Key/App Secret、token、Account ID、订单标识和 Credential Manager 值全部替换为 `[REDACTED]`；账户或条目需要关联时只保留末 4 位。
- 截图不得显示桌面上的 `.env`、终端历史、密码管理器、浏览器凭据或完整用户数据；安装日志应按行裁剪并脱敏。不得附加整个 `%LOCALAPPDATA%\FastOrder`、数据库、token 目录或原始安装日志。
- 证据存放在访问受控目录，不提交 Git；发现安装包含秘密或卸载删除用户数据时立即标记 `FAIL` 并保留最小化诊断证据。

## Gate 决策

- Gate C：`PASS` / `FAIL` / `BLOCKED`
- QA 操作者：
- 第二人复核：
- VM/OS：
- 安装器 SHA-256：
- `installerQa` 证据引用：
- 未决风险/后续动作：
