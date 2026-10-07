#!/bin/bash
# Compatibility entry point. Transaction logic lives in the detached bundled runtime.
set -euo pipefail
exec "$2/../helper-runtime/heliostat-viewer-server" --macos-update "$1.update-state/state.json"
