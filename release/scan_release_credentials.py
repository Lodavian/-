"""Read-only credential leak scanner; never prints supplied secret values."""

from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

TEXT_SUFFIXES = {".cfg", ".conf", ".css", ".csv", ".html", ".ini", ".js", ".json", ".log", ".md", ".pem", ".ps1", ".py", ".toml", ".txt", ".xml", ".yaml", ".yml"}
DEFAULT_RULES = (
    ("private-key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I)),
    ("authorization-header", re.compile(rb"authorization\s*:\s*(?:bearer|basic)\s+[^\s]+", re.I)),
    ("token-assignment", re.compile(rb"(?:access[_-]?token|refresh[_-]?token|api[_-]?secret|password)\s*[:=]\s*[^\s\"']{8,}", re.I)),
)


def _zero(buffers: Iterable[bytearray]) -> None:
    for value in buffers:
        for index in range(len(value)):
            value[index] = 0


def scan_tree(root: Path, policy_path: Path | None = None, exact_values: dict[str, bytearray] | None = None) -> list[dict[str, str]]:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"scan root is not a directory: {root}")
    findings: list[dict[str, str]] = []
    exact_needles: list[tuple[str, bytes]] = []
    for label, value in (exact_values or {}).items():
        if value:
            raw = bytes(value)
            exact_needles.extend(((f"exact-{label}-utf8", raw), (f"exact-{label}-utf16le", raw.decode("utf-8").encode("utf-16le"))))
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        lower = path.name.lower()
        if lower in {".env", ".env.local", ".env.production"} or path.suffix.lower() == ".key" or lower in {"id_rsa", "id_ed25519"}:
            findings.append({"path": relative, "rule": "credential-file-name"})
        try:
            payload = path.read_bytes()
        except OSError:
            continue
        for rule, needle in exact_needles:
            if needle and needle in payload:
                findings.append({"path": relative, "rule": rule})
        if path.suffix.lower() in TEXT_SUFFIXES or lower.startswith(".env"):
            for rule, pattern in DEFAULT_RULES:
                match = pattern.search(payload)
                if match and b"${" not in match.group(0) and b"[REDACTED]" not in match.group(0).upper():
                    findings.append({"path": relative, "rule": rule})
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only FastOrder credential leak scan")
    parser.add_argument("roots", nargs="+", type=Path, help="installation and LocalAppData roots")
    parser.add_argument("--policy", type=Path, help="reserved artifact-policy reference")
    parser.add_argument("--exact-credentials", action="store_true", help="securely prompt for App Key, App Secret and full Account ID")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    buffers: dict[str, bytearray] = {}
    try:
        if args.exact_credentials:
            for label, prompt in (("app-key", "App Key: "), ("app-secret", "App Secret: "), ("account-id", "Full Account ID: ")):
                entered = getpass.getpass(prompt)
                if not entered:
                    print(f"ERROR: {label} is required", file=sys.stderr)
                    return 2
                buffers[label] = bytearray(entered.encode("utf-8"))
                entered = ""
        all_findings: list[dict[str, str]] = []
        for root_arg in args.roots:
            root = root_arg.resolve()
            try:
                for finding in scan_tree(root, args.policy, buffers):
                    all_findings.append({"root": str(root), **finding})
            except ValueError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 2
        result = {"status": "PASS" if not all_findings else "FAIL", "findings": all_findings}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif all_findings:
            for finding in all_findings:
                print(f"ERROR: {finding['root']} :: {finding['path']} :: {finding['rule']}", file=sys.stderr)
        else:
            print("Credential leak scan passed")
        return 0 if not all_findings else 1
    finally:
        _zero(buffers.values())
        buffers.clear()


if __name__ == "__main__":
    raise SystemExit(main())
