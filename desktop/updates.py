"""Signed release discovery, verified staging and detached desktop installers."""
import base64
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from desktop.update_storage import directory, write, owned_stage, TaskLease
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
            path = PurePosixPath(item.filename)
            if ':' in item.filename or path.is_absolute() or '..' in path.parts or '\\' in item.filename:
                raise ValueError('安装包包含不安全路径')
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                target = bundle.read(item).decode('utf-8')
                resolved = (destination / item.filename).parent / target
                if PurePosixPath(target).is_absolute() or ':' in target or '\\' in target or not resolved.resolve().is_relative_to(destination.resolve()):
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


def desktop_platform():
    machine = platform.machine().lower()
    return ('macos-arm64' if sys.platform == 'darwin' and machine == 'arm64'
            else 'windows-x64' if sys.platform == 'win32' and machine in ('amd64', 'x86_64') else 'unsupported')


class UpdateManager:
    ACTIVE = ('checking', 'downloading', 'verifying', 'installing', 'waiting-start')

    def __init__(self, root):
        self.root = Path(root)
        self.identity = json.loads((self.root / 'viewer/version.json').read_text())
        self.target = Path(os.environ['HELIOSTAT_APP_PATH']).resolve() if os.environ.get('HELIOSTAT_APP_PATH') else None
        self.platform = desktop_platform()
        self.state = {'state': 'idle', 'current': self.identity['version'], 'build': self.identity['build'],
                      'platform': self.platform, 'supported': bool(self.target) and self.platform != 'unsupported'}
        self.lock = threading.RLock()
        self.actor = uuid.uuid4().hex
        self.cancelled = threading.Event()
        self.worker = None
        self.downloading = False
        self.manifest = self.envelope = self.stage = None
        self.session_file = directory(self.target) / 'session.json' if self.target else None
        self.task_lease = None
        if self.session_file and self.session_file.exists():
            startup_lease = TaskLease(self.session_file.parent / 'task.lock')
            exclusive = startup_lease.acquire()
            try:
                saved = json.loads(self.session_file.read_text())
                self.envelope = saved.get('envelope')
                self.manifest = verify_manifest(self.envelope, self.public_key()) if self.envelope else None
                self.state.update(saved['status'])
                self.state.update(current=self.identity['version'], build=self.identity['build'], platform=self.platform,
                                  supported=self.platform != 'unsupported')
                candidate = Path(saved['stage']) if saved.get('stage') else None
                if candidate and owned_stage(candidate) and candidate.parent.resolve() == self.session_file.parent.resolve():
                    self.stage = candidate
                if exclusive and self.state['state'] in ('checking', 'downloading', 'verifying'):
                    self.state.update(state='error', error='上次更新已中断，请重试')
                elif exclusive and self.state['state'] == 'ready':
                    self._verify_stage()
                elif self.state['state'] in ('installing', 'waiting-start'):
                    self.state.update(state='waiting-start')
            except Exception as error:
                self.state.update(state='error', error=str(error))
            if exclusive:
                try: self._persist()
                finally: startup_lease.release()
        self.http = requests.Session()
        self.http.proxies.update(getproxies())

    def public_key(self):
        return (self.root / 'viewer/update-public.pem').read_bytes()

    def _transaction(self):
        if not self.target: return None
        if self.platform == 'macos-arm64': return Path(str(self.target) + '.update-state/state.json')
        if self.platform == 'windows-x64': return directory(self.target) / 'transaction/state.json'
        return None

    def status(self):
        with self.lock:
            if self.session_file and self.session_file.exists() and not self.worker:
                saved = json.loads(self.session_file.read_text())
                if saved.get('owner', self.actor) != self.actor:
                    self.state.update(saved['status'])
                    self.state.update(current=self.identity['version'], build=self.identity['build'], platform=self.platform, supported=self.platform != 'unsupported')
                    self.envelope = saved.get('envelope')
                    self.manifest = verify_manifest(self.envelope, self.public_key()) if self.envelope else None
                    candidate = Path(saved['stage']) if saved.get('stage') else None
                    self.stage = candidate if candidate and owned_stage(candidate) and candidate.parent.resolve() == self.session_file.parent.resolve() else None
            if self.target:
                receipt = directory(self.target) / 'health.json'
                if receipt.exists():
                    data = json.loads(receipt.read_text())
                    if (data['identity'] == self.identity and data['token'] == self.state.get('transactionToken')):
                        self.state.update(state='success', error=data.get('error'))
                        if not self._transaction().exists(): self.stage = None
                transaction = self._transaction()
                if transaction and transaction.exists():
                    data = json.loads(transaction.read_text())
                    phase = data['phase']
                    self.state['recovery'] = str(transaction.parent / ('recover.command' if self.platform == 'macos-arm64' else 'recover.cmd'))
                    if phase in ('aborted', 'rolled-back', 'recovery-required', 'replacing'):
                        self.state.update(state='error', error=data.get('error', '更新中断，请退出应用后运行恢复入口'))
                    elif phase in ('awaiting-health', 'prepared'):
                        self.state.update(state='installing' if phase == 'prepared' else 'waiting-start')
                    elif phase in ('healthy', 'cleanup-pending', 'complete'):
                        self.state.update(state='success', error=data.get('error'))
                        self.state['cleanupPending'] = True
                elif self.state['state'] in ('installing', 'waiting-start'):
                    self.state.update(state='error', error='更新事务已中断或已回退，当前应用可继续使用，请重新检查更新')
                elif self.state['state'] == 'success':
                    self.state['cleanupPending'] = False
            return dict(self.state)

    def _persist(self):
        if self.session_file:
            write(self.session_file, {'owner': self.actor, 'ownerPid': os.getpid(), 'status': self.state, 'envelope': self.envelope,
                                      'stage': str(self.stage) if self.stage else None})

    def _verify_stage(self):
        if not self.stage or not self.manifest: raise ValueError('更新暂存文件不存在，请重新下载')
        if self.envelope: self.manifest = verify_manifest(self.envelope, self.public_key())
        entry = self.manifest['platforms'][self.platform]
        self._compatible(entry)
        archive = self.stage / 'package.zip'
        with archive.open('rb') as stream: digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if archive.stat().st_size != entry['size'] or digest != entry['sha256']:
            raise ValueError('暂存包校验失败，请重新下载')
        # Re-extract the authenticated archive instead of trusting an old unpacked tree.
        unpacked = self.stage / 'unpacked'
        if unpacked.exists(): shutil.rmtree(unpacked)
        extract_archive(archive, unpacked)
        if self.platform == 'macos-arm64':
            app = unpacked / '塔式镜场设计与优化.app'
            subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True)
            from desktop.macos_update import identity
            if identity(app) != str(self.manifest['build']): raise ValueError('应用构建号不匹配')
            file = app / 'Contents/Resources/server/_internal/viewer/version.json'
        else:
            from desktop.windows_update import files
            files(unpacked)
            file = unpacked / 'server/_internal/viewer/version.json'
        if json.loads(file.read_text()) != {'version': self.manifest['version'], 'build': self.manifest['build']}:
            raise ValueError('安装包版本不一致')

    def _background(self, operation):
        def work():
            try: operation()
            finally:
                with self.lock:
                    self.worker = None
                    try: self._persist()
                    finally:
                        if self.task_lease: self.task_lease.release(); self.task_lease = None
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def preferences(self):
        if not self.target: return {}
        path = directory(self.target).parent.parent / 'desktop-preferences.json'
        return json.loads(path.read_text()) if path.exists() else {}

    def _save_preferences(self, values):
        if not isinstance(values, dict) or len(values) > 1000: raise ValueError('用户设置格式无效')
        for key, value in values.items():
            if not re.fullmatch(r'heliostat-operation-[A-Za-z0-9_-]{1,64}', key) or not isinstance(value, str) or len(value) > 4096:
                raise ValueError('用户设置格式无效')
            params = json.loads(value)
            numbers = [params[name] for name in ('startDni', 'startElevation', 'stopDni', 'stopElevation')]
            if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in numbers):
                raise ValueError('用户启停参数无效')
            start_dni, start_elevation, stop_dni, stop_elevation = numbers
            if not (start_dni >= stop_dni >= 0 and 90 >= start_elevation >= stop_elevation >= 0):
                raise ValueError('用户启停参数无效')
        saved = self.preferences(); saved.update(values)
        # User preferences live outside Updates and are never update cleanup targets.
        write(directory(self.target).parent.parent / 'desktop-preferences.json', saved)

    def _start(self, action, payload=None):
        with self.lock:
            if action == 'cancel':
                if self.state['state'] in ('installing', 'waiting-start'): raise ValueError('安装已开始，请使用恢复入口')
                self.cancelled.set()
                if not self.worker and not self.downloading and self.stage:
                    self._discard_stage()
                self.state.update(state='cancelled', error=None); self._persist()
                return self.status()
            if self.worker or self.state['state'] in self.ACTIVE:
                return self.status()
            transaction = self._transaction()
            if transaction and transaction.exists():
                raise ValueError('上次更新尚未完成，请先重启应用或使用恢复入口')
            self.cancelled.clear()
            if action == 'check':
                self.state.pop('transactionToken', None)
                self.state.pop('recovery', None)
                self.state.pop('message', None)
                self.state.update(state='checking', error=None)
                self._background(self._check)
            elif action == 'download':
                if not self.state['supported'] or not self.manifest or self.state['state'] not in ('available', 'error'):
                    raise ValueError('请先检查可用更新')
                self.state.update(state='downloading', error=None, progress=0)
                self._background(self._download)
            elif action == 'install':
                if self.state['state'] != 'ready' or self.stage is None: raise ValueError('请先下载并校验新版')
                if payload and 'preferences' in payload: self._save_preferences(payload['preferences'])
                self.state.update(state='verifying', error=None)
                self._background(self._install_verified)
            else: raise ValueError('无效更新操作')
            self._persist()
            return self.status()

    def start(self, action, payload=None):
        with self.lock:
            if self.worker:
                return self._start(action, payload)
            self.status()
            lease = TaskLease(self.session_file.parent / 'task.lock') if self.session_file else None
            if lease and not lease.acquire(): raise ValueError('另一个应用窗口正在更新，请在原窗口继续')
            self.task_lease = lease
            try: return self._start(action, payload)
            finally:
                if not self.worker and self.task_lease:
                    self.task_lease.release(); self.task_lease = None

    def _error(self, error):
        with self.lock:
            self.state.update(state='cancelled' if self.cancelled.is_set() else 'error', error=str(error))
            self._persist()

    def _check(self):
        try:
            with self.lock:
                self._discard_stage()
                self.manifest = self.envelope = None
                self.state.pop('latest', None)
            response = self.http.get(LATEST, timeout=(10, 30), headers={'User-Agent': 'Heliostat-Updater'})
            response.raise_for_status(); release = response.json()
            if release.get('draft') or release.get('prerelease'): raise ValueError('更新来源不是正式发布')
            entry = next((a for a in release['assets'] if a['name'] == 'updates.json'), None)
            if entry is None:
                if not self.cancelled.is_set(): self.state.update(state='current', message='当前发布没有可认证的更新包')
                return
            url = entry['browser_download_url']
            if not url.startswith(REPO + '/releases/download/'): raise ValueError('更新来源不受信任')
            response = self.http.get(url, timeout=(10, 30)); response.raise_for_status()
            envelope = response.json(); manifest = verify_manifest(envelope, self.public_key())
            if release['tag_name'] != 'v' + manifest['version']: raise ValueError('发布版本与更新清单不一致')
            available = version_tuple(manifest['version']) > version_tuple(self.identity['version']) and manifest['build'] > self.identity['build']
            if available and self.state['supported'] and self.platform not in manifest['platforms']:
                raise ValueError('本发布没有兼容的安装包')
            if available and self.platform in manifest['platforms']:
                entry = manifest['platforms'][self.platform]
                self._compatible(entry)
            if self.cancelled.is_set(): return
            with self.lock:
                self.envelope = envelope
                self.manifest = manifest if available else None
                self.state.update(state='available' if available else 'current', latest=manifest['version'],
                                  notes=manifest['notes'], date=manifest.get('date', release.get('published_at', '')[:10]))
        except Exception as error: self._error(error)

    def _compatible(self, entry):
        system, arch = ('macos', 'arm64') if self.platform == 'macos-arm64' else ('windows', 'x64')
        if (entry.get('channel', 'desktop') != 'desktop' or entry.get('system', system) != system
                or entry.get('arch', arch) != arch): raise ValueError('更新渠道、系统或架构不兼容')
        minimum = entry.get('minOS')
        current = platform.mac_ver()[0] if system == 'macos' else platform.win32_ver()[1]
        if minimum:
            numbers = lambda value: tuple(int(part) for part in value.split('.'))
            if not current or numbers(current) < numbers(minimum): raise ValueError('当前系统版本不支持本次更新')

    def _discard_stage(self):
        if self.stage:
            if not owned_stage(self.stage): raise ValueError('暂存目录归属不明，保留文件')
            shutil.rmtree(self.stage)
            self.stage = None

    def _download(self):
        self.downloading = True
        try:
            with self.lock:
                self._discard_stage()
                self.stage = Path(tempfile.mkdtemp(prefix='heliostat-update-', dir=self.session_file.parent if self.session_file else None))
                write(self.stage / 'owner.json', {'app': 'heliostat-shadow'})
                self._persist()
            entry = self.manifest['platforms'][self.platform]
            digest = hashlib.sha256(); count = 0
            with self.http.get(entry['url'], stream=True, timeout=(10, 60)) as response:
                response.raise_for_status()
                with (self.stage / 'package.zip').open('wb') as output:
                    for chunk in response.iter_content(1024 * 1024):
                        if self.cancelled.is_set(): raise ValueError('更新已取消')
                        count += len(chunk)
                        if count > entry['size']: raise ValueError('安装包大小超限')
                        output.write(chunk); digest.update(chunk)
                        with self.lock: self.state['progress'] = round(count / entry['size'] * 100)
                    output.flush(); os.fsync(output.fileno())
            with self.lock: self.state.update(state='verifying'); self._persist()
            if count != entry['size'] or digest.hexdigest() != entry['sha256']: raise ValueError('安装包校验失败，请重新下载')
            self._verify_stage()
            with self.lock:
                if self.cancelled.is_set(): raise ValueError('更新已取消')
                self.state.update(state='ready', progress=100); self._persist()
        except Exception as error:
            try: self._discard_stage()
            except Exception as cleanup: error = ValueError(f'{error}；清理待重试：{cleanup}')
            self._error(error)
        finally:
            self.downloading = False

    def _install_verified(self):
        try:
            self._verify_stage()
            with self.lock:
                if self.cancelled.is_set(): raise ValueError('更新已取消')
                self._install()
                self.state.update(state='installing'); self._persist()
        except Exception as error: self._error(error)

    def _install(self):
        if not self.target.is_dir() or not os.access(self.target, os.W_OK) or not os.access(self.target.parent, os.W_OK):
            raise ValueError('应用目录不可写，请安装到有写入权限的目录后更新')
        write(self.stage / 'authorization.json', self.envelope)
        pid = int(os.environ['HELIOSTAT_APP_PID'])
        runtime = self.stage / 'helper-runtime'
        if runtime.exists(): shutil.rmtree(runtime)
        if self.platform == 'macos-arm64':
            from desktop.macos_update import prepare
            import shlex
            shutil.copytree(self.target / 'Contents/Resources/server', runtime)
            executable = runtime / 'heliostat-viewer-server'
            transaction = prepare(self.target, self.stage, self.manifest['build'], pid)
            recovery = transaction.parent / 'recover.command'
            recovery.write_text('#!/bin/bash\nset -e\n' + shlex.join([str(executable), '--macos-recover', str(transaction)]) + '\n')
            recovery.chmod(0o700)
            command = [str(executable), '--macos-update', str(transaction)]
            options = {'start_new_session': True}
        else:
            from desktop.windows_update import prepare
            shutil.copytree(self.target / 'server', runtime)
            transaction = prepare(self.target, self.stage, {'version': self.manifest['version'], 'build': self.manifest['build']}, pid)
            command = [str(runtime / 'heliostat-viewer-server.exe'), '--windows-update', str(transaction)]
            options = {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
        self.state['transactionToken'] = json.loads(transaction.read_text())['token']
        self._persist()
        try:
            with (directory(self.target) / 'install.log').open('a') as log:
                subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, **options)
        except Exception:
            record = json.loads(transaction.read_text()); record['phase'] = 'aborted'; write(transaction, record)
            raise
