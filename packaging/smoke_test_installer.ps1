[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$InstallerPath,
    [Parameter(Mandatory = $true)]
    [ValidateSet("InternalUnsignedRc", "FormalSigned")]
    [string]$Mode,
    [Parameter(Mandatory = $true)]
    [switch]$DisposableVmConfirmed,
    [Parameter(Mandatory = $true)]
    [Alias("UpgradeInput", "PreviousInstaller", "UpgradeInstallerPath", "PreviousVersionInstallerPath")]
    [string]$PreviousInstallerPath,
    [ValidateSet("0.9.9-qa")]
    [string]$PreviousVersion = "0.9.9-qa",
    [string]$PythonPath = "python.exe",
    [string]$InstallDir = "$env:ProgramFiles\FastOrder",
    [string]$ArtifactPolicyPath = (Join-Path $PSScriptRoot "artifact-policy.json"),
    [string]$UiManifestPath = (Join-Path $PSScriptRoot "ui-hashes.json")
)
$ErrorActionPreference = "Stop"

function Invoke-Installer([string]$Path, [string]$TargetDir) {
    & $Path /VERYSILENT /SUPPRESSMSGBOXES /NORESTART "/DIR=$TargetDir"
    return [int]$LASTEXITCODE
}

function Assert-InstallerSucceeded([string]$Path, [string]$TargetDir, [string]$Message) {
    $code = Invoke-Installer $Path $TargetDir
    if ($code -ne 0) { throw "$Message (exit code $code)." }
}

function Get-RelativePath([string]$Root, [string]$Path) {
    return $Path.Substring($Root.TrimEnd('\').Length).TrimStart('\', '/').Replace('\', '/')
}

function Assert-InstalledWhitelist([string]$Root, [string]$PolicyPath) {
    $policy = Get-Content -LiteralPath $PolicyPath -Raw | ConvertFrom-Json
    $allowedRoots = @($policy.allowedRootDirectories)
    $allowedPatterns = @($policy.allowedRootPatterns)
    $allowedExtensions = @($policy.allowedRootExtensions | ForEach-Object { ([string]$_).ToLowerInvariant() })
    $forbidden = @($policy.forbiddenNames)
    foreach ($file in Get-ChildItem -LiteralPath $Root -Recurse -File -Force) {
        $relative = Get-RelativePath $Root $file.FullName
        $parts = $relative -split '/'
        foreach ($part in $parts) {
            foreach ($pattern in $forbidden) {
                if ($part -like [string]$pattern) { throw "Installer whitelist rejected forbidden path: $relative" }
            }
        }
        $rootName = $parts[0]
        $rootAllowed = $allowedRoots -contains $rootName
        if (-not $rootAllowed) {
            foreach ($pattern in $allowedPatterns) {
                if ($rootName -like [string]$pattern) { $rootAllowed = $true; break }
            }
        }
        $extensionAllowed = $allowedExtensions -contains $file.Extension.ToLowerInvariant()
        if (-not $rootAllowed -and -not $extensionAllowed) {
            throw "Installer whitelist rejected path: $relative"
        }
    }
}

function Get-Hash([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Assert-UiHashes([string]$Root, [string]$ManifestPath) {
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    foreach ($property in $manifest.files.PSObject.Properties) {
        $path = Join-Path $Root ([string]$property.Name).Replace('/', '\')
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Installed UI file is missing: $($property.Name)" }
        if ((Get-Hash $path) -ne ([string]$property.Value).ToUpperInvariant()) {
            throw "Installed UI hash mismatch: $($property.Name)"
        }
    }
}

function Assert-Shortcuts([string]$TargetExe) {
    $shortcutPaths = @(
        (Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs\极速下单.lnk"),
        (Join-Path $env:Public "Desktop\极速下单.lnk")
    )
    $shell = New-Object -ComObject WScript.Shell
    foreach ($shortcut in $shortcutPaths) {
        if (-not (Test-Path -LiteralPath $shortcut)) { throw "Expected shortcut is missing: $shortcut" }
        $shortcutObject = $shell.CreateShortcut($shortcut)
        $target = $shortcutObject.TargetPath
        if ([IO.Path]::GetFullPath($target) -ne [IO.Path]::GetFullPath($TargetExe)) {
            throw "Shortcut target mismatch: $shortcut"
        }
        if ([IO.Path]::GetFullPath($shortcutObject.WorkingDirectory) -ne [IO.Path]::GetFullPath((Split-Path -Parent $TargetExe))) {
            throw "Shortcut working directory mismatch: $shortcut"
        }
    }
}

function Wait-Healthy([Diagnostics.Process]$Process) {
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($Process.HasExited) { throw "FastOrder exited before /livez became ready." }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8765/livez" -TimeoutSec 1
            if ($response.StatusCode -eq 200) { return }
        } catch { }
        Start-Sleep -Milliseconds 200
    }
    throw "Installed FastOrder.exe did not become ready."
}

function Stop-SmokeProcess([Diagnostics.Process]$Process) {
    if ($null -eq $Process -or $Process.HasExited) { return }
    Stop-Process -Id $Process.Id -Force
    $Process.WaitForExit(5000)
}

function Assert-RunningInstallRejected([string]$Setup, [string]$TargetDir, [string]$TargetExe) {
    $running = Start-Process -FilePath $TargetExe -ArgumentList "--no-browser" -WorkingDirectory $TargetDir -PassThru -WindowStyle Hidden
    try {
        Wait-Healthy $running
        $code = Invoke-Installer $Setup $TargetDir
        if ($code -eq 0) { throw "Installer overwrote an installation while FastOrder was running." }
    } finally {
        Stop-SmokeProcess $running
    }
}

function Assert-InstalledVersion([string]$Expected) {
    $paths = @(
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{5E1CC7C3-2A6F-4E71-9C2A-6F1BBE4D6D0A}_is1",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{5E1CC7C3-2A6F-4E71-9C2A-6F1BBE4D6D0A}_is1"
    )
    foreach ($path in $paths) {
        if (Test-Path -LiteralPath $path) {
            $version = [string](Get-ItemProperty -LiteralPath $path -Name DisplayVersion).DisplayVersion
            if ($version -eq $Expected) { return }
        }
    }
    throw "Expected installed version '$Expected' was not found in Add/Remove Programs."
}

$setup = (Resolve-Path -LiteralPath $InstallerPath).Path
if (-not (Test-Path -LiteralPath $setup -PathType Leaf)) { throw "Installer does not exist: $setup" }
$setupSignature = Get-AuthenticodeSignature -LiteralPath $setup
if ($Mode -eq "InternalUnsignedRc" -and $setupSignature.Status -ne "NotSigned") {
    throw "Internal unsigned RC installer must have Authenticode status NotSigned."
}
if ($Mode -eq "FormalSigned" -and $setupSignature.Status -ne "Valid") {
    throw "Formal installer must have a Valid Authenticode signature."
}
if ($Mode -eq "FormalSigned" -and (-not $setupSignature.SignerCertificate -or -not $setupSignature.TimeStamperCertificate)) {
    throw "Formal installer must include a signer certificate and trusted timestamp."
}
$expectedName = if ($Mode -eq "InternalUnsignedRc") { "FastOrder-Setup-1.0.0-rc1.exe" } else { "FastOrder-Setup-1.0.0.exe" }
if ([IO.Path]::GetFileName($setup) -cne $expectedName) {
    throw "Installer file name does not match $Mode`: expected $expectedName."
}
$previous = $null
if ([string]::IsNullOrWhiteSpace($PreviousInstallerPath)) {
    throw "PreviousInstallerPath is required for the 0.9.9-qa upgrade smoke."
}
if ($PreviousInstallerPath) {
    $previous = (Resolve-Path -LiteralPath $PreviousInstallerPath).Path
    if (-not (Test-Path -LiteralPath $previous -PathType Leaf)) { throw "Previous installer does not exist: $previous" }
}

$resolvedInstallDir = [IO.Path]::GetFullPath($InstallDir)
$uninstallRegistryPaths = @(
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{5E1CC7C3-2A6F-4E71-9C2A-6F1BBE4D6D0A}_is1",
    "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{5E1CC7C3-2A6F-4E71-9C2A-6F1BBE4D6D0A}_is1"
)
if (-not $DisposableVmConfirmed) { throw "Installer lifecycle smoke is destructive to the test installation and requires -DisposableVmConfirmed." }
if ((Test-Path -LiteralPath $resolvedInstallDir) -or ($uninstallRegistryPaths | Where-Object { Test-Path -LiteralPath $_ })) {
    throw "A FastOrder installation already exists. Restore a clean disposable VM snapshot; this script will not remove it."
}
$installParent = Split-Path -Parent $resolvedInstallDir
New-Item -ItemType Directory -Force -Path $installParent | Out-Null
$qaLocalAppData = Join-Path ([IO.Path]::GetTempPath()) ("FastOrder Installer QA " + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $qaLocalAppData | Out-Null
$oldLocalAppData = $env:LOCALAPPDATA
$env:LOCALAPPDATA = $qaLocalAppData
$exe = Join-Path $resolvedInstallDir "FastOrder.exe"
$sentinel = Join-Path $qaLocalAppData "FastOrder\installer-data-preservation.sentinel"

try {
    # First exercise a genuinely clean installation of the current candidate.
    Assert-InstallerSucceeded $setup $resolvedInstallDir "Clean installation failed"
    if (-not (Test-Path -LiteralPath $exe)) { throw "Installed FastOrder.exe is missing." }
    Assert-InstalledWhitelist $resolvedInstallDir $ArtifactPolicyPath
    Assert-UiHashes $resolvedInstallDir $UiManifestPath
    Assert-Shortcuts $exe

    # smoke_test_exe.py exercises only /livez, /readyz, GET UI and authenticated
    # WebSocket state. It has no order-submit call, so installer QA cannot place
    # a real order.
    $exeSmoke = Join-Path $PSScriptRoot "smoke_test_exe.py"
    $smokeArguments = @($exeSmoke, "--exe", $exe, "--ui-manifest", $UiManifestPath)
    if ($Mode -eq "InternalUnsignedRc") { $smokeArguments += "--allow-credential-manager-unavailable" }
    & $PythonPath @smokeArguments
    if ($LASTEXITCODE -ne 0) { throw "Installed executable smoke failed." }

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $sentinel) | Out-Null
    [IO.File]::WriteAllText($sentinel, "preserve", [Text.UTF8Encoding]::new($false))
    $uninstaller = Join-Path $resolvedInstallDir "unins000.exe"
    if (-not (Test-Path -LiteralPath $uninstaller)) { throw "Uninstaller is missing." }
    & $uninstaller /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
    if ($LASTEXITCODE -ne 0) { throw "Uninstall failed." }
    if (-not (Test-Path -LiteralPath $sentinel)) { throw "Uninstaller deleted LocalAppData user data." }

    # Install the QA-only predecessor, then prove the same AppId upgrades to RC1.
    Assert-InstallerSucceeded $previous $resolvedInstallDir "0.9.9-qa prerequisite installation failed"
    Assert-InstalledVersion $PreviousVersion
    Assert-InstallerSucceeded $setup $resolvedInstallDir "0.9.9-qa to 1.0.0 upgrade failed"
    Assert-InstalledVersion "1.0.0"
    if (-not (Test-Path -LiteralPath $sentinel)) { throw "Upgrade deleted LocalAppData user data." }
    Assert-InstalledWhitelist $resolvedInstallDir $ArtifactPolicyPath
    Assert-UiHashes $resolvedInstallDir $UiManifestPath

    Assert-RunningInstallRejected $setup $resolvedInstallDir $exe
    Assert-InstallerSucceeded $setup $resolvedInstallDir "Same-version repair failed"
    if (-not (Test-Path -LiteralPath $sentinel)) { throw "Repair deleted LocalAppData user data." }

    $uninstaller = Join-Path $resolvedInstallDir "unins000.exe"
    & $uninstaller /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
    if ($LASTEXITCODE -ne 0) { throw "Post-upgrade uninstall failed." }
    if (-not (Test-Path -LiteralPath $sentinel)) { throw "Post-upgrade uninstall deleted LocalAppData user data." }

    Assert-InstallerSucceeded $setup $resolvedInstallDir "Reinstallation after uninstall failed"
    if (-not (Test-Path -LiteralPath $exe)) { throw "FastOrder.exe is missing after reinstall." }
    Assert-InstalledWhitelist $resolvedInstallDir $ArtifactPolicyPath
    Assert-UiHashes $resolvedInstallDir $UiManifestPath
    Assert-Shortcuts $exe
    $uninstaller = Join-Path $resolvedInstallDir "unins000.exe"
    & $uninstaller /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
    if ($LASTEXITCODE -ne 0) { throw "Final uninstall failed." }
    if (-not (Test-Path -LiteralPath $sentinel)) { throw "Final uninstall deleted LocalAppData user data." }
    Write-Output "Installer lifecycle smoke passed: whitelist/UI hash, shortcuts, launch/restart, repair, 0.9.9-qa upgrade, running overwrite rejection, uninstall/reinstall, and user-data retention."
} finally {
    $env:LOCALAPPDATA = $oldLocalAppData
}
