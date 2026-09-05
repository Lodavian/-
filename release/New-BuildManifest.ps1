[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$ArtifactDirectory,
    [Parameter(Mandatory = $true)] [string]$SourceRoot,
    [Parameter(Mandatory = $true)] [string]$EvidenceJson,
    [string]$Version = "1.0.0",
    [string]$OutputPath
)
$ErrorActionPreference = "Stop"
$artifactRoot = (Resolve-Path -LiteralPath $ArtifactDirectory).Path
$source = (Resolve-Path -LiteralPath $SourceRoot).Path
$evidencePath = (Resolve-Path -LiteralPath $EvidenceJson).Path
if (-not $OutputPath) { $OutputPath = Join-Path $artifactRoot "build-manifest.json" }
$evidence = Get-Content -LiteralPath $evidencePath -Raw | ConvertFrom-Json
foreach ($gate in "sourceTests", "packagedSmoke", "sandboxClosure", "installerQa", "signatureVerification") {
    if ($evidence.$gate -ne "PASS") { throw "Release evidence gate is not PASS: $gate" }
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
    $ui[$name] = [ordered]@{ source = (Get-FileHash $sourceUi -Algorithm SHA256).Hash.ToLowerInvariant() }
}
$setup = Get-ChildItem -LiteralPath $artifactRoot -Filter "FastOrder-Setup-$Version.exe" -Recurse -File | Select-Object -First 1
if (-not $setup) { throw "Signed installer artifact is missing." }
$signature = Get-AuthenticodeSignature -LiteralPath $setup.FullName
if ($signature.Status -ne "Valid") { throw "Installer signature is not valid." }
if (-not $signature.TimeStamperCertificate) { throw "Installer has no verified RFC 3161 timestamp certificate." }
$gitCommit = (& git -C $source rev-parse HEAD).Trim()
$gitTree = (& git -C $source rev-parse "HEAD^{tree}").Trim()
if ($LASTEXITCODE -ne 0) { throw "Unable to resolve source Git identity." }
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
        python = (& python --version 2>&1).ToString().Trim()
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
$manifest | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $OutputPath -Encoding utf8
Write-Output $OutputPath
