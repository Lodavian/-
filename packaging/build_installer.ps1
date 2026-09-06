[CmdletBinding(DefaultParameterSetName = "FormalSigned")]
param(
    [Parameter(Mandatory = $true)] [string]$StagingDir,
    [Parameter(Mandatory = $true)] [string]$EvidenceJson,
    [Parameter(Mandatory = $true, ParameterSetName = "InternalUnsignedRc")]
    [switch]$InternalUnsignedRc,
    [Parameter(Mandatory = $true, ParameterSetName = "FormalSigned")]
    [switch]$FormalSigned,
    [Parameter(Mandatory = $true, ParameterSetName = "FormalSigned")]
    [string]$Publisher,
    [Parameter(Mandatory = $true, ParameterSetName = "FormalSigned")]
    [string]$CertificateThumbprint,
    [Parameter(Mandatory = $true, ParameterSetName = "FormalSigned")]
    [string]$TimestampUrl,
    [Parameter(Mandatory = $true, ParameterSetName = "FormalSigned")]
    [switch]$CommercialLicenseConfirmed,
    [string]$IsccPath,
    [string]$PythonPath = "python.exe",
    [string]$OutputDir = (Join-Path $PSScriptRoot "..\release\artifacts"),
    [string]$UpgradeFixtureOutputDir = (Join-Path $PSScriptRoot "..\build\installer-qa-fixtures")
)
$ErrorActionPreference = "Stop"

$mode = $PSCmdlet.ParameterSetName
if ($mode -eq "FormalSigned" -and -not $CommercialLicenseConfirmed) {
    throw "Commercial Inno Setup license confirmation is required for a formal production installer."
}
if ($mode -eq "InternalUnsignedRc" -and $FormalSigned) {
    throw "InternalUnsignedRc and FormalSigned are mutually exclusive."
}
if ($mode -eq "FormalSigned" -and $InternalUnsignedRc) {
    throw "InternalUnsignedRc and FormalSigned are mutually exclusive."
}
$evidencePath = (Resolve-Path -LiteralPath $EvidenceJson).Path
$evidenceValidator = Join-Path $PSScriptRoot "..\release\validate_release_evidence.py"
& $PythonPath $evidenceValidator $evidencePath
if ($LASTEXITCODE -ne 0) { throw "Release qualification evidence is invalid." }
$evidence = Get-Content -LiteralPath $evidencePath -Raw | ConvertFrom-Json
$requiredPriorGates = if ($mode -eq "InternalUnsignedRc") { @("A", "B") } else { @("A", "B", "C") }
foreach ($gate in $requiredPriorGates) {
    $gateEvidence = $evidence.gates.PSObject.Properties[$gate].Value
    if ($gateEvidence.status -ne "PASS") {
        throw "Gate $gate must PASS before the $mode installer build."
    }
}

if ($IsccPath) {
    $isccSource = (Resolve-Path -LiteralPath $IsccPath).Path
} else {
    $iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($iscc) {
        $isccSource = $iscc.Source
    } else {
        $standardIscc = Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 7\ISCC.exe"
        if (Test-Path -LiteralPath $standardIscc) { $isccSource = $standardIscc }
    }
}
if (-not $isccSource) { throw "Inno Setup 7.1.0 ISCC.exe not found." }
$innoVersion = (& $isccSource --version 2>&1 | Select-Object -Last 1).ToString().Trim()
if ($LASTEXITCODE -ne 0 -or $innoVersion -ne "7.1.0") { throw "Inno Setup 7.1.0 is required; found $innoVersion." }
$resolvedStage = (Resolve-Path -LiteralPath $StagingDir).Path
$exe = Join-Path $resolvedStage "FastOrder.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "Standalone FastOrder.exe not found." }

if ($mode -eq "FormalSigned") {
    if ([string]::IsNullOrWhiteSpace($Publisher) -or $Publisher.Length -lt 2 -or
        $Publisher -match '[<>"\r\n]' -or
        $Publisher -match '(?i)^(example|test|todo|unknown|fastorder internal)') {
        throw "Publisher must be a legal, non-placeholder company name."
    }
    $CertificateThumbprint = ($CertificateThumbprint -replace '\s', '').ToUpperInvariant()
    if ($CertificateThumbprint -notmatch '^[A-F0-9]{40,64}$') {
        throw "Certificate thumbprint format is invalid."
    }
    $timestampUri = $null
    if (-not [Uri]::TryCreate($TimestampUrl, [UriKind]::Absolute, [ref]$timestampUri) -or
        $timestampUri.Scheme -ne "https") {
        throw "RFC 3161 timestamp URL must use HTTPS."
    }
    $signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if (-not $signtool) { throw "Windows SDK signtool.exe not found." }
    & $signtool.Source verify /pa /all /v $exe
    if ($LASTEXITCODE -ne 0) { throw "FastOrder.exe signature verification failed." }
    $signature = Get-AuthenticodeSignature -LiteralPath $exe
    if ($signature.Status -ne "Valid") { throw "FastOrder.exe Authenticode status is not Valid." }
    if (($signature.SignerCertificate.Thumbprint -replace '\s', '') -ne $CertificateThumbprint) {
        throw "FastOrder.exe signing certificate does not match the requested thumbprint."
    }
    if (-not $signature.TimeStamperCertificate) { throw "FastOrder.exe has no trusted timestamp certificate." }
    if ((Get-Item -LiteralPath $exe).VersionInfo.CompanyName -ne $Publisher) {
        throw "PE CompanyName must exactly match Publisher."
    }
    if ($signature.SignerCertificate.Subject -notmatch [regex]::Escape($Publisher)) {
        throw "Publisher is not present in the certificate subject."
    }
}

$smokeArgs = @(
    (Join-Path $PSScriptRoot "smoke_test_exe.py"),
    "--exe", $exe,
    "--ui-manifest", (Join-Path $PSScriptRoot "ui-hashes.json")
)
if ($mode -eq "InternalUnsignedRc") { $smokeArgs += "--allow-credential-manager-unavailable" }
& $PythonPath @smokeArgs
if ($LASTEXITCODE -ne 0) { throw "Standalone packaged smoke failed." }
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$issArgs = @("/DStagingDir=$resolvedStage", "/O$OutputDir")
if ($mode -eq "InternalUnsignedRc") {
    $issArgs += "/DInternalUnsignedRc"
} else {
    $signCommand = '"' + $signtool.Source + '" sign /sha1 ' + $CertificateThumbprint + ' /fd SHA256 /tr ' + $TimestampUrl + ' /td SHA256 $f'
    $issArgs += @("/DFormalSigned", "/DMyAppPublisher=$Publisher", "/DSignToolName=fastorder-sign", "/Sfastorder-sign=$signCommand")
}
& $isccSource @issArgs (Join-Path $PSScriptRoot "FastOrder.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed." }
$outputName = if ($mode -eq "InternalUnsignedRc") { "FastOrder-Setup-1.0.0-rc1.exe" } else { "FastOrder-Setup-1.0.0.exe" }
$setup = Join-Path $OutputDir $outputName
if (-not (Test-Path -LiteralPath $setup)) { throw "Expected installer artifact was not generated: $setup" }
if ($mode -eq "FormalSigned") {
    & $signtool.Source verify /pa /all /v $setup
    if ($LASTEXITCODE -ne 0) { throw "Installer signature verification failed." }
    $setupSignature = Get-AuthenticodeSignature -LiteralPath $setup
    if ($setupSignature.Status -ne "Valid") { throw "Installer Authenticode status is not Valid." }
    if (($setupSignature.SignerCertificate.Thumbprint -replace '\s', '') -ne $CertificateThumbprint) {
        throw "Installer signing certificate does not match the requested thumbprint."
    }
    if (-not $setupSignature.TimeStamperCertificate) { throw "Installer has no trusted timestamp certificate." }
} else {
    $setupSignature = Get-AuthenticodeSignature -LiteralPath $setup
    if ($setupSignature.Status -ne "NotSigned") { throw "Internal RC installer must not be signed." }
}
if ($mode -eq "InternalUnsignedRc") {
    New-Item -ItemType Directory -Force -Path $UpgradeFixtureOutputDir | Out-Null
    $fixtureArgs = @(
        "/DStagingDir=$resolvedStage",
        "/DInternalUnsignedRc",
        "/DMyAppVersion=0.9.9-qa",
        "/DMyOutputBaseFilename=FastOrder-Setup-0.9.9-qa",
        "/O$UpgradeFixtureOutputDir"
    )
    & $isccSource @fixtureArgs (Join-Path $PSScriptRoot "FastOrder.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup 0.9.9-qa lifecycle fixture build failed." }
    $fixture = Join-Path $UpgradeFixtureOutputDir "FastOrder-Setup-0.9.9-qa.exe"
    if (-not (Test-Path -LiteralPath $fixture)) { throw "Expected lifecycle fixture was not generated: $fixture" }
    if ((Get-AuthenticodeSignature -LiteralPath $fixture).Status -ne "NotSigned") {
        throw "The 0.9.9-qa lifecycle fixture must remain unsigned."
    }
    Write-Output "QA_ONLY_UPGRADE_FIXTURE=$fixture"
    Write-Output "RELEASE_CLASS=UNSIGNED_INTERNAL_RC"
}
Write-Output $setup
