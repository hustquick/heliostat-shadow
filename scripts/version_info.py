"""One version/build identity for every client."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def write_version():
    version = (ROOT / 'VERSION').read_text().strip()
    build = int((ROOT / 'BUILD_NUMBER').read_text().strip())
    (ROOT / 'viewer/version.json').write_text(json.dumps({'version': version, 'build': build}) + '\n')
    return version, build


if __name__ == '__main__':
    write_version()
