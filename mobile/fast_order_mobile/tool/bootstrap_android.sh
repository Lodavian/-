#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
command -v flutter >/dev/null 2>&1 || { echo "Flutter SDK is required" >&2; exit 1; }
flutter create --platforms=android --org=com.fastorder --project-name=fast_order_mobile .
flutter pub get
flutter analyze
flutter test
