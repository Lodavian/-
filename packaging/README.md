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

## Signed installer

After the standalone candidate passes automated and Broker Sandbox QA, sign
`FastOrder.exe`, rerun `smoke_test_exe.py`, then build the installer:

```powershell
./packaging/build_installer.ps1 `
  -StagingDir ./build/windows-x64/FastOrder.dist `
  -Publisher "<certificate subject company>" `
  -CertificateThumbprint "<thumbprint>" `
  -TimestampUrl "https://<RFC3161 service>" `
  -CommercialLicenseConfirmed
```

Formal release is blocked until Authenticode, the Inno Setup commercial-license
record, packaged QA, Sandbox closure, and installer lifecycle QA all have real
evidence. `release/evidence-template.json` deliberately cannot pass the manifest
gate.

The installer never owns or deletes `%LOCALAPPDATA%\FastOrder` or the retained
Credential Manager service `极速下单.WebullOpenAPI`.
