[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$ArtifactPath,
    [Parameter(Mandatory = $true)] [string]$CertificateThumbprint,
    [string]$SignToolPath = 'signtool.exe',
    [string]$AdditionalSignToolArgs = '',
    [Parameter(Mandatory = $true)] [string]$TimestampUrl
)
$ErrorActionPreference = 'Stop'
$signtool = (Get-Command $SignToolPath -ErrorAction SilentlyContinue)
if (-not $signtool) { throw "$SignToolPath not found; install Windows SDK before signing." }
$extra = if ($AdditionalSignToolArgs) { $AdditionalSignToolArgs -split '\s+' } else { @() }
& $signtool.Source sign /fd SHA256 /sha1 $CertificateThumbprint /tr $TimestampUrl /td SHA256 @extra $ArtifactPath
if ($LASTEXITCODE -ne 0) { throw "signtool failed with exit code $LASTEXITCODE" }
& $signtool.Source verify /pa /all /v $ArtifactPath
if ($LASTEXITCODE -ne 0) { throw "signature verification failed with exit code $LASTEXITCODE" }
$signature = Get-AuthenticodeSignature -LiteralPath $ArtifactPath
if ($signature.Status -ne 'Valid' -or -not $signature.TimeStamperCertificate) {
    throw 'Authenticode signature or RFC 3161 timestamp validation failed.'
}
if ($signature.SignerCertificate.Thumbprint -ne $CertificateThumbprint) {
    throw 'Signed artifact certificate thumbprint mismatch.'
}
