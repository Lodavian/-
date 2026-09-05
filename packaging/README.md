# FastOrder V1.0 Windows build

This directory builds a Windows x64 Nuitka **standalone** candidate. It never
downloads dependencies during a release build and never packages project-local
runtime data.

## Immutable inputs

- CPython 3.11.9 x64
- Nuitka 4.2
- MSVC 14.3 / Visual Studio 2022 Build Tools and a recorded Windows SDK
- the exact versions in `requirements.txt`
- a pre-provisioned `packaging/wheelhouse/` whose every input is listed in
  `wheelhouse-manifest.json`

The build aborts if Git is dirty, any baseline check fails, the UI hashes differ,
the compiler is unavailable, or an artifact contains forbidden files, secrets,
or the developer source path.

## Candidate build

Run from an x64 VS 2022 Native Tools PowerShell:

```powershell
./packaging/build_windows.ps1 -Clean -CompanyName "<certificate subject company>"
```

Use `-SandboxQa` to create the separate internal Sandbox QA directory. The
formal build always embeds `config/production.json` and ignores `.env` and
credential environment variables.

## Installer lanes

Both installer lanes require an explicit commercial Inno Setup license
confirmation.  The internal lane is unsigned and is only for release-candidate
QA; it has a fixed `FastOrder Internal Candidate` publisher and always emits
`FastOrder-Setup-1.0.0-rc1.exe`:

```powershell
./packaging/build_installer.ps1 `
  -StagingDir ./build/windows-x64-internal/FastOrder.dist `
  -EvidenceJson ./release/evidence-rc1.json `
  -InternalUnsignedRc `
  -CommercialLicenseConfirmed
```

After the standalone candidate passes automated and Broker Sandbox QA, sign
`FastOrder.exe`, rerun `smoke_test_exe.py`, then build the formal installer.
Formal mode requires the legal publisher, matching Authenticode certificate,
and an HTTPS RFC 3161 timestamp service:

```powershell
./packaging/build_installer.ps1 `
  -StagingDir ./build/windows-x64/FastOrder.dist `
  -EvidenceJson ./release/evidence-final.json `
  -FormalSigned `
  -Publisher "<certificate subject company>" `
  -CertificateThumbprint "<thumbprint>" `
  -TimestampUrl "https://<RFC3161 service>" `
  -CommercialLicenseConfirmed
```

`InternalUnsignedRc` and `FormalSigned` are mutually exclusive.  The formal
installer signs both setup and uninstaller (`SignedUninstaller=yes`); the RC
installer has no signing tool and sets `SignedUninstaller=no`.

Run the full lifecycle smoke with a separately built `0.9.9-qa` installer as
the upgrade input:

```powershell
./packaging/smoke_test_installer.ps1 `
  -InstallerPath ./release/artifacts/FastOrder-Setup-1.0.0-rc1.exe `
  -Mode InternalUnsignedRc `
  -DisposableVmConfirmed `
  -PreviousInstallerPath ./build/installer-qa-fixtures/FastOrder-Setup-0.9.9-qa.exe
```

The internal installer build creates that `0.9.9-qa` predecessor from the same
staging tree and stable AppId solely to exercise upgrade mechanics. It is emitted
under `build/installer-qa-fixtures`, must remain unsigned, and must never be
copied into release artifacts.

The smoke is isolated to a temporary install data root and verifies the
installed-file whitelist, UI hashes, shortcuts, launch/restart, repair,
upgrade, running-process overwrite rejection, uninstall/reinstall, and
`LOCALAPPDATA` retention. It only performs health/UI/WebSocket reads and never
submits an order.

Formal release is blocked until Authenticode, the Inno Setup commercial-license
record, packaged QA, Sandbox closure, and installer lifecycle QA all have real
evidence. `release/evidence-template.json` deliberately cannot pass the manifest
gate.

The installer never owns or deletes `%LOCALAPPDATA%\FastOrder` or the retained
Credential Manager service `极速下单.WebullOpenAPI`.
