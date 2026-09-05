# FastOrder 1.0.0 Windows release gate

- [ ] Build on x64 Windows 10 22H2 or newer with pinned Python/dependencies.
- [ ] Nuitka standalone staging tree contains `FastOrder.exe`, static assets, and `FastOrder.ico`; no `.py`, `.env`, `runtime`, `logs`, tokens, databases, or secrets.
- [ ] Compile `packaging/FastOrder.iss` with Inno Setup 7.1 x64; verify stable AppId and `Program Files/FastOrder` target.
- [ ] Verify desktop and Start Menu shortcuts; run as a non-admin user after installation.
- [ ] Verify the application mutex blocks overwrite while FastOrder is running.
- [ ] Verify formal production policy is LIVE fail-closed; Sandbox QA uses its separate build directory and policy.
- [ ] Verify real Sandbox connection, quote, non-marketable LIMIT order, order event, and cancel evidence.
- [ ] Verify reinstall and upgrade preserve `%LOCALAPPDATA%\FastOrder`, order ledger, tokens, profile, and Credential Manager entries.
- [ ] Verify uninstall removes only binaries/shortcuts; it must never delete `%LOCALAPPDATA%\FastOrder` or Credential Manager entries.
- [ ] Record proof of a valid Inno Setup commercial license before commercial release.
- [ ] Verify EXE and installer ProductVersion/FileVersion/OriginalFilename/icon metadata.
- [ ] Sign EXE and installer with Authenticode SHA-256 plus RFC3161 timestamp; verify signer and chain on a clean VM.
- [ ] Run `Write-SHA256.ps1` and `New-BuildManifest.ps1`; independently verify hashes and source commit.
- [ ] Second-person review approves the manifest, Sandbox evidence, signing output, and release artifacts.
