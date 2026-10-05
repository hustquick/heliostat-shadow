"""Signed release discovery, verified staging and detached desktop installers."""
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from urllib.request import getproxies

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

REPO = 'https://github.com/hustquick/heliostat-shadow'
LATEST = 'https://api.github.com/repos/hustquick/heliostat-shadow/releases/latest'


def version_tuple(value):
    if not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('无效版本号')
    return tuple(map(int, value.split('.')))


def verify_manifest(envelope, public_key):
    raw = base64.b64decode(envelope['payload'], validate=True)
    signature = base64.b64decode(envelope['signature'], validate=True)
    serialization.load_pem_public_key(public_key).verify(signature, raw, padding.PKCS1v15(), hashes.SHA256())
    manifest = json.loads(raw)
    version_tuple(manifest['version'])
    if manifest['schema'] != 1 or int(manifest['build']) <= 0:
        raise ValueError('无效更新清单')
    for entry in manifest['platforms'].values():
        if not entry['url'].startswith(f"{REPO}/releases/download/v{manifest['version']}/"):
            raise ValueError('更新来源不受信任')
        if not re.fullmatch('[0-9a-f]{64}', entry['sha256']) or not 0 < entry['size'] < 2_000_000_000:
            raise ValueError('无效安装包校验信息')
    return manifest


def extract_archive(archive, destination):
    """Do not follow symlinks or permit archive members outside the staging dir."""
    with zipfile.ZipFile(archive) as bundle:
        if sum(i.file_size for i in bundle.infolist()) > 4_000_000_000:
            raise ValueError('安装包解压大小超限')
        links = []
        for item in bundle.infolist():
            path = Path(item.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in item.filename:
                raise ValueError('安装包包含不安全路径')
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                target = bundle.read(item).decode('utf-8')
                resolved = (destination / item.filename).parent / target
                if Path(target).is_absolute() or not resolved.resolve().is_relative_to(destination.resolve()):
                    raise ValueError('安装包包含不安全符号链接')
                links.append((item.filename, target))
        link_names = {name for name, _ in links}
        for item in bundle.infolist():
            if any(parent.as_posix() in link_names for parent in Path(item.filename).parents):
                raise ValueError('安装包试图穿过符号链接写入')
            if item.filename not in link_names:
                bundle.extract(item, destination)
        for item in bundle.infolist():
            mode = (item.external_attr >> 16) & 0o777
            if mode and not item.is_dir() and item.filename not in link_names:
                (destination / item.filename).chmod(mode)
        for name, target in links:
            link = destination / name
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target)
        if any(not (destination / name).resolve().is_relative_to(destination.resolve()) for name, _ in links):
            raise ValueError('安装包符号链接链越界')


class UpdateManager:
    def __init__(self, root):
        self.root = Path(root)
        self.identity = json.loads((self.root / 'viewer/version.json').read_text())
        self.target = Path(os.environ['HELIOSTAT_APP_PATH']) if os.environ.get('HELIOSTAT_APP_PATH') else None
        self.platform = 'macos-arm64' if sys.platform == 'darwin' and platform.machine() == 'arm64' else 'windows-x64' if sys.platform == 'win32' else 'unsupported'
        self.state = {'state': 'idle', 'current': self.identity['version'], 'build': self.identity['build'], 'platform': self.platform, 'supported': bool(self.target) and self.platform != 'unsupported'}
        self.lock = threading.RLock()
        self.manifest = None
        self.stage = None
        self.http = requests.Session()
        self.http.proxies.update(getproxies())

    def status(self):
        with self.lock:
            return dict(self.state)

    def start(self, action):
        with self.lock:
            if self.state['state'] in ('checking', 'downloading', 'installing'):
                return self.status()
            if action == 'check':
                self.state.update(state='checking', error=None)
                threading.Thread(target=self._check, daemon=True).start()
            elif action == 'download':
                if not self.state['supported'] or not self.manifest or self.state['state'] not in ('available', 'error'):
                    raise ValueError('请先检查可用更新')
                self.state.update(state='downloading', error=None, progress=0)
                threading.Thread(target=self._download, daemon=True).start()
            elif action == 'install':
                if self.state['state'] != 'ready' or self.stage is None:
                    raise ValueError('请先下载并校验新版')
                self._install()
                self.state.update(state='installing')
            else:
                raise ValueError('无效更新操作')
            return self.status()

    def _error(self, error):
        with self.lock:
            self.state.update(state='error', error=str(error))

    def _check(self):
        try:
            with self.lock:
                self.manifest = None
                self.state.pop("latest", None)
            response = self.http.get(LATEST, timeout=(10, 30), headers={'User-Agent': 'Heliostat-Updater'})
            response.raise_for_status()
            release = response.json()
            entry = next((a for a in release['assets'] if a['name'] == 'updates.json'), None)
            if entry is None:
                with self.lock:
                    self.manifest = None
                    self.state.update(state='current', latest=release['tag_name'].lstrip('v'))
                return
            url = entry['browser_download_url']
            if not url.startswith(REPO + '/releases/download/'):
                raise ValueError('更新来源不受信任')
            response = self.http.get(url, timeout=(10, 30)); response.raise_for_status()
            manifest = verify_manifest(response.json(), (self.root / 'viewer/update-public.pem').read_bytes())
            if release['tag_name'] != 'v' + manifest['version']:
                raise ValueError('发布版本与更新清单不一致')
            available = version_tuple(manifest['version']) > version_tuple(self.identity['version']) and manifest['build'] > self.identity['build']
            with self.lock:
                self.manifest = manifest if available else None
                self.state.update(state='available' if available else 'current', latest=manifest['version'], notes=manifest['notes'], date=manifest.get('date', release.get('published_at', '')[:10]))
        except Exception as error:
            self._error(error)

    def _download(self):
        stage = Path(tempfile.mkdtemp(prefix='heliostat-update-'))
        try:
            entry = self.manifest['platforms'][self.platform]
            digest = hashlib.sha256(); count = 0
            archive = stage / 'package.zip'
            with self.http.get(entry['url'], stream=True, timeout=(10, 60)) as response:
                response.raise_for_status()
                with archive.open('wb') as output:
                    for chunk in response.iter_content(1024 * 1024):
                        count += len(chunk)
                        if count > entry['size']: raise ValueError('安装包大小超限')
                        output.write(chunk); digest.update(chunk)
                        with self.lock: self.state['progress'] = round(count / entry['size'] * 100)
            if count != entry['size'] or digest.hexdigest() != entry['sha256']:
                raise ValueError('安装包校验失败，请重新下载')
            extract_archive(archive, stage / 'unpacked')
            if self.platform == 'macos-arm64':
                app = stage / 'unpacked/塔式镜场设计与优化.app'
                subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True)
                identity = app / 'Contents/Resources/server/_internal/viewer/version.json'
            else:
                identity = stage / 'unpacked/server/_internal/viewer/version.json'
                if not (stage / 'unpacked/HeliostatViewer.exe').is_file(): raise ValueError('安装包缺少启动程序')
            if json.loads(identity.read_text()) != {'version': self.manifest['version'], 'build': self.manifest['build']}:
                raise ValueError('安装包版本不一致')
            with self.lock:
                self.stage = stage; self.state.update(state='ready', progress=100)
        except Exception as error:
            shutil.rmtree(stage, ignore_errors=True); self._error(error)

    def _install(self):
        # The helper lives outside the app being replaced and waits for its parent.
        if not self.target.is_dir() or not os.access(self.target.parent, os.W_OK):
            raise ValueError('应用目录不可写，请将应用安装到有写入权限的目录后更新')
        pid = int(os.environ['HELIOSTAT_APP_PID'])
        args = [str(self.target), str(self.stage / 'unpacked'), str(pid)]
        if self.platform == 'macos-arm64':
            helper = self.stage / 'install.sh'
            shutil.copyfile(self.root / 'viewer/install-macos.sh', helper)
            subprocess.Popen(['/bin/bash', str(helper), *args], start_new_session=True,
                             stdout=(self.stage / 'install.log').open('w'), stderr=subprocess.STDOUT)
        else:
            helper = self.stage / 'install.ps1'
            shutil.copyfile(self.root / 'viewer/install-windows.ps1', helper)
            subprocess.Popen(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(helper), *args],
                             creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                             stdout=(self.stage / 'install.log').open('w'), stderr=subprocess.STDOUT)
