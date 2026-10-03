#!/bin/zsh

set -euo pipefail

ROOT="${0:A:h:h}"
PYTHON_BIN="${PYTHON_BIN:-${HOME}/venv/bin/python}"
VERSION="${1:-$(<"${ROOT}/VERSION")}"
ARCH="$(uname -m)"
BUILD_ROOT="${ROOT}/build/macos"
OUTPUT_ROOT="${ROOT}/dist/macos"
APP="${OUTPUT_ROOT}/塔式镜场设计与优化.app"
CONTENTS="${APP}/Contents"
SERVER_NAME="heliostat-viewer-server"

if [[ "${ARCH}" != "arm64" ]]; then
  echo "This release script currently builds the Apple Silicon package only." >&2
  exit 1
fi
if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python virtual environment not found: ${PYTHON_BIN}" >&2
  exit 1
fi
if ! "${PYTHON_BIN}" -m PyInstaller --version >/dev/null 2>&1; then
  echo "Install requirements-build.txt into ~/venv before building." >&2
  exit 1
fi
if ! command -v cargo >/dev/null 2>&1; then
  echo "Rust toolchain not found. Install rustup before building." >&2
  exit 1
fi

(
  source "${HOME}/venv/bin/activate"
  maturin develop --release --manifest-path "${ROOT}/rust/heliostat-core/Cargo.toml" \
    --features python-bindings
)

"${PYTHON_BIN}" - "${BUILD_ROOT}" "${OUTPUT_ROOT}" <<'PY'
from pathlib import Path
import shutil
import sys
for value in sys.argv[1:]:
    path = Path(value)
    if path.exists():
        shutil.rmtree(path)
PY
mkdir -p "${BUILD_ROOT}" "${OUTPUT_ROOT}" \
  "${CONTENTS}/MacOS" "${CONTENTS}/Resources"

"${PYTHON_BIN}" -m PyInstaller \
  --noconfirm --clean --onedir \
  --name "${SERVER_NAME}" \
  --distpath "${BUILD_ROOT}/pyinstaller-dist" \
  --workpath "${BUILD_ROOT}/pyinstaller-work" \
  --specpath "${BUILD_ROOT}/pyinstaller-spec" \
  --paths "${ROOT}" \
  --collect-data pvlib \
  --hidden-import _heliostat_rust \
  --add-data "${ROOT}/viewer:viewer" \
  --add-data "${ROOT}/data/gemasolar_config.json:data" \
  --add-data "${ROOT}/data/power_tower_catalog.json:data" \
  --add-data "${ROOT}/data/temperature_archive.json:data" \
  --add-data "${ROOT}/data/processed/gemasolar_layout.csv:data/processed" \
  --add-data "${ROOT}/data/processed/gemasolar_dni_2023.csv:data/processed" \
  --add-data "${ROOT}/reports/three_layout_optimization:reports/three_layout_optimization" \
  "${ROOT}/desktop/server_entry.py"

cp -R "${BUILD_ROOT}/pyinstaller-dist/${SERVER_NAME}" "${CONTENTS}/Resources/server"

xcrun swiftc -O -target arm64-apple-macos12.0 \
  -framework AppKit -framework WebKit \
  "${ROOT}/macos/HeliostatViewerApp.swift" \
  -o "${CONTENTS}/MacOS/塔式镜场设计与优化"

ICONSET="${BUILD_ROOT}/ViewerIcon.iconset"
"${PYTHON_BIN}" "${ROOT}/macos/create_icon.py" "${ICONSET}"
iconutil -c icns "${ICONSET}" -o "${CONTENTS}/Resources/ViewerIcon.icns"

cat > "${CONTENTS}/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleDisplayName</key><string>塔式镜场设计与优化</string>
  <key>CFBundleExecutable</key><string>塔式镜场设计与优化</string>
  <key>CFBundleIconFile</key><string>ViewerIcon</string>
  <key>CFBundleIdentifier</key><string>com.hustquick.heliostat-shadow.viewer</string>
  <key>CFBundleName</key><string>塔式镜场设计与优化</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
PLIST

codesign --force --deep --sign - --timestamp=none "${APP}"
codesign --verify --deep --strict --verbose=2 "${APP}"

DMG_STAGE="${BUILD_ROOT}/dmg-stage"
mkdir -p "${DMG_STAGE}"
cp -R "${APP}" "${DMG_STAGE}/"
ln -s /Applications "${DMG_STAGE}/Applications"
DMG="${OUTPUT_ROOT}/Heliostat-Viewer-macOS-arm64-v${VERSION}.dmg"
hdiutil create -volname "塔式镜场设计与优化" -srcfolder "${DMG_STAGE}" \
  -ov -format UDZO "${DMG}"
(
  cd "${OUTPUT_ROOT}"
  shasum -a 256 "${DMG:t}" > "${DMG:t}.sha256"
)

echo "APP=${APP}"
echo "DMG=${DMG}"
