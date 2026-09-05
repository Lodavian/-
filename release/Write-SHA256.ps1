[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ArtifactDirectory,
    [string]$OutputPath
)
$ErrorActionPreference = 'Stop'
$artifactRoot = (Resolve-Path -LiteralPath $ArtifactDirectory).Path
if (-not $OutputPath) { $OutputPath = Join-Path $artifactRoot 'SHA256SUMS.txt' }
$files = Get-ChildItem -LiteralPath $artifactRoot -File | Where-Object { $_.Name -ne (Split-Path -Leaf $OutputPath) } | Sort-Object Name
if (-not $files) { throw "No release artifacts found in $artifactRoot" }
$lines = foreach ($file in $files) {
    $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $($file.Name)"
}
[IO.File]::WriteAllLines($OutputPath, $lines, [Text.UTF8Encoding]::new($false))
Write-Output $OutputPath
