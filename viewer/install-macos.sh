#!/bin/bash
set -euo pipefail
target="$1"; staged="$2/塔式镜场设计与优化.app"; parent_pid="$3"
# Never replace a mounted DMG or an application without write access.
test -w "$(dirname "$target")"
for ((i=0; i<120; i++)); do
  if ! kill -0 "$parent_pid" 2>/dev/null; then break; fi
  sleep 1
done
if kill -0 "$parent_pid" 2>/dev/null; then exit 1; fi
backup="${target}.previous"
incoming="${target}.incoming"
rm -rf "$incoming"
ditto "$staged" "$incoming"
codesign --verify --deep --strict "$incoming"
rm -rf "$backup"
mv "$target" "$backup"
if ! mv "$incoming" "$target"; then mv "$backup" "$target"; exit 1; fi
open "$target"
