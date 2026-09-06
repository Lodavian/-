"""Validate FastOrder release evidence without mutating files."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

GATES = ("A", "B", "C", "D")
STATUSES = {"PASS", "FAIL", "BLOCKED", "NOT_RUN", "WAIVED"}
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
GIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")
THUMBPRINT_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")
FROZEN_UI = {
    "static/index.html": "BBC49478141348365F513D84F9B8AAD96C1A9CCE2DD796B92D37A46941F8349D",
    "static/styles.css": "528E6E634C199BBAB1A45379247C9489308512E5AD04B8B68B5922CD3B34EFE0",
    "static/app.js": "C6DFA98A139761EDDCDBD03117EBE155475A28FFB8447A13BD3CA7218513963B",
}


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read evidence JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("evidence root must be a JSON object")
    return value


def _is_iso_utc(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
        return True
    except ValueError:
        return False


def validate_evidence(evidence: dict[str, Any], *, require_formal: bool = False) -> list[str]:
    errors: list[str] = []
    if evidence.get("schemaVersion") != 2:
        errors.append("schemaVersion must be 2")
    if evidence.get("product") != "FastOrder":
        errors.append("product must be FastOrder")
    release_class = evidence.get("releaseClass")
    if release_class not in {"formal", "internal", "sandbox"}:
        errors.append("releaseClass must be formal, internal, or sandbox")
    if require_formal and release_class != "formal":
        errors.append("formal manifest requires releaseClass=formal")

    candidate = evidence.get("candidate")
    if not isinstance(candidate, dict):
        errors.append("candidate metadata is missing")
        candidate = {}
    else:
        if candidate.get("environment") not in {"LIVE", "SANDBOX"}:
            errors.append("candidate environment must be LIVE or SANDBOX")
        for key in ("commit", "tree"):
            if not isinstance(candidate.get(key), str):
                errors.append(f"candidate.{key} must be a string")
        for key in ("executable", "installer", "policy"):
            artifact = candidate.get(key)
            if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"} or not all(isinstance(artifact.get(field), str) for field in ("path", "sha256")):
                errors.append(f"candidate.{key} must contain path and sha256 strings")
        ui = candidate.get("uiHashes")
        if not isinstance(ui, dict) or set(ui) != {"source", "standalone", "installed"}:
            errors.append("candidate.uiHashes must contain source, standalone and installed")
        else:
            for stage in ("source", "standalone", "installed"):
                if not isinstance(ui.get(stage), dict) or set(ui[stage]) != set(FROZEN_UI) or not all(isinstance(value, str) for value in ui[stage].values()):
                    errors.append(f"candidate.uiHashes.{stage} has an invalid shape")
    if require_formal:
        if not GIT_RE.fullmatch(str(candidate.get("commit", ""))):
            errors.append("formal candidate requires a 40-hex commit")
        if not GIT_RE.fullmatch(str(candidate.get("tree", ""))):
            errors.append("formal candidate requires a 40-hex tree")
        if candidate.get("environment") != "LIVE":
            errors.append("formal candidate environment must be LIVE")
        for key in ("executable", "installer", "policy"):
            artifact = candidate.get(key)
            if not isinstance(artifact, dict) or not artifact.get("path") or not SHA256_RE.fullmatch(str(artifact.get("sha256", ""))):
                errors.append(f"formal candidate {key} requires path and SHA256")
        installer_name = Path(str(candidate.get("installer", {}).get("path", ""))).name.lower()
        if "-rc" in installer_name or installer_name != "fastorder-setup-1.0.0.exe":
            errors.append("unsigned/internal RC installer cannot enter a formal manifest")
        ui = candidate.get("uiHashes")
        for stage in ("source", "standalone", "installed"):
            values = ui.get(stage) if isinstance(ui, dict) else None
            for name, expected in FROZEN_UI.items():
                if not isinstance(values, dict) or str(values.get(name, "")).upper() != expected:
                    errors.append(f"{stage} UI hash mismatch: {name}")

    qa = evidence.get("qaEnvironment")
    if not isinstance(qa, dict):
        errors.append("qaEnvironment is missing")
    else:
        qa_keys = ("windowsVersion", "userType", "python", "nuitka", "msvc", "windowsSdk", "innoSetup")
        if set(qa) != set(qa_keys) or not all(isinstance(qa.get(key), str) for key in qa_keys):
            errors.append("qaEnvironment has an invalid shape")
    if require_formal and isinstance(qa, dict):
        for key in ("windowsVersion", "userType", "python", "nuitka", "msvc", "windowsSdk", "innoSetup"):
            if not str(qa.get(key, "")).strip():
                errors.append(f"formal qaEnvironment.{key} is required")

    gates = evidence.get("gates")
    if not isinstance(gates, dict):
        errors.append("gates must contain Gate A-D")
        gates = {}
    for gate in GATES:
        item = gates.get(gate)
        if not isinstance(item, dict):
            errors.append(f"Gate {gate} evidence is missing")
            continue
        status = item.get("status")
        if status not in STATUSES:
            errors.append(f"Gate {gate} has an invalid status: {status!r}")
        if status == "WAIVED" and gate != "B":
            errors.append(f"Only Gate B may be WAIVED; Gate {gate} is invalid")
        elif require_formal and gate != "B" and status != "PASS":
            errors.append(f"Gate {gate} is not PASS: {status}")
        required_gate_keys = {"status", "tester", "reviewer", "startedAtUtc", "completedAtUtc", "evidenceRef", "redactionVerified", "blocker", "notes"}
        if set(item) != required_gate_keys or not all(isinstance(item.get(key), str) for key in ("tester", "reviewer", "startedAtUtc", "completedAtUtc", "blocker", "notes")) or not isinstance(item.get("redactionVerified"), bool) or not isinstance(item.get("evidenceRef"), list):
            errors.append(f"Gate {gate} has an invalid evidence shape")
        elif any(not isinstance(ref, dict) or set(ref) != {"stepId", "path", "sha256"} or not all(isinstance(ref.get(key), str) for key in ("stepId", "path", "sha256")) for ref in item["evidenceRef"]):
            errors.append(f"Gate {gate} has an invalid evidence reference shape")
        if require_formal and status == "PASS":
            for key in ("tester", "reviewer"):
                if not str(item.get(key, "")).strip():
                    errors.append(f"Gate {gate} {key} is required")
            for key in ("startedAtUtc", "completedAtUtc"):
                if not _is_iso_utc(item.get(key)):
                    errors.append(f"Gate {gate} {key} must be an ISO UTC timestamp")
            refs = item.get("evidenceRef")
            if not isinstance(refs, list) or not refs:
                errors.append(f"Gate {gate} evidenceRef must not be empty")
            else:
                for ref in refs:
                    if not isinstance(ref, dict) or not str(ref.get("stepId", "")).startswith(f"{gate}-") or not str(ref.get("path", "")).strip() or not SHA256_RE.fullmatch(str(ref.get("sha256", ""))):
                        errors.append(f"Gate {gate} has an invalid step evidence reference")
            if item.get("redactionVerified") is not True:
                errors.append(f"Gate {gate} redactionVerified must be true")
            if str(item.get("blocker", "")).strip():
                errors.append(f"Gate {gate} still records a blocker")

    waiver = evidence.get("sandboxQaWaiver")
    live_qa = evidence.get("liveManualQa")
    gate_b_status = gates.get("B", {}).get("status") if isinstance(gates.get("B"), dict) else None
    if gate_b_status == "WAIVED":
        if not isinstance(waiver, dict) or waiver.get("status") != "APPROVED" or not str(waiver.get("approvedBy", "")).strip() or not _is_iso_utc(waiver.get("approvedAtUtc")) or not str(waiver.get("reason", "")).strip():
            errors.append("WAIVED Gate B requires an approved, dated owner waiver")
        if not isinstance(live_qa, dict) or live_qa.get("status") != "PASS":
            errors.append("WAIVED Gate B requires liveManualQa=PASS")
    elif require_formal and gate_b_status != "PASS":
        errors.append(f"Gate B is neither PASS nor an approved WAIVED replacement: {gate_b_status}")
    if not isinstance(waiver, dict) or set(waiver) != {"status", "approvedBy", "approvedAtUtc", "reason"}:
        errors.append("sandboxQaWaiver has an invalid shape")
    if not isinstance(live_qa, dict):
        errors.append("liveManualQa evidence is missing")
    else:
        live_keys = {"status", "tester", "reviewer", "startedAtUtc", "completedAtUtc", "evidenceRef", "redactionVerified", "blocker", "notes"}
        if set(live_qa) != live_keys or live_qa.get("status") not in STATUSES:
            errors.append("liveManualQa has an invalid shape")
        if require_formal and gate_b_status == "WAIVED":
            if not all(str(live_qa.get(key, "")).strip() for key in ("tester", "reviewer")) or not _is_iso_utc(live_qa.get("startedAtUtc")) or not _is_iso_utc(live_qa.get("completedAtUtc")) or not live_qa.get("evidenceRef") or live_qa.get("redactionVerified") is not True or str(live_qa.get("blocker", "")).strip():
                errors.append("formal liveManualQa replacement evidence is incomplete")

    scan = evidence.get("credentialScan")
    if not isinstance(scan, dict):
        errors.append("credentialScan evidence is missing")
    else:
        scan_keys = {"status", "findings", "scannedRoots", "completedAtUtc", "evidenceSha256"}
        if set(scan) != scan_keys or scan.get("status") not in STATUSES or not isinstance(scan.get("findings"), list) or not isinstance(scan.get("scannedRoots"), list) or not isinstance(scan.get("completedAtUtc"), str) or not isinstance(scan.get("evidenceSha256"), str):
            errors.append("credentialScan has an invalid shape")
    if require_formal and isinstance(scan, dict):
        if scan.get("status") != "PASS" or scan.get("findings") != []:
            errors.append("credential scan must PASS with no findings")
        if not scan.get("scannedRoots") or not _is_iso_utc(scan.get("completedAtUtc")) or not SHA256_RE.fullmatch(str(scan.get("evidenceSha256", ""))):
            errors.append("credential scan requires roots, UTC time, and evidence SHA256")

    signature = evidence.get("signature")
    if not isinstance(signature, dict):
        errors.append("signature evidence is missing")
    else:
        signature_keys = {"status", "timestampStatus", "timestampUrl", "signerSubject", "signerThumbprint"}
        if set(signature) != signature_keys or not all(isinstance(signature.get(key), str) for key in signature_keys):
            errors.append("signature evidence has an invalid shape")
    if require_formal and isinstance(signature, dict):
        if str(signature.get("status", "")).lower() != "valid":
            errors.append("formal release requires a Valid Authenticode signature")
        if str(signature.get("timestampStatus", "")).lower() != "valid":
            errors.append("formal release requires a Valid RFC3161 timestamp")
        if not str(signature.get("timestampUrl", "")).lower().startswith("https://"):
            errors.append("formal release requires an HTTPS timestampUrl")
        if not str(signature.get("signerSubject", "")).strip() or not THUMBPRINT_RE.fullmatch(str(signature.get("signerThumbprint", ""))):
            errors.append("formal release requires signer subject and certificate thumbprint")
    if require_formal and evidence.get("redactionVerified") is not True:
        errors.append("formal evidence requires redactionVerified=true")
    if not isinstance(evidence.get("redactionVerified"), bool):
        errors.append("redactionVerified must be boolean")
    if not isinstance(evidence.get("approver"), str) or not isinstance(evidence.get("notes"), str):
        errors.append("approver and notes must be strings")
    if require_formal and not str(evidence.get("approver", "")).strip():
        errors.append("formal evidence requires a second-person approver")
    return errors


def validate_evidence_file(path: Path, *, require_formal: bool = False) -> tuple[list[str], dict[str, Any]]:
    evidence = _load(path)
    return validate_evidence(evidence, require_formal=require_formal), evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate FastOrder Gate A-D release evidence")
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--formal", action="store_true")
    args = parser.parse_args(argv)
    try:
        errors, _ = validate_evidence_file(args.evidence.resolve(), require_formal=args.formal)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    print("Release evidence validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
