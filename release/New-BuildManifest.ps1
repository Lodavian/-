[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$ArtifactDirectory,
    [Parameter(Mandatory = $true)] [string]$SourceRoot,
    [Parameter(Mandatory = $true)] [string]$EvidenceJson,
    [string]$Version = "1.0.0",
    [string]$OutputPath,
    [string]$PythonPath = "python.exe"
)
$ErrorActionPreference = "Stop"
$artifactRoot = (Resolve-Path -LiteralPath $ArtifactDirectory).Path
$source = (Resolve-Path -LiteralPath $SourceRoot).Path
$evidencePath = (Resolve-Path -LiteralPath $EvidenceJson).Path
if (-not $OutputPath) { $OutputPath = Join-Path $artifactRoot "build-manifest.json" }
$evidence = Get-Content -LiteralPath $evidencePath -Raw | ConvertFrom-Json
$validator = Join-Path $PSScriptRoot "validate_release_evidence.py"
& $PythonPath $validator $evidencePath --formal
if ($LASTEXITCODE -ne 0) { throw "Unified Gate A-D evidence validation failed; formal manifest was not generated." }
$credentialScanner = Join-Path $PSScriptRoot "scan_release_credentials.py"
$credentialPolicy = Join-Path $source "packaging\artifact-policy.json"
& $PythonPath $credentialScanner $artifactRoot --policy $credentialPolicy
if ($LASTEXITCODE -ne 0) { throw "Read-only credential leak scan failed; formal manifest was not generated." }
if ($evidence.schemaVersion -ne 2 -or $evidence.product -ne "FastOrder") {
    throw "Release evidence schemaVersion 2 for FastOrder is required."
}
if ($evidence.releaseClass -ne "formal") {
    throw "Only releaseClass=formal may generate a formal build manifest; unsigned/internal candidates are ineligible."
}
if (-not $evidence.gates) { throw "Unified Gate A-D evidence is required." }
foreach ($gate in "A", "B", "C", "D") {
    $gateEvidence = $evidence.gates.PSObject.Properties[$gate].Value
    if (-not $gateEvidence) { throw "Release evidence gate is missing: Gate $gate" }
    if ($gateEvidence.status -eq "BLOCKED") { throw "Release evidence is BLOCKED: Gate $gate" }
    if ($gateEvidence.status -ne "PASS") { throw "Release evidence gate is not PASS: Gate $gate" }
}
if (-not $evidence.credentialScan -or $evidence.credentialScan.status -ne "PASS" -or @($evidence.credentialScan.findings).Count -ne 0) {
    throw "A PASS read-only credential scan with no findings is required."
}
if (-not $evidence.signature -or [string]$evidence.signature.status -ine "Valid" -or [string]$evidence.signature.timestampStatus -ine "Valid") {
    throw "Evidence must record a Valid Authenticode signature and Valid RFC3161 timestamp."
}
if ([string]$evidence.signature.timestampUrl -notmatch '^https://') {
    throw "Evidence timestampUrl must use HTTPS."
}
$files = Get-ChildItem -LiteralPath $artifactRoot -Recurse -File | Where-Object { $_.FullName -ne [IO.Path]::GetFullPath($OutputPath) } | Sort-Object FullName | ForEach-Object {
    [ordered]@{
        path = $_.FullName.Substring($artifactRoot.Length).TrimStart("\").Replace("\", "/")
        bytes = $_.Length
        sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
$ui = @{}
foreach ($name in "index.html", "styles.css", "app.js") {
    $sourceUi = Join-Path $source "static\$name"
    $relativeUi = "static/$name"
    $sourceHash = (Get-FileHash $sourceUi -Algorithm SHA256).Hash.ToLowerInvariant()
    $ui[$name] = [ordered]@{
        source = $sourceHash
        standalone = ([string]$evidence.candidate.uiHashes.standalone.PSObject.Properties[$relativeUi].Value).ToLowerInvariant()
        installed = ([string]$evidence.candidate.uiHashes.installed.PSObject.Properties[$relativeUi].Value).ToLowerInvariant()
    }
}
$setup = Get-ChildItem -LiteralPath $artifactRoot -Filter "FastOrder-Setup-$Version.exe" -Recurse -File | Select-Object -First 1
if (-not $setup) { throw "Signed installer artifact is missing." }
$signature = Get-AuthenticodeSignature -LiteralPath $setup.FullName
if ($signature.Status -ne "Valid") { throw "Installer signature is not valid." }
if (-not $signature.TimeStamperCertificate) { throw "Installer has no verified RFC 3161 timestamp certificate." }
if (-not $signature.SignerCertificate) { throw "Installer signer certificate is missing." }
if ($evidence.signature.signerThumbprint -and $signature.SignerCertificate.Thumbprint -ine [string]$evidence.signature.signerThumbprint) {
    throw "Installer signer certificate does not match the evidence thumbprint."
}
if ($signature.SignerCertificate.Subject -ine [string]$evidence.signature.signerSubject) {
    throw "Installer signer subject does not match the evidence."
}
if ((Get-FileHash -LiteralPath $setup.FullName -Algorithm SHA256).Hash -ine [string]$evidence.candidate.installer.sha256) {
    throw "Installer SHA256 does not match the qualified evidence."
}
$standalone = Get-ChildItem -LiteralPath $artifactRoot -Filter "FastOrder.exe" -Recurse -File | Select-Object -First 1
if (-not $standalone) { throw "Signed standalone FastOrder.exe artifact is missing." }
$standaloneSignature = Get-AuthenticodeSignature -LiteralPath $standalone.FullName
if ($standaloneSignature.Status -ne "Valid") { throw "Standalone FastOrder.exe signature is not valid." }
if (-not $standaloneSignature.TimeStamperCertificate) { throw "Standalone FastOrder.exe has no verified RFC 3161 timestamp certificate." }
if ($standaloneSignature.SignerCertificate.Thumbprint -ine [string]$evidence.signature.signerThumbprint) { throw "Standalone signer does not match the evidence." }
if ((Get-FileHash -LiteralPath $standalone.FullName -Algorithm SHA256).Hash -ine [string]$evidence.candidate.executable.sha256) { throw "Standalone SHA256 does not match the qualified evidence." }
$standaloneRoot = Split-Path -Parent $standalone.FullName
foreach ($name in "index.html", "styles.css", "app.js") {
    $relativeUi = "static/$name"
    $packedUi = Join-Path $standaloneRoot "static\$name"
    if (-not (Test-Path -LiteralPath $packedUi -PathType Leaf)) { throw "Standalone UI resource is missing: $relativeUi" }
    if ((Get-FileHash -LiteralPath $packedUi -Algorithm SHA256).Hash -ine [string]$evidence.candidate.uiHashes.standalone.PSObject.Properties[$relativeUi].Value) {
        throw "Standalone UI hash does not match evidence: $relativeUi"
    }
}
$gitCommit = (& git -C $source rev-parse HEAD).Trim()
$gitTree = (& git -C $source rev-parse "HEAD^{tree}").Trim()
if ($LASTEXITCODE -ne 0) { throw "Unable to resolve source Git identity." }
if ($gitCommit -ine [string]$evidence.candidate.commit -or $gitTree -ine [string]$evidence.candidate.tree) {
    throw "Source Git identity does not match the qualified evidence."
}
$dirty = (& git -C $source status --porcelain)
if ($LASTEXITCODE -ne 0 -or $dirty) { throw "Formal manifest requires a clean source worktree." }
$inputs = @{}
foreach ($relative in "requirements.txt", "packaging/runtime-requirements.lock", "packaging/build-requirements.lock", "packaging/wheelhouse-manifest.json", "packaging/ui-hashes.json", "packaging/artifact-policy.json", "config/production.json") {
    $path = Join-Path $source $relative
    $inputs[$relative] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
}
$manifest = [ordered]@{
    schemaVersion = 1
    product = "FastOrder"
    semanticVersion = $Version
    fileVersion = "$Version.0"
    builtAtUtc = [DateTime]::UtcNow.ToString("o")
    target = "windows-x64"
    sourceCommit = $gitCommit
    sourceTree = $gitTree
    inputSha256 = $inputs
    toolchain = [ordered]@{
        python = (& $PythonPath --version 2>&1).ToString().Trim()
        nuitka = "4.2"
        msvc = "14.3"
        innoSetup = "7.1.0-x64"
        windowsSdk = $evidence.windowsSdk
    }
    ui = $ui
    productionPolicySha256 = (Get-FileHash (Join-Path $source "config\production.json") -Algorithm SHA256).Hash.ToLowerInvariant()
    signature = [ordered]@{
        status = $signature.Status.ToString()
        subject = $signature.SignerCertificate.Subject
        thumbprint = $signature.SignerCertificate.Thumbprint
        timestampCertificate = $signature.TimeStamperCertificate.Subject
    }
    evidence = $evidence
    artifacts = @($files)
}
if ($manifest.productionPolicySha256 -ine [string]$evidence.candidate.policy.sha256) {
    throw "Production policy SHA256 does not match the qualified evidence."
}
$manifest | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $OutputPath -Encoding utf8
Write-Output $OutputPath
