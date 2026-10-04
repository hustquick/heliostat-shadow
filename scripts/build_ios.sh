#!/bin/zsh
set -euo pipefail

ROOT="${0:A:h:h}"
VERSION="${1:-$(<"${ROOT}/VERSION")}" 
BUILD="${ROOT}/build/ios"
OUTPUT="${ROOT}/dist/ios"
XCFRAMEWORK="${ROOT}/ios/Frameworks/HeliostatCore.xcframework"
XCODEGEN_BIN="${XCODEGEN_BIN:-xcodegen}"

if ! command -v cargo >/dev/null 2>&1; then echo "Rust toolchain is required." >&2; exit 1; fi
if ! command -v "${XCODEGEN_BIN}" >/dev/null 2>&1; then echo "XcodeGen is required." >&2; exit 1; fi

python3 - "${BUILD}" "${OUTPUT}" "${XCFRAMEWORK}" <<'PY'
from pathlib import Path
import shutil,sys
for value in sys.argv[1:]:
    path=Path(value)
    if path.exists(): shutil.rmtree(path)
PY
mkdir -p "${BUILD}" "${OUTPUT}" "${ROOT}/ios/Frameworks"
PYTHON_BIN="${PYTHON:-${HOME}/venv/bin/python}"
if [[ ! -x "${PYTHON_BIN}" ]]; then PYTHON_BIN="python3"; fi
"${PYTHON_BIN}" "${ROOT}/scripts/version_info.py"
"${PYTHON_BIN}" "${ROOT}/scripts/build_mobile_bundle.py"

rustup target add aarch64-apple-ios aarch64-apple-ios-sim x86_64-apple-ios
for target in aarch64-apple-ios aarch64-apple-ios-sim x86_64-apple-ios; do
  cargo build --manifest-path "${ROOT}/rust/Cargo.toml" --release --target "${target}"
done
lipo -create \
  "${ROOT}/rust/target/aarch64-apple-ios-sim/release/lib_heliostat_rust.a" \
  "${ROOT}/rust/target/x86_64-apple-ios/release/lib_heliostat_rust.a" \
  -output "${BUILD}/libHeliostatCore-simulator.a"
xcodebuild -create-xcframework \
  -library "${ROOT}/rust/target/aarch64-apple-ios/release/lib_heliostat_rust.a" \
  -headers "${ROOT}/rust/heliostat-core/include" \
  -library "${BUILD}/libHeliostatCore-simulator.a" \
  -headers "${ROOT}/rust/heliostat-core/include" \
  -output "${XCFRAMEWORK}"

(cd "${ROOT}/ios" && "${XCODEGEN_BIN}" generate)
xcodebuild -project "${ROOT}/ios/HeliostatViewerIOS.xcodeproj" -scheme HeliostatViewer \
  -configuration Release -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  MARKETING_VERSION="$(cat "${ROOT}/VERSION")" CURRENT_PROJECT_VERSION="$(cat "${ROOT}/BUILD_NUMBER")" \
  CODE_SIGNING_ALLOWED=NO CONFIGURATION_BUILD_DIR="${BUILD}/simulator" build
ditto -c -k --sequesterRsrc --keepParent "${BUILD}/simulator/塔式镜场设计与优化.app" \
  "${OUTPUT}/Heliostat-Viewer-iOS-Simulator-v${VERSION}.zip"
shasum -a 256 "${OUTPUT}/Heliostat-Viewer-iOS-Simulator-v${VERSION}.zip" > \
  "${OUTPUT}/Heliostat-Viewer-iOS-Simulator-v${VERSION}.zip.sha256"

echo "iOS simulator package: ${OUTPUT}/Heliostat-Viewer-iOS-Simulator-v${VERSION}.zip"
echo "A physical-device archive requires an Apple Development/Distribution signing identity."
