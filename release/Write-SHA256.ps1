[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ArtifactDirectory,
    [string]$OutputPath
)
$ErrorActionPreference = 'Stop'
$artifactRoot = (Resolve-Path -LiteralPath $ArtifactDirectory).Path
if (-not $OutputPath) { $OutputPath = Join-Path $artifactRoot 'SHA256SUMS.txt' }
$outputFullPath = [IO.Path]::GetFullPath($OutputPath)
$files = Get-ChildItem -LiteralPath $artifactRoot -Recurse -File |
    Where-Object { [IO.Path]::GetFullPath($_.FullName) -ne $outputFullPath } |
    Sort-Object FullName
if (-not $files) { throw "No release artifacts found in $artifactRoot" }
$lines = foreach ($file in $files) {
    $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $relative = $file.FullName.Substring($artifactRoot.Length).TrimStart('\').Replace('\', '/')
    "$hash  $relative"
}
[IO.Directory]::CreateDirectory((Split-Path -Parent $outputFullPath)) | Out-Null
[IO.File]::WriteAllLines($outputFullPath, $lines, [Text.UTF8Encoding]::new($false))
Write-Output $outputFullPath
