"""Offline source/UI and Nuitka artifact audit for FastOrder.

This module intentionally uses only the Python standard library so it can run
before the build environment has installed any optional tooling.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import sys
from pathlib import Path


PACKAGING_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGING_DIR.parent
TEXT_SUFFIXES = {
    ".c", ".cfg", ".conf", ".css", ".csv", ".html", ".ini", ".js", ".json",
    ".md", ".pem", ".ps1", ".py", ".toml", ".txt", ".xml", ".yaml", ".yml",
}


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 JSON 配置: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON 根节点必须是对象: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def audit_ui(source_root: Path, manifest_path: Path) -> list[str]:
    manifest = _load(manifest_path)
    if manifest.get("algorithm") != "sha256":
        return [f"不支持的 UI hash 算法: {manifest.get('algorithm')!r}"]
    expected = manifest.get("files")
    if not isinstance(expected, dict) or not expected:
        return ["UI hash 清单为空"]
    errors: list[str] = []
    for relative, wanted in expected.items():
        path = source_root / Path(str(relative).replace("/", "\\"))
        if not path.is_file():
            errors.append(f"UI 文件缺失: {relative}")
            continue
        actual = _sha256(path)
        if actual.lower() != str(wanted).lower():
            errors.append(f"UI hash 不匹配: {relative} (expected {wanted}, got {actual})")
    return errors


def _matches_forbidden(relative: str, patterns: list[str]) -> bool:
    normalized = relative.replace("\\", "/")
    return any(
        fnmatch.fnmatch(Path(normalized).name, pattern)
        or fnmatch.fnmatch(normalized, pattern)
        for pattern in patterns
    )


def _scan_text(path: Path, patterns: list[str]) -> list[str]:
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    return [pattern for pattern in patterns if re.search(pattern, text)]


def audit_artifact(dist: Path, policy_path: Path, manifest_out: Path | None) -> tuple[list[str], dict]:
    policy = _load(policy_path)
    errors: list[str] = []
    required = [str(item).replace("\\", "/") for item in policy.get("requiredPaths", [])]
    required += [str(item).replace("\\", "/") for item in policy.get("requiredPackageData", [])]
    for relative in required:
        if not (dist / Path(relative.replace("/", "\\"))).is_file():
            errors.append(f"制品缺少必需文件: {relative}")

    forbidden = [str(item) for item in policy.get("forbiddenNames", [])]
    allowed_dirs = {str(item).lower() for item in policy.get("allowedRootDirectories", [])}
    allowed_root_patterns = [str(item).lower() for item in policy.get("allowedRootPatterns", [])]
    allowed_extensions = {str(item).lower() for item in policy.get("allowedRootExtensions", [])}
    secret_patterns = [str(item) for item in policy.get("textSecretPatterns", [])]
    files: dict[str, dict[str, object]] = {}
    for path in sorted(item for item in dist.rglob("*") if item.is_file()):
        relative = _relative(path, dist)
        root = relative.split("/", 1)[0].lower()
        if "/" not in relative and path.suffix.lower() in allowed_extensions:
            root_allowed = True
        else:
            root_allowed = root in allowed_dirs or any(fnmatch.fnmatch(root, pattern) for pattern in allowed_root_patterns)
        if not root_allowed:
            errors.append(f"制品路径不在白名单: {relative}")
        if _matches_forbidden(relative, forbidden):
            errors.append(f"制品包含禁止文件: {relative}")
        found = _scan_text(path, secret_patterns)
        if found:
            errors.append(f"制品疑似包含密钥 ({len(found)} patterns): {relative}")
        files[relative] = {"sha256": _sha256(path), "bytes": path.stat().st_size}

        # Compilers and tracebacks must not embed this developer machine's
        # source location. Check both ordinary and UTF-16LE strings in every
        # file, including PE/DLL/PYD binaries.
        try:
            payload = path.read_bytes()
        except OSError:
            payload = b""
        source_text = str(PROJECT_ROOT.resolve())
        for marker in (source_text.encode("utf-8"), source_text.encode("utf-16le")):
            if marker and marker in payload:
                errors.append(f"制品泄漏开发机绝对路径: {relative}")
                break

    manifest = {"algorithm": "sha256", "root": dist.name, "files": files}
    if manifest_out:
        manifest_out.parent.mkdir(parents=True, exist_ok=True)
        manifest_out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return errors, manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FastOrder offline UI/artifact audit")
    parser.add_argument("--source-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--ui-manifest", type=Path, default=PACKAGING_DIR / "ui-hashes.json")
    parser.add_argument("--dist", type=Path)
    parser.add_argument("--policy", type=Path, default=PACKAGING_DIR / "artifact-policy.json")
    parser.add_argument("--manifest-out", type=Path)
    args = parser.parse_args(argv)

    errors = audit_ui(args.source_root.resolve(), args.ui_manifest.resolve())
    if args.dist:
        errors.extend(f"dist: {item}" for item in audit_ui(args.dist.resolve(), args.ui_manifest.resolve()))
        artifact_errors, _ = audit_artifact(args.dist.resolve(), args.policy.resolve(), args.manifest_out)
        errors.extend(artifact_errors)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("FastOrder audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
