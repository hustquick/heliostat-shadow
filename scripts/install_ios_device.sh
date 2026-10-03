#!/bin/zsh
set -euo pipefail

ROOT="${0:A:h:h}"
DEVICE_ID="${IOS_DEVICE_ID:-}"
TEAM_ID="${IOS_DEVELOPMENT_TEAM:-}"
BUNDLE_ID="${IOS_BUNDLE_ID:-com.hustquick.heliostatviewer.dev}"

if [[ -z "${DEVICE_ID}" ]]; then
  echo "Set IOS_DEVICE_ID to the connected iPhone UDID." >&2
  exit 2
fi
if [[ -z "${TEAM_ID}" ]]; then
  echo "Set IOS_DEVELOPMENT_TEAM to the Xcode team identifier." >&2
  exit 2
fi

APP_DIR="${ROOT}/build/ios-device/塔式镜场设计与优化.app"
mkdir -p "${ROOT}/build/ios-device"

# A device install must rebuild the native core as well as the WebView files.
# Otherwise Rust changes can leave the phone running an older calculation engine.
PYTHON_BIN="${PYTHON:-${HOME}/venv/bin/python}"
if [[ ! -x "${PYTHON_BIN}" ]]; then PYTHON_BIN="python3"; fi
"${PYTHON_BIN}" "${ROOT}/scripts/build_mobile_bundle.py"
rustup target add aarch64-apple-ios
cargo build --manifest-path "${ROOT}/rust/Cargo.toml" --release --target aarch64-apple-ios
rm -rf "${ROOT}/ios/Frameworks/HeliostatCore.xcframework"
xcodebuild -create-xcframework \
  -library "${ROOT}/rust/target/aarch64-apple-ios/release/lib_heliostat_rust.a" \
  -headers "${ROOT}/rust/heliostat-core/include" \
  -output "${ROOT}/ios/Frameworks/HeliostatCore.xcframework"

XCODEGEN_BIN="${XCODEGEN_BIN:-$(command -v xcodegen || true)}"
if [[ -z "${XCODEGEN_BIN}" && -x /opt/homebrew/opt/xcodegen/bin/xcodegen ]]; then
  XCODEGEN_BIN=/opt/homebrew/opt/xcodegen/bin/xcodegen
fi
if [[ -n "${XCODEGEN_BIN}" ]]; then
  (cd "${ROOT}/ios" && "${XCODEGEN_BIN}" generate)
else
  echo "XcodeGen not found; building the checked-in Xcode project." >&2
fi
xcodebuild \
  -project "${ROOT}/ios/HeliostatViewerIOS.xcodeproj" \
  -scheme HeliostatViewer -configuration Debug \
  -destination "id=${DEVICE_ID}" \
  -allowProvisioningUpdates -allowProvisioningDeviceRegistration \
  DEVELOPMENT_TEAM="${TEAM_ID}" CODE_SIGN_STYLE=Automatic \
  PRODUCT_BUNDLE_IDENTIFIER="${BUNDLE_ID}" \
  CONFIGURATION_BUILD_DIR="${ROOT}/build/ios-device" build

if ! output="$(xcrun devicectl device install app --device "${DEVICE_ID}" "${APP_DIR}" 2>&1)"; then
  print -r -- "${output}" >&2
  if [[ "${output}" == *"maximum number of installed apps using a free developer profile"* ]]; then
    echo "The iPhone free developer profile already has three apps. Remove one in iPhone Settings before retrying." >&2
  fi
  exit 3
fi
print -r -- "${output}"
xcrun devicectl device process launch --device "${DEVICE_ID}" \
  --terminate-existing "${BUNDLE_ID}"
