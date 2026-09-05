$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    $BootstrapCandidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe")
    )
    $Bootstrap = $BootstrapCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $Bootstrap) {
        throw "需要 Python 3.11、3.12 或 3.13。"
    }
    & $Bootstrap -m venv (Join-Path $ProjectRoot ".venv")
    & $Python -m pip install --disable-pip-version-check --no-input -r (Join-Path $ProjectRoot "requirements.txt")
}

$DependencyCheck = & $Python -c "import importlib.metadata as m; print(m.version('aiohttp') + '|' + m.version('keyring') + '|' + m.version('tzdata') + '|' + m.version('webull-openapi-python-sdk'))" 2>$null
if ($LASTEXITCODE -ne 0 -or $DependencyCheck -ne "3.14.3|25.7.0|2026.3|2.0.18") {
    & $Python -m pip install --disable-pip-version-check --no-input -r (Join-Path $ProjectRoot "requirements.txt")
}

& $Python (Join-Path $ProjectRoot "app.py")
