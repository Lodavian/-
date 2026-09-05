[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$InstallerPath,
    [string]$PythonPath = "python.exe",
    [string]$InstallDir = "$env:ProgramFiles\FastOrder"
)
$ErrorActionPreference = "Stop"
$setup = (Resolve-Path -LiteralPath $InstallerPath).Path
$signature = Get-AuthenticodeSignature -LiteralPath $setup
if ($signature.Status -ne "Valid") { throw "Installer Authenticode signature is not valid." }
& $setup /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
if ($LASTEXITCODE -ne 0) { throw "First installation failed." }
$exe = Join-Path $InstallDir "FastOrder.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "Installed FastOrder.exe is missing." }
& $PythonPath (Join-Path $PSScriptRoot "smoke_test_exe.py") --exe $exe --ui-manifest (Join-Path $PSScriptRoot "ui-hashes.json")
if ($LASTEXITCODE -ne 0) { throw "Installed executable smoke failed." }
& $setup /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
if ($LASTEXITCODE -ne 0) { throw "Same-version repair failed." }
$dataRoot = Join-Path $env:LOCALAPPDATA "FastOrder"
$sentinel = Join-Path $dataRoot "installer-data-preservation.sentinel"
New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
[IO.File]::WriteAllText($sentinel, "preserve", [Text.UTF8Encoding]::new($false))
$uninstaller = Join-Path $InstallDir "unins000.exe"
if (-not (Test-Path -LiteralPath $uninstaller)) { throw "Uninstaller is missing." }
& $uninstaller /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
if ($LASTEXITCODE -ne 0) { throw "Uninstall failed." }
if (-not (Test-Path -LiteralPath $sentinel)) { throw "Uninstaller deleted LocalAppData user data." }
Write-Output "Signed installer install/repair/uninstall smoke passed; user-data sentinel retained at $sentinel"
