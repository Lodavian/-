[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$StagingDir,
    [Parameter(Mandatory = $true)] [string]$Publisher,
    [Parameter(Mandatory = $true)] [string]$CertificateThumbprint,
    [Parameter(Mandatory = $true)] [string]$TimestampUrl,
    [Parameter(Mandatory = $true)] [switch]$CommercialLicenseConfirmed,
    [string]$PythonPath = "python.exe",
    [string]$OutputDir = (Join-Path $PSScriptRoot "..\release\artifacts")
)
$ErrorActionPreference = "Stop"
if (-not $CommercialLicenseConfirmed) { throw "Commercial Inno Setup license confirmation is required." }
$iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if (-not $iscc) { throw "Inno Setup 7.1.0 ISCC.exe not found." }
$innoVersion = $iscc.FileVersionInfo.ProductVersion
if ($innoVersion -notlike "7.1.0*") { throw "Inno Setup 7.1.0 is required; found $innoVersion." }
$signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
if (-not $signtool) { throw "Windows SDK signtool.exe not found." }
$resolvedStage = (Resolve-Path -LiteralPath $StagingDir).Path
$exe = Join-Path $resolvedStage "FastOrder.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "Signed standalone FastOrder.exe not found." }
& $signtool.Source verify /pa /all /v $exe
if ($LASTEXITCODE -ne 0) { throw "FastOrder.exe signature verification failed." }
$signature = Get-AuthenticodeSignature -LiteralPath $exe
if ($signature.Status -ne "Valid") { throw "FastOrder.exe Authenticode status is not Valid." }
if ($signature.SignerCertificate.Thumbprint -ne $CertificateThumbprint) { throw "FastOrder.exe signing certificate does not match the requested thumbprint." }
if ((Get-Item -LiteralPath $exe).VersionInfo.CompanyName -ne $Publisher) { throw "PE CompanyName must exactly match Publisher." }
if ($signature.SignerCertificate.Subject -notmatch [regex]::Escape($Publisher)) { throw "Publisher is not present in the certificate subject." }
if ($TimestampUrl -notmatch '^https://') { throw "RFC 3161 timestamp URL must use HTTPS." }
if ($CertificateThumbprint -notmatch '^[A-Fa-f0-9]{40,64}$') { throw "Certificate thumbprint format is invalid." }
& $PythonPath (Join-Path $PSScriptRoot "smoke_test_exe.py") --exe $exe --ui-manifest (Join-Path $PSScriptRoot "ui-hashes.json")
if ($LASTEXITCODE -ne 0) { throw "Signed standalone packaged smoke failed." }
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$signCommand = '"' + $signtool.Source + '" sign /sha1 ' + $CertificateThumbprint + ' /fd SHA256 /tr ' + $TimestampUrl + ' /td SHA256 $f'
& $iscc.Source "/DStagingDir=$resolvedStage" "/DMyAppPublisher=$Publisher" "/DSignToolName=fastorder-sign" "/Sfastorder-sign=$signCommand" "/O$OutputDir" (Join-Path $PSScriptRoot "FastOrder.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed." }
$setup = Join-Path $OutputDir "FastOrder-Setup-1.0.0.exe"
& $signtool.Source verify /pa /all /v $setup
if ($LASTEXITCODE -ne 0) { throw "Installer signature verification failed." }
Write-Output $setup
