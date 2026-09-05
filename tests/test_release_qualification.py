import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release"
PACKAGING = ROOT / "packaging"
FROZEN = {
    "static/index.html": "BBC49478141348365F513D84F9B8AAD96C1A9CCE2DD796B92D37A46941F8349D",
    "static/styles.css": "528E6E634C199BBAB1A45379247C9489308512E5AD04B8B68B5922CD3B34EFE0",
    "static/app.js": "C6DFA98A139761EDDCDBD03117EBE155475A28FFB8447A13BD3CA7218513963B",
}


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, RELEASE / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _formal_evidence():
    value = json.loads((RELEASE / "evidence-template.json").read_text(encoding="utf-8"))
    value["releaseClass"] = "formal"
    value["candidate"].update({"commit": "a" * 40, "tree": "b" * 40, "environment": "LIVE"})
    value["candidate"]["executable"] = {"path": "FastOrder.exe", "sha256": "1" * 64}
    value["candidate"]["installer"] = {"path": "FastOrder-Setup-1.0.0.exe", "sha256": "2" * 64}
    value["candidate"]["policy"]["sha256"] = "3" * 64
    value["candidate"]["uiHashes"] = {stage: dict(FROZEN) for stage in ("source", "standalone", "installed")}
    value["qaEnvironment"] = {key: "recorded" for key in ("windowsVersion", "userType", "python", "nuitka", "msvc", "windowsSdk", "innoSetup")}
    for name, gate in value["gates"].items():
        gate.update({
            "status": "PASS", "tester": "tester", "reviewer": "reviewer",
            "startedAtUtc": "2026-09-06T01:00:00Z", "completedAtUtc": "2026-09-06T02:00:00Z",
            "evidenceRef": [{"stepId": f"{name}-STEP-001", "path": f"evidence/{name}.txt", "sha256": "4" * 64}],
            "redactionVerified": True, "blocker": "",
        })
    value["credentialScan"] = {"status": "PASS", "findings": [], "scannedRoots": ["Program Files/FastOrder", "LocalAppData/FastOrder"], "completedAtUtc": "2026-09-06T03:00:00Z", "evidenceSha256": "5" * 64}
    value["signature"] = {"status": "Valid", "timestampStatus": "Valid", "timestampUrl": "https://timestamp.example.test", "signerSubject": "CN=Legal Publisher", "signerThumbprint": "A" * 40}
    value["redactionVerified"] = True
    value["approver"] = "second reviewer"
    return value


def test_evidence_template_is_fail_closed_and_freezes_ui():
    evidence = json.loads((RELEASE / "evidence-template.json").read_text(encoding="utf-8"))
    assert evidence["schemaVersion"] == 2
    assert evidence["releaseClass"] == "internal"
    assert set(evidence["gates"]) == {"A", "B", "C", "D"}
    assert evidence["gates"]["C"]["status"] == "BLOCKED"
    assert evidence["gates"]["D"]["status"] == "BLOCKED"
    assert evidence["candidate"]["uiHashes"]["source"] == FROZEN


def test_formal_evidence_requires_all_release_gates_and_identity():
    validator = _load("release_evidence_validator", "validate_release_evidence.py")
    evidence = _formal_evidence()
    assert validator.validate_evidence(evidence, require_formal=True) == []
    cases = [
        (lambda item: item["gates"]["C"].update(status="BLOCKED"), "Gate C"),
        (lambda item: item.update(releaseClass="internal"), "releaseClass=formal"),
        (lambda item: item["signature"].update(timestampStatus="UNRECORDED"), "RFC3161"),
        (lambda item: item["candidate"]["installer"].update(path="FastOrder-Setup-1.0.0-rc1.exe"), "unsigned/internal RC"),
        (lambda item: item["candidate"]["uiHashes"]["installed"].update({"static/app.js": "0" * 64}), "UI hash mismatch"),
        (lambda item: item.update(redactionVerified=False), "redactionVerified"),
    ]
    for mutate, expected in cases:
        invalid = copy.deepcopy(evidence)
        mutate(invalid)
        assert any(expected in error for error in validator.validate_evidence(invalid, require_formal=True))


def test_credential_scan_exact_values_is_read_only_and_redacted(tmp_path):
    scanner = _load("release_credential_scanner", "scan_release_credentials.py")
    clean = tmp_path / "clean.txt"
    clean.write_text("API_SECRET=${API_SECRET}\n", encoding="utf-8")
    assert scanner.scan_tree(tmp_path) == []
    secret = bytearray(b"dummy-secret-for-qa")
    leaked = tmp_path / "cache.bin"
    leaked.write_bytes(b"prefix:" + bytes(secret))
    before = leaked.read_bytes()
    findings = scanner.scan_tree(tmp_path, exact_values={"app-secret": secret})
    rendered = json.dumps(findings)
    assert {item["rule"] for item in findings} == {"exact-app-secret-utf8"}
    assert "dummy-secret-for-qa" not in rendered
    assert leaked.read_bytes() == before
    scanner._zero([secret])
    assert secret == bytearray(len(secret))


def test_installer_modes_are_mutually_exclusive_and_fail_closed():
    iss = (PACKAGING / "FastOrder.iss").read_text(encoding="utf-8")
    build = (PACKAGING / "build_installer.ps1").read_text(encoding="utf-8")
    smoke = (PACKAGING / "smoke_test_installer.ps1").read_text(encoding="utf-8")
    assert "InternalUnsignedRc and FormalSigned are mutually exclusive" in iss
    assert "SignedUninstaller=no" in iss and "SignedUninstaller=yes" in iss
    assert "FastOrder-Setup-1.0.0-rc1" in iss and "FastOrder-Setup-1.0.0" in iss
    assert "CommercialLicenseConfirmed" in build
    for required in ("Publisher", "CertificateThumbprint", "TimestampUrl"):
        assert required in build
    assert "0.9.9-qa" in build and "installer-qa-fixtures" in build
    assert "DisposableVmConfirmed" in smoke
    assert "Credential Manager" not in smoke or "allow-credential-manager-unavailable" in smoke
    assert "Shortcut working directory mismatch" in smoke


def test_release_scripts_require_formal_evidence_and_signed_artifacts():
    manifest = (RELEASE / "New-BuildManifest.ps1").read_text(encoding="utf-8")
    assert "releaseClass=formal" in manifest
    assert '"A", "B", "C", "D"' in manifest
    assert "validate_release_evidence.py" in manifest and "--formal" in manifest
    assert "TimeStamperCertificate" in manifest
    assert "Source Git identity does not match" in manifest
    assert "candidate.uiHashes.standalone" in manifest
    assert "& $PythonPath --version" in manifest
    sums = (RELEASE / "Write-SHA256.ps1").read_text(encoding="utf-8")
    assert "-Recurse" in sums and "Get-FileHash" in sums


def test_gate_checklists_cover_required_release_qualification_scenarios():
    gate_a = (RELEASE / "gate-a-credential-qa.md").read_text(encoding="utf-8")
    for phrase in ("自动连接", "切换账户", "scan_release_credentials.py --exact-credentials", "UNBOUND"):
        assert phrase in gate_a
    gate_b = (RELEASE / "gate-b-sandbox-qa.md").read_text(encoding="utf-8")
    for phrase in ("至少 2 分钟", "AAPL → MSFT → AAPL", "快速双击", "UNKNOWN", "MARKET_READY=false", "ORDER_EVENTS_READY=false"):
        assert phrase in gate_b
    gate_c = (RELEASE / "gate-c-installer-qa.md").read_text(encoding="utf-8")
    for phrase in ("FastOrder-Setup-1.0.0-rc1.exe", "NotSigned", "0.9.9-qa", "Windows 重启"):
        assert phrase in gate_c
