#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
PACKAGING_ROOT="$PROJECT_ROOT/packaging"
OUTPUT_ROOT="$PROJECT_ROOT/build/macos-arm64"
BUILD_VENV="$PROJECT_ROOT/build/.venv-macos311"
WORK_ROOT="${TMPDIR:-/tmp}/FastOrderBuild/macos-arm64"
APP_OUTPUT="$OUTPUT_ROOT/FastOrder.app"
PYTHON_BIN="${FAST_ORDER_PYTHON:-$(command -v python3.11 || true)}"
CLEAN=0

if [[ "${1:-}" == "--clean" ]]; then
  CLEAN=1
elif [[ $# -gt 0 ]]; then
  echo "Usage: ./packaging/build_macos.sh [--clean]" >&2
  exit 2
fi

fail() { echo "FastOrder macOS build failed: $*" >&2; exit 2; }
[[ "$(uname -s)" == "Darwin" ]] || fail "must run on macOS"
[[ "$(uname -m)" == "arm64" ]] || fail "native Apple Silicon arm64 host required"
MACOS_MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
[[ "$MACOS_MAJOR" =~ ^[0-9]+$ && "$MACOS_MAJOR" -ge 15 ]] || fail "macOS 15 or newer required"
[[ -n "$PYTHON_BIN" && -x "$PYTHON_BIN" ]] || fail "CPython 3.11.9 arm64 not found; set FAST_ORDER_PYTHON"
xcode-select -p >/dev/null 2>&1 || fail "Xcode Command Line Tools are required"
command -v clang >/dev/null 2>&1 || fail "Apple clang is required"
command -v codesign >/dev/null 2>&1 || fail "codesign is required"
command -v lipo >/dev/null 2>&1 || fail "lipo is required"

PYTHON_INFO="$($PYTHON_BIN -c 'import platform,sys; print(".".join(map(str,sys.version_info[:3]))); print(platform.machine())')"
[[ "$(printf '%s\n' "$PYTHON_INFO" | sed -n '1p')" == "3.11.9" ]] || fail "CPython 3.11.9 required"
[[ "$(printf '%s\n' "$PYTHON_INFO" | sed -n '2p')" == "arm64" ]] || fail "arm64 Python required"

GIT_STATE="$(git -C "$PROJECT_ROOT" status --porcelain --untracked-files=all)"
[[ -z "$GIT_STATE" ]] || fail "Git working tree must be completely clean"

if [[ "$CLEAN" == "1" ]]; then
  [[ "$OUTPUT_ROOT" == "$PROJECT_ROOT/build/macos-arm64" ]] || fail "unsafe output path"
  [[ "$WORK_ROOT" == "${TMPDIR:-/tmp}/FastOrderBuild/macos-arm64" ]] || fail "unsafe work path"
  rm -rf -- "$OUTPUT_ROOT" "$WORK_ROOT"
fi
mkdir -p "$PROJECT_ROOT/build" "$OUTPUT_ROOT" "$WORK_ROOT"

if [[ ! -x "$BUILD_VENV/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$BUILD_VENV"
fi
BUILD_PYTHON="$BUILD_VENV/bin/python"
BUILD_INFO="$($BUILD_PYTHON -c 'import platform,sys; print(".".join(map(str,sys.version_info[:3]))); print(platform.machine())')"
[[ "$(printf '%s\n' "$BUILD_INFO" | sed -n '1p')" == "3.11.9" ]] || fail "build venv is not CPython 3.11.9"
[[ "$(printf '%s\n' "$BUILD_INFO" | sed -n '2p')" == "arm64" ]] || fail "build venv is not arm64"

"$BUILD_PYTHON" -m pip install --disable-pip-version-check --require-hashes -r "$PACKAGING_ROOT/build-requirements.lock"

PAHO_INPUT="$WORK_ROOT/paho-input"
PAHO_WHEELS="$WORK_ROOT/paho-wheels"
rm -rf -- "$PAHO_INPUT" "$PAHO_WHEELS"
mkdir -p "$PAHO_INPUT" "$PAHO_WHEELS"
"$BUILD_PYTHON" -m pip download --disable-pip-version-check --no-deps --no-binary=:all: --dest "$PAHO_INPUT" paho-mqtt==1.6.1
printf '%s  %s\n' \
  '2a8291c81623aec00372b5a85558a372c747cbca8e9934dfe218638b8eefc26f' \
  "$PAHO_INPUT/paho-mqtt-1.6.1.tar.gz" | shasum -a 256 -c -
"$BUILD_PYTHON" -m pip wheel --disable-pip-version-check --no-deps --no-build-isolation \
  --wheel-dir "$PAHO_WHEELS" "$PAHO_INPUT/paho-mqtt-1.6.1.tar.gz"

grep -v '^paho-mqtt==' "$PACKAGING_ROOT/runtime-requirements-macos.lock" > "$WORK_ROOT/runtime-without-paho.lock"
"$BUILD_PYTHON" -m pip install --disable-pip-version-check --no-deps "$PAHO_WHEELS"/paho_mqtt-1.6.1-py3-none-any.whl
"$BUILD_PYTHON" -m pip install --disable-pip-version-check --only-binary=:all: \
  -r "$WORK_ROOT/runtime-without-paho.lock"
"$BUILD_PYTHON" -m pip install --disable-pip-version-check -r "$PROJECT_ROOT/requirements-dev.txt"
"$BUILD_PYTHON" -m pip check
NUITKA_VERSION="$($BUILD_PYTHON -c 'import importlib.metadata as m; print(m.version("Nuitka"))')"
[[ "$NUITKA_VERSION" == "4.2" || "$NUITKA_VERSION" == 4.2.* ]] || fail "Nuitka 4.2.x required"

"$BUILD_PYTHON" -m pytest -q --basetemp "$WORK_ROOT/pytest"
"$BUILD_PYTHON" -m compileall -q "$PROJECT_ROOT/app.py" "$PROJECT_ROOT/FastOrder.py" "$PROJECT_ROOT/fast_order"
"$BUILD_PYTHON" "$PACKAGING_ROOT/verify_build.py" --source-root "$PROJECT_ROOT"

LICENSES_ROOT="$WORK_ROOT/LICENSES"
"$BUILD_PYTHON" "$PACKAGING_ROOT/collect_licenses.py" --output "$LICENSES_ROOT"
rm -rf -- "$WORK_ROOT/FastOrder.app" "$WORK_ROOT/FastOrder.build"

NUITKA_ARGS=(
  --mode=app
  "--output-dir=$WORK_ROOT"
  --output-filename=FastOrder
  --python-flag=safe_path
  --macos-target-arch=arm64
  --macos-app-name=FastOrder
  --macos-signed-app-name=com.fastorder.local
  --macos-app-version=1.0.0
  --macos-app-macos-min-version=15.0
  --macos-app-console-mode=force
  "--macos-app-icon=$PACKAGING_ROOT/assets/FastOrder-icon-master.png"
  --nofollow-import-to=tests
  --nofollow-import-to=pytest
  --nofollow-import-to=packaging
  --include-package=fast_order
  --include-package=aiohttp
  --include-package=webull
  --include-package=webull.trade.events
  --include-package=grpc
  --include-package=paho
  --include-package=keyring
  --include-package=keyring.backends
  --include-module=keyring.backends.macOS
  --include-package=tzdata
  --include-package=certifi
  --include-package=google.protobuf
  --include-package=cryptography.hazmat.bindings
  --include-module=cryptography.hazmat.bindings._rust
  --include-module=sqlite3
  --include-module=_sqlite3
  --include-package-data=webull
  --include-package-data=grpc
  --include-package-data=certifi
  --include-package-data=tzdata
  --include-distribution-metadata=keyring
  "--report=$WORK_ROOT/compilation-report.xml"
  "$PROJECT_ROOT/FastOrder.py"
)

(cd "$PROJECT_ROOT" && "$BUILD_PYTHON" -m nuitka "${NUITKA_ARGS[@]}")
[[ -d "$WORK_ROOT/FastOrder.app" ]] || fail "Nuitka did not produce FastOrder.app"
rm -rf -- "$APP_OUTPUT"
ditto "$WORK_ROOT/FastOrder.app" "$APP_OUTPUT"
mkdir -p "$APP_OUTPUT/Contents/Resources"
ditto "$PROJECT_ROOT/static" "$APP_OUTPUT/Contents/Resources/static"
ditto "$PROJECT_ROOT/config" "$APP_OUTPUT/Contents/Resources/config"
ditto "$LICENSES_ROOT" "$APP_OUTPUT/Contents/Resources/LICENSES"
codesign --force --deep --sign - "$APP_OUTPUT"
codesign --verify --deep --strict --verbose=2 "$APP_OUTPUT"

cp "$WORK_ROOT/compilation-report.xml" "$OUTPUT_ROOT/compilation-report.xml"
"$BUILD_PYTHON" -m pip freeze --all > "$OUTPUT_ROOT/dependencies.txt"
"$BUILD_PYTHON" "$PACKAGING_ROOT/verify_macos_app.py" \
  --app "$APP_OUTPUT" \
  --source-root "$PROJECT_ROOT" \
  --ui-manifest "$PACKAGING_ROOT/ui-hashes.json" \
  --manifest-out "$OUTPUT_ROOT/artifact-manifest.json"
"$BUILD_PYTHON" "$PACKAGING_ROOT/smoke_test_macos.py" --app "$APP_OUTPUT"

echo "FastOrder macOS arm64 app ready: $APP_OUTPUT"
