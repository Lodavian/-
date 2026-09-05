[CmdletBinding()]
param([Parameter(Mandatory = $true)] [string]$PythonPath)
$ErrorActionPreference = "Stop"
$python = (Resolve-Path -LiteralPath $PythonPath).Path
$wheelhouse = Join-Path $PSScriptRoot "wheelhouse"
if (Test-Path -LiteralPath $wheelhouse) { Remove-Item -LiteralPath $wheelhouse -Recurse -Force }
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
& $python -m pip download --no-deps --dest $wheelhouse -r (Join-Path $PSScriptRoot "runtime-requirements.lock")
if ($LASTEXITCODE -ne 0) { throw "Runtime wheel download failed." }
$builtWheels = Join-Path $wheelhouse ".built"
New-Item -ItemType Directory -Force -Path $builtWheels | Out-Null
& $python -m pip wheel --no-deps --no-build-isolation --wheel-dir $builtWheels "paho-mqtt==1.6.1"
if ($LASTEXITCODE -ne 0) { throw "Pure-Python paho-mqtt wheel build failed." }
Get-ChildItem -LiteralPath $builtWheels -File | Move-Item -Destination $wheelhouse -Force
Remove-Item -LiteralPath $builtWheels -Recurse -Force
Get-ChildItem -LiteralPath $wheelhouse -File -Filter "paho-mqtt-1.6.1*.tar.gz" | Remove-Item -Force
& $python -m pip download --no-deps --dest $wheelhouse "Nuitka==4.2" "ordered-set==4.1.0" "wheel==0.45.1"
if ($LASTEXITCODE -ne 0) { throw "Build input download failed." }
$files = [ordered]@{}
Get-ChildItem -LiteralPath $wheelhouse -File | Sort-Object Name | ForEach-Object {
    $files[$_.Name] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
$manifest = [ordered]@{ schemaVersion = 1; algorithm = "sha256"; files = $files }
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $PSScriptRoot "wheelhouse-manifest.json") -Encoding utf8
Write-Output "Wheelhouse prepared. Review and commit only the manifest; wheel payloads remain ignored."
