"""Publish a complete immutable-by-workflow release and RSA-signed update feed."""
import base64
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from desktop.updates import REPO, verify_manifest, version_tuple

ROOT = Path(__file__).resolve().parents[1]


def make_manifest(assets, key):
    version = (ROOT / 'VERSION').read_text().strip()
    build = int((ROOT / 'BUILD_NUMBER').read_text())
    names = {
        'macos-arm64': f'Heliostat-Viewer-macOS-arm64-v{version}-update.zip',
        'windows-x64': f'Heliostat-Viewer-Windows-x64-v{version}-portable.zip',
        'android': f'Heliostat-Viewer-Android-v{version}.apk',
        'ios': f'Heliostat-Viewer-iOS-Simulator-v{version}.zip',
    }
    files = {path.name: path for path in Path(assets).rglob('*') if path.is_file()}
    # These are required even though the updater consumes the ZIP packages.
    for required in [f'Heliostat-Viewer-Windows-x64-v{version}-Setup.exe', f'Heliostat-Viewer-macOS-arm64-v{version}.dmg']:
        if required not in files: raise ValueError(f'缺少安装包：{required}')
    entries = {}
    for target, name in names.items():
        path = files[name]
        entries[target] = {'url': f'{REPO}/releases/download/v{version}/{name}', 'size': path.stat().st_size,
                           'sha256': hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()}
    entries['macos-arm64'].update(channel='desktop', system='macos', arch='arm64', minOS='12.0')
    entries['windows-x64'].update(channel='desktop', system='windows', arch='x64', minOS='10.0')
    raw = json.dumps({'schema': 1, 'version': version, 'build': build,
                      'notes': (ROOT / 'RELEASE_NOTES.md').read_text(),
                      'date': datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat(), 'platforms': entries},
                     ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    private = serialization.load_pem_private_key(key, password=None)
    envelope = {'payload': base64.b64encode(raw).decode(),
                'signature': base64.b64encode(private.sign(raw, padding.PKCS1v15(), hashes.SHA256())).decode()}
    verify_manifest(envelope, (ROOT / 'viewer/update-public.pem').read_bytes())
    return envelope


def gh(*args):
    return subprocess.check_output(['gh', *args], text=True).strip()


def main():
    assets = Path(sys.argv[1])
    version = (ROOT / 'VERSION').read_text().strip()
    tag = 'v' + version
    releases = json.loads(gh('api', 'repos/hustquick/heliostat-shadow/releases?per_page=100'))
    existing = next((r for r in releases if r['tag_name'] == tag), None)
    if existing and not existing['draft']:
        raise ValueError('该版本已发布；必须递增 VERSION 和 BUILD_NUMBER，禁止覆盖历史版本')
    published = [r for r in releases if not r['draft'] and not r['prerelease']]
    if published and version_tuple(version) <= max(version_tuple(r['tag_name'].lstrip('v')) for r in published):
        raise ValueError('新版本号必须高于已发布版本')
    if published:
        latest = max(published, key=lambda r: version_tuple(r['tag_name'].lstrip('v')))
        if any(a['name'] == 'updates.json' for a in latest['assets']):
            previous = verify_manifest(json.loads(gh('release', 'download', latest['tag_name'], '-p', 'updates.json', '-O', '-')),
                                       (ROOT / 'viewer/update-public.pem').read_bytes())
            if int((ROOT / 'BUILD_NUMBER').read_text()) <= previous['build']:
                raise ValueError('新构建号必须高于已发布构建号')
    envelope = make_manifest(assets, os.environ['UPDATE_SIGNING_KEY'].encode())
    manifest = assets / 'updates.json'
    manifest.write_text(json.dumps(envelope) + '\n')
    if not existing:
        gh('release', 'create', tag, '--draft', '--target', os.environ.get('GITHUB_SHA', 'main'),
           '--title', f'塔式镜场设计与优化 {tag}', '--notes-file', str(ROOT / 'RELEASE_NOTES.md'))
    # Only drafts can be retried; a published release is never overwritten.
    packages = [str(p) for p in assets.rglob('*') if p.is_file()]
    gh('release', 'upload', tag, *packages, '--clobber')
    gh('release', 'edit', tag, '--draft=false', '--latest')
    print(f'Published complete signed release {tag}')


if __name__ == '__main__':
    main()
