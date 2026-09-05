from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_inno_setup_has_stable_x64_install_and_data_safe_uninstall_contract():
    iss = (ROOT / "packaging" / "FastOrder.iss").read_text(encoding="utf-8")
    assert "AppId={{5E1CC7C3-2A6F-4E71-9C2A-6F1BBE4D6D0A}" in iss
    assert "AppVersion={#MyAppVersion}" in iss
    assert "DefaultDirName={autopf}\\FastOrder" in iss
    assert "ArchitecturesInstallIn64BitMode=x64compatible" in iss
    assert "SetupArchitecture=x64" in iss
    assert "AppMutex=Local\\FastOrder.V1" in iss
    assert "OutputBaseFilename=FastOrder-Setup-1.0.0" in iss
    assert "MinVersion=10.0.19045" in iss
    assert "ArchitecturesAllowed=x64compatible" in iss
    assert "InstalledPacked > CandidatePacked" in iss
    assert "runasoriginaluser" in iss
    assert "AppPublisherURL" not in iss
    assert "SignTool={#SignToolName}" in iss
    assert "SignedUninstaller=yes" in iss
    assert "Credential Manager" in iss
    assert "\n[UninstallDelete]" not in iss


def test_release_scripts_are_hash_and_signing_gated():
    release = ROOT / "release"
    assert "Get-FileHash" in (release / "Write-SHA256.ps1").read_text(encoding="utf-8")
    manifest = (release / "New-BuildManifest.ps1").read_text(encoding="utf-8")
    assert "sourceCommit" in manifest and "sha256" in manifest
    signing = (release / "Sign-Release.ps1").read_text(encoding="utf-8")
    assert "/fd SHA256" in signing and "/sha1 $CertificateThumbprint" in signing
    assert "/pa /all /v" in signing
    assert "CertificatePassword" not in signing
    installer = (ROOT / "packaging" / "build_installer.ps1").read_text(encoding="utf-8")
    assert "CommercialLicenseConfirmed" in installer
    assert "FastOrder.exe signature verification failed" in installer


def test_release_checklist_requires_sandbox_and_lifecycle_proofs():
    checklist = (ROOT / "release" / "checklist.md").read_text(encoding="utf-8")
    for phrase in ("Sandbox", "reinstall", "upgrade", "uninstall", "SHA256", "Authenticode"):
        assert phrase in checklist
