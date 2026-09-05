param(
    [switch]$Clean,
    [switch]$SandboxQa,
    [switch]$InternalUnsignedCandidate,
    [switch]$AllowToolDownloads,
    [string]$PythonPath = $env:FAST_ORDER_PYTHON,
    [string]$CompanyName
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PackagingRoot = Join-Path $ProjectRoot "packaging"
$OutputName = if ($SandboxQa) { "windows-x64-sandbox-qa" } else { "windows-x64" }
$OutputRoot = Join-Path $ProjectRoot "build\$OutputName"
$DistRoot = Join-Path $OutputRoot "FastOrder.dist"
$BuildVenv = Join-Path $ProjectRoot "build\.venv-windows311"
$Wheelhouse = Join-Path $PackagingRoot "wheelhouse"
$WheelManifestPath = Join-Path $PackagingRoot "wheelhouse-manifest.json"
$ReportPath = Join-Path $OutputRoot "compilation-report.xml"
$ManifestPath = Join-Path $OutputRoot "artifact-manifest.json"
$NuitkaWorkRoot = Join-Path $env:TEMP "FastOrderBuild\$OutputName"
$NuitkaDistRoot = Join-Path $NuitkaWorkRoot "FastOrder.dist"
$NuitkaReportPath = Join-Path $NuitkaWorkRoot "compilation-report.xml"
$SelectedPolicy = if ($SandboxQa) { Join-Path $PackagingRoot "sandbox-qa-policy.json" } else { Join-Path $ProjectRoot "config\production.json" }
$env:NUITKA_CACHE_DIR = Join-Path $ProjectRoot "build\nuitka-cache"

function Fail([string]$Message) { throw "FastOrder build preflight failed: $Message" }

function Test-NuitkaToolCache([switch]$RequirePresent) {
    $toolManifestPath = Join-Path $PackagingRoot "nuitka-tool-manifest.json"
    try { $toolManifest = Get-Content -LiteralPath $toolManifestPath -Raw | ConvertFrom-Json }
    catch { Fail "Invalid Nuitka tool hash manifest: $toolManifestPath" }
    foreach ($property in $toolManifest.files.PSObject.Properties) {
        $toolPath = Join-Path $env:NUITKA_CACHE_DIR $property.Name
        if (-not (Test-Path -LiteralPath $toolPath)) {
            if ($RequirePresent) { Fail "Nuitka tool cache is missing: $($property.Name)" }
            continue
        }
        $actual = (Get-FileHash -LiteralPath $toolPath -Algorithm SHA256).Hash
        if ($actual -ne [string]$property.Value) { Fail "Nuitka tool hash mismatch: $($property.Name)" }
    }
}

if (-not $InternalUnsignedCandidate -and -not $CompanyName) {
    Fail "A legal CompanyName is required for a formal candidate. Use -InternalUnsignedCandidate only for unsigned internal QA."
}
if ($InternalUnsignedCandidate -and -not $CompanyName) {
    $CompanyName = "FastOrder Internal Candidate"
}
if ($InternalUnsignedCandidate -and -not $SandboxQa) {
    $OutputName = "windows-x64-internal"
    $OutputRoot = Join-Path $ProjectRoot "build\$OutputName"
    $DistRoot = Join-Path $OutputRoot "FastOrder.dist"
    $ReportPath = Join-Path $OutputRoot "compilation-report.xml"
    $ManifestPath = Join-Path $OutputRoot "artifact-manifest.json"
    $NuitkaWorkRoot = Join-Path $env:TEMP "FastOrderBuild\$OutputName"
    $NuitkaDistRoot = Join-Path $NuitkaWorkRoot "FastOrder.dist"
    $NuitkaReportPath = Join-Path $NuitkaWorkRoot "compilation-report.xml"
    $env:NUITKA_CACHE_DIR = Join-Path $ProjectRoot "build\nuitka-cache"
}
Test-NuitkaToolCache -RequirePresent:(-not $AllowToolDownloads)

$GitState = @(& git -C $ProjectRoot status --porcelain --untracked-files=all)
if ($LASTEXITCODE -ne 0) { Fail "Source root is not a Git repository." }
if ($GitState.Count -ne 0) { Fail "Git working tree must be completely clean before a release build." }

if (-not $PythonPath) {
    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    $fixedPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
    if (Test-Path -LiteralPath $venvPython) { $PythonPath = $venvPython }
    elseif (Test-Path -LiteralPath $fixedPython) { $PythonPath = $fixedPython }
    else { Fail "Preinstalled Python 3.11.9 x64 is required; network bootstrap is disabled." }
}
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path

$PythonInfo = & $PythonPath -c 'import platform,struct,sys; print(sys.version_info[:3]); print(struct.calcsize(chr(80))*8); print(platform.machine())'
if ($LASTEXITCODE -ne 0) { Fail "Unable to execute Python: $PythonPath" }
if ($PythonInfo[0] -notlike "(3, 11, 9)*") { Fail "Python 3.11.9 is required; got: $($PythonInfo[0])" }
if ($PythonInfo[1] -ne "64") { Fail "64-bit Python is required; got: $($PythonInfo[1])" }

if (-not (Test-Path -LiteralPath $BuildVenv)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $BuildVenv) | Out-Null
    & $PythonPath -m venv $BuildVenv
    if ($LASTEXITCODE -ne 0) { Fail "Unable to create isolated build venv." }
}
$BuildPython = Join-Path $BuildVenv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $BuildPython)) { Fail "The isolated build venv has no Python." }
$BuildPythonInfo = & $BuildPython -c 'import struct,sys; print(sys.version_info[:3]); print(struct.calcsize(chr(80))*8)'
if ($BuildPythonInfo[0] -notlike "(3, 11, 9)*" -or $BuildPythonInfo[1] -ne "64") {
    Fail "The isolated build venv must use Python 3.11.9 x64."
}
if (-not (Test-Path -LiteralPath $Wheelhouse)) {
    Fail "Offline wheelhouse is missing: $Wheelhouse."
}
$WheelFiles = @(Get-ChildItem -LiteralPath $Wheelhouse -File | Where-Object { $_.Extension -in ".whl", ".gz", ".zip" })
if ($WheelFiles.Count -eq 0) { Fail "Offline wheelhouse is empty." }
$WheelManifest = $null
try { $WheelManifest = Get-Content -LiteralPath $WheelManifestPath -Raw | ConvertFrom-Json }
catch { Fail "Invalid wheelhouse hash manifest: $WheelManifestPath" }
if ($WheelManifest.algorithm -ne "sha256" -or -not $WheelManifest.files) { Fail "Wheelhouse manifest must declare a sha256 file map." }
foreach ($property in $WheelManifest.files.PSObject.Properties) {
    $wheelPath = Join-Path $Wheelhouse $property.Name
    if (-not (Test-Path -LiteralPath $wheelPath)) { Fail "Wheelhouse is missing manifest file: $($property.Name)" }
    $actualHash = (Get-FileHash -LiteralPath $wheelPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne ([string]$property.Value).ToLowerInvariant()) { Fail "Wheel hash mismatch: $($property.Name)" }
}
foreach ($wheel in $WheelFiles) {
    if (-not $WheelManifest.files.PSObject.Properties[$wheel.Name]) {
        Fail "Wheelhouse contains an unmanifested input: $($wheel.Name)"
    }
}
$LockPath = Join-Path $PackagingRoot "build-requirements.lock"
& $BuildPython -m pip install --no-cache-dir --no-index --no-deps --find-links $Wheelhouse "wheel==0.45.1" "ordered-set==4.1.0"
if ($LASTEXITCODE -ne 0) { Fail "Offline build bootstrap install failed." }
& $BuildPython -m pip install --no-cache-dir --no-index --no-build-isolation --find-links $Wheelhouse --require-hashes -r $LockPath
if ($LASTEXITCODE -ne 0) { Fail "Offline build requirements hash install failed." }
$NuitkaVersion = & $BuildPython -c 'import importlib.metadata as m; print(m.version("Nuitka"))'
if ($LASTEXITCODE -ne 0 -or $NuitkaVersion -notmatch "^4[.]2([.]|$)") {
    Fail "Nuitka 4.2 is required in the isolated build venv."
}
& $BuildPython -m pip install --no-cache-dir --no-index --no-deps --find-links $Wheelhouse -r (Join-Path $PackagingRoot "runtime-requirements.lock")
if ($LASTEXITCODE -ne 0) { Fail "Runtime dependencies were not installed from offline wheelhouse." }
if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path -LiteralPath $vswhere)) { Fail "VS 2022 vswhere.exe was not found." }
    $vsRootOutput = @(& $vswhere -latest -all -products * -property installationPath)
    $vsRoot = ($vsRootOutput | Select-Object -First 1)
    if (-not $vsRoot) { Fail "VS 2022 Build Tools installation was not found." }
    $vsRoot = $vsRoot.Trim()
    $devCmd = Join-Path $vsRoot "Common7\Tools\VsDevCmd.bat"
    if (-not (Test-Path -LiteralPath $devCmd)) { Fail "VS 2022 developer environment was not found." }
    $environmentLines = & cmd.exe /d /s /c "`"$devCmd`" -arch=x64 -host_arch=x64 >nul && set"
    if ($LASTEXITCODE -ne 0) { Fail "Unable to initialize the VS 2022 x64 toolchain." }
    $developerPath = $null
    foreach ($line in $environmentLines) {
        $name, $value = $line -split "=", 2
        # cmd.exe may emit PATH, Path, or both. Select the value actually
        # initialized by VsDevCmd rather than relying on casing/order.
        if ($name -ieq "Path") {
            if ($value -match "VC\\Tools\\MSVC") { $developerPath = $value }
            continue
        }
        if ($name -and $null -ne $value) { Set-Item -Path "Env:$name" -Value $value }
    }
    if ($developerPath) { Set-Item -Path "Env:Path" -Value $developerPath }
}
if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
    Fail "MSVC v143 x64 compiler was not found."
}
$ResolvedCl = (Get-Command cl.exe -ErrorAction Stop).Source
# Nuitka/Scons intentionally restores the pre-VsDevCmd PATH. Passing the
# already-validated compiler explicitly keeps the selected v143 toolchain
# deterministic while INCLUDE/LIB continue to come from VsDevCmd.
$env:CC = $ResolvedCl
$env:CXX = $ResolvedCl
# Nuitka emits UTF-8 C headers for the Chinese PE product metadata. Force the
# MSVC source/execution character sets so localized names compile losslessly.
$env:CL = (([string]$env:CL + " /utf-8").Trim())
$PytestBaseTemp = Join-Path $ProjectRoot "build\pytest-$OutputName"
& $PythonPath -m pytest -q --basetemp $PytestBaseTemp
if ($LASTEXITCODE -ne 0) { Fail "Source test suite failed." }
& $PythonPath -m compileall -q (Join-Path $ProjectRoot "app.py") (Join-Path $ProjectRoot "FastOrder.py") (Join-Path $ProjectRoot "fast_order")
if ($LASTEXITCODE -ne 0) { Fail "compileall failed." }
$Node = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $Node) { Fail "node.exe is required for the frozen JavaScript syntax check." }
& $Node.Source --check (Join-Path $ProjectRoot "static\app.js")
if ($LASTEXITCODE -ne 0) { Fail "static/app.js syntax check failed." }
& $PythonPath -m pip check
if ($LASTEXITCODE -ne 0) { Fail "pip check failed." }

& $PythonPath (Join-Path $PackagingRoot "verify_build.py") --source-root $ProjectRoot
if ($LASTEXITCODE -ne 0) { Fail "Source/UI hash preflight failed." }

if ($Clean) {
    $resolvedOutput = [System.IO.Path]::GetFullPath($OutputRoot)
    $expectedOutput = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "build\$OutputName"))
    if ($resolvedOutput -ne $expectedOutput) { Fail "Refusing to clean an unexpected output directory." }
    if (Test-Path -LiteralPath $resolvedOutput) { Remove-Item -LiteralPath $resolvedOutput -Recurse -Force }
    $resolvedWork = [System.IO.Path]::GetFullPath($NuitkaWorkRoot)
    $expectedWork = [System.IO.Path]::GetFullPath((Join-Path $env:TEMP "FastOrderBuild\$OutputName"))
    if ($resolvedWork -ne $expectedWork) { Fail "Refusing to clean an unexpected Nuitka work directory." }
    if (Test-Path -LiteralPath $resolvedWork) { Remove-Item -LiteralPath $resolvedWork -Recurse -Force }
}
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
New-Item -ItemType Directory -Force -Path $NuitkaWorkRoot | Out-Null

$NuitkaArgs = @(
    "--mode=standalone",
    "--output-dir=$NuitkaWorkRoot",
    "--output-filename=FastOrder.exe",
    "--windows-console-mode=force",
    "--python-flag=safe_path",
    "--include-data-dir=$(Join-Path $ProjectRoot 'static')=static",
    "--include-data-dir=$(Join-Path $ProjectRoot 'config')=config",
    "--windows-icon-from-ico=$(Join-Path $PackagingRoot 'assets\FastOrder.ico')",
    "--msvc=14.3",
    "--company-name=$CompanyName",
    "--product-name=极速下单",
    "--file-version=1.0.0.0",
    "--product-version=1.0.0",
    "--file-description=极速下单本地交易客户端",
    "--nofollow-import-to=tests",
    "--nofollow-import-to=pytest",
    "--nofollow-import-to=packaging",
    "--include-package=fast_order",
    "--include-package=aiohttp",
    "--include-package=webull",
    "--include-package=webull.trade.events",
    "--include-package=grpc",
    "--include-package=paho",
    "--include-package=keyring",
    "--include-package=keyring.backends",
    "--include-module=keyring.backends.Windows",
    "--include-package=win32ctypes",
    "--include-package=tzdata",
    "--include-package=certifi",
    "--include-package=google.protobuf",
    "--include-package=cryptography.hazmat.bindings",
    "--include-module=cryptography.hazmat.bindings._rust",
    "--include-module=sqlite3",
    "--include-module=_sqlite3",
    "--include-package-data=webull",
    "--include-package-data=grpc",
    "--include-package-data=certifi",
    "--include-package-data=tzdata",
    "--include-distribution-metadata=keyring",
    "--report=$NuitkaReportPath",
    (Join-Path $ProjectRoot "FastOrder.py")
)
if ($AllowToolDownloads) {
    $NuitkaArgs = @("--assume-yes-for-downloads") + $NuitkaArgs
}

# Scons writes linker response/resource data containing Unicode package
# metadata. Scope UTF-8 mode to the compiler phase so native ACL test tools
# continue to decode with the host Windows code page.
$env:PYTHONUTF8 = "1"
Push-Location $ProjectRoot
try {
    & $BuildPython -m nuitka @NuitkaArgs
    if ($LASTEXITCODE -ne 0) { Fail "Nuitka standalone compilation failed." }
}
finally { Pop-Location }

Test-NuitkaToolCache -RequirePresent

if (-not (Test-Path -LiteralPath $NuitkaDistRoot)) { Fail "FastOrder.dist was not generated." }
if (Test-Path -LiteralPath $DistRoot) { Remove-Item -LiteralPath $DistRoot -Recurse -Force }
Copy-Item -LiteralPath $NuitkaDistRoot -Destination $DistRoot -Recurse
Copy-Item -LiteralPath $NuitkaReportPath -Destination $ReportPath -Force
New-Item -ItemType Directory -Force -Path (Join-Path $DistRoot "config") | Out-Null
Copy-Item -LiteralPath $SelectedPolicy -Destination (Join-Path $DistRoot "config\production.json") -Force
& $BuildPython (Join-Path $PackagingRoot "collect_licenses.py") --output (Join-Path $DistRoot "LICENSES")
if ($LASTEXITCODE -ne 0) { Fail "Third-party license collection failed." }
Copy-Item -LiteralPath (Join-Path $PackagingRoot "assets\FastOrder.ico") -Destination (Join-Path $DistRoot "FastOrder.ico") -Force
& $BuildPython (Join-Path $PackagingRoot "verify_build.py") --source-root $ProjectRoot --dist $DistRoot --manifest-out $ManifestPath
if ($LASTEXITCODE -ne 0) { Fail "Artifact whitelist, resource, or secret audit failed." }
& $BuildPython (Join-Path $PackagingRoot "smoke_test_exe.py") --exe (Join-Path $DistRoot "FastOrder.exe") --ui-manifest (Join-Path $PackagingRoot "ui-hashes.json")
if ($LASTEXITCODE -ne 0) { Fail "Packaged executable smoke failed." }
Write-Host "FastOrder standalone ready: $DistRoot"
