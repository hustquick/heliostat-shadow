"""PyInstaller entry point shared by the macOS and Windows desktop apps."""

import sys
import os

# Dispatch before importing the HTTP server; the helper survives the old app exit.
if sys.platform == 'darwin' and len(sys.argv) == 3 and sys.argv[1].startswith('--macos-'):
    if sys.argv[1] == '--macos-healthy' and not os.environ.get('HELIOSTAT_CONFIRM_BUILD'):
        raise ValueError('Native launch build confirmation is required')
    from desktop.macos_update import run, recover, healthy
    {'--macos-update': run, '--macos-recover': recover, '--macos-healthy': healthy}[sys.argv[1]](sys.argv[2])
    sys.exit(0)

if sys.platform == 'win32' and len(sys.argv) == 3 and sys.argv[1].startswith('--windows-'):
    from desktop.windows_update import run, recover, healthy
    if sys.argv[1] == '--windows-healthy':
        if not os.environ.get('HELIOSTAT_CONFIRM_IDENTITY'): raise ValueError('Native launch identity confirmation is required')
        from desktop.update_storage import directory
        healthy(directory(sys.argv[2]) / 'transaction/state.json')
    else:
        {'--windows-update': run, '--windows-recover': recover}[sys.argv[1]](sys.argv[2])
    sys.exit(0)

from scripts.serve_viewer import main


if __name__ == "__main__":
    main()
