"""UTF-8 ZIP with executable modes and internal symlinks preserved."""
import os
from pathlib import Path
import sys
import zipfile


def package(app, output):
    app = Path(app)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for directory, dirs, files in os.walk(app, followlinks=False):
            for name in list(dirs) + files:
                path = Path(directory) / name
                relative = path.relative_to(app.parent).as_posix()
                if path.is_symlink():
                    info = zipfile.ZipInfo(relative)
                    info.create_system = 3
                    info.external_attr = 0o120777 << 16
                    bundle.writestr(info, os.readlink(path).encode())
                    if name in dirs: dirs.remove(name)
                else:
                    bundle.write(path, relative)


if __name__ == '__main__':
    package(*sys.argv[1:])
