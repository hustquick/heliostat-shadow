#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-$(cat "${ROOT}/VERSION")}" 
OUTPUT="${ROOT}/dist/android"
export ANDROID_HOME="${ANDROID_HOME:-${HOME}/Library/Android/sdk}"
export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-${ANDROID_HOME}}"

PYTHON_BIN="${PYTHON:-${HOME}/venv/bin/python}"
if [[ ! -x "${PYTHON_BIN}" ]]; then PYTHON_BIN="python3"; fi
"${PYTHON_BIN}" "${ROOT}/scripts/version_info.py"
"${PYTHON_BIN}" "${ROOT}/scripts/build_mobile_bundle.py"
rm -rf "${ROOT}/android/app/src/main/assets/viewer" "${ROOT}/android/app/src/main/assets/mobile"
mkdir -p "${ROOT}/android/app/src/main/assets"
cp -R "${ROOT}/viewer" "${ROOT}/android/app/src/main/assets/viewer"
mkdir -p "${ROOT}/android/app/src/main/assets/mobile"
cp "${ROOT}/build/mobile/plant_bundle.json.gz" "${ROOT}/android/app/src/main/assets/mobile/plant_bundle.bundle"

if ! command -v cargo-ndk >/dev/null 2>&1; then
  echo "cargo-ndk is required to build the Android Rust library." >&2
  exit 1
fi
rustup target add aarch64-linux-android armv7-linux-androideabi x86_64-linux-android
(
  cd "${ROOT}/rust"
  cargo ndk -t arm64-v8a -t armeabi-v7a -t x86_64 \
    -o "${ROOT}/android/app/src/main/jniLibs" \
    build --release -p heliostat-core --features android-jni
)
"${ROOT}/android/gradlew" -p "${ROOT}/android" :app:assembleDebug
mkdir -p "${OUTPUT}"
APK="${OUTPUT}/Heliostat-Viewer-Android-v${VERSION}.apk"
cp "${ROOT}/android/app/build/outputs/apk/debug/app-debug.apk" "${APK}"
APKSIGNER="$("${PYTHON_BIN}" -c 'from pathlib import Path; import os; print(sorted(Path(os.environ["ANDROID_HOME"]).glob("build-tools/*/apksigner"))[-1])')"
CERT_SHA="$("${APKSIGNER}" verify --print-certs "${APK}" | sed -n 's/.*certificate SHA-256 digest: //p' | head -n 1)"
EXPECTED_CERT="$(cat "${ROOT}/android/SIGNING_CERT_SHA256")"
if [[ "${CERT_SHA}" != "${EXPECTED_CERT}" ]]; then
  echo "Android signing certificate mismatch: actual=${CERT_SHA}, expected=${EXPECTED_CERT}; refusing to publish this APK." >&2
  exit 1
fi
(
  cd "${OUTPUT}"
  APK_NAME="$(basename "${APK}")"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "${APK_NAME}" > "${APK_NAME}.sha256"
  else
    sha256sum "${APK_NAME}" > "${APK_NAME}.sha256"
  fi
)
echo "APK=${APK}"
