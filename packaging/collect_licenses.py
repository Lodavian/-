"""Collect unmodified license/notice files for the locked runtime dependency closure."""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import re
import shutil
from collections import deque
from pathlib import Path


ROOT_REQUIREMENTS = ("aiohttp", "keyring", "tzdata", "webull-openapi-python-sdk")
LICENSE_WORDS = ("license", "copying", "notice", "copyright")


def requirement_name(value: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", value)
    return match.group(1) if match else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    queue = deque(ROOT_REQUIREMENTS)
    seen: set[str] = set()
    records: list[tuple[str, str, list[str]]] = []
    while queue:
        requested = queue.popleft()
        try:
            distribution = metadata.distribution(requested)
        except metadata.PackageNotFoundError:
            continue
        canonical = distribution.metadata["Name"]
        key = canonical.lower().replace("_", "-")
        if key in seen:
            continue
        seen.add(key)
        for requirement in distribution.requires or ():
            marker = requirement.partition(";")[2].lower()
            if "extra ==" in marker or "extra==" in marker:
                continue
            name = requirement_name(requirement)
            if name:
                try:
                    metadata.distribution(name)
                except metadata.PackageNotFoundError:
                    continue
                queue.append(name)

        copied: list[str] = []
        package_dir = output / f"{canonical}-{distribution.version}"
        for item in distribution.files or ():
            lowered = item.name.lower()
            if not any(word in lowered for word in LICENSE_WORDS):
                continue
            source = Path(distribution.locate_file(item))
            if not source.is_file():
                continue
            target = package_dir / item.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied.append(target.relative_to(output).as_posix())
        if not copied:
            raise RuntimeError(f"No license/notice file found for runtime distribution {canonical}")
        records.append((canonical, distribution.version, sorted(copied)))

    lines = ["# Third-party notices", "", "Files below are copied unmodified from installed distributions.", ""]
    for name, version, files in sorted(records):
        lines.extend((f"## {name} {version}", ""))
        lines.extend(f"- `{item}`" for item in files)
        lines.append("")
    (output / "THIRD_PARTY_NOTICES.md").write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
