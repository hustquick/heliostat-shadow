"""Private updater storage, separate from application and mirror-field data."""
import hashlib
import json
import os
from pathlib import Path
import sys


def directory(target):
    base = (Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Heliostat Viewer' if sys.platform == 'win32'
            else Path.home() / 'Library/Application Support/Heliostat Viewer')
    if os.environ.get('HELIOSTAT_VIEWER_DATA'): base = Path(os.environ['HELIOSTAT_VIEWER_DATA'])
    identity = str(Path(target).resolve())
    if sys.platform == 'win32': identity = identity.lower()
    key = hashlib.sha256(identity.encode()).hexdigest()[:24]
    folder = base / 'Updates' / key
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def write(path, value):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as stream:
        json.dump(value, stream); stream.flush(); os.fsync(stream.fileno())
    tmp.replace(path)
    if os.name != 'nt':
        descriptor = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)


def owned_stage(stage):
    stage = Path(stage)
    return (not stage.is_symlink() and stage.name.startswith('heliostat-update-')
            and (stage / 'owner.json').is_file()
            and json.loads((stage / 'owner.json').read_text()).get('app') == 'heliostat-shadow')


def confirmed(target, identity, token, error=None):
    write(directory(target) / 'health.json', {'identity': identity, 'token': token, 'error': error})


def authenticate_stage(stage, platform, expected):
    """Recheck signed authorization after the old application exits."""
    from desktop.updates import verify_manifest, extract_archive
    import shutil
    stage = Path(stage)
    authorization = json.loads((stage / 'authorization.json').read_text())
    public = (stage / 'helper-runtime/_internal/viewer/update-public.pem').read_bytes()
    manifest = verify_manifest(authorization, public)
    if {'version': manifest['version'], 'build': manifest['build']} != expected:
        raise ValueError('Transaction version does not match signed manifest')
    entry = manifest['platforms'][platform]
    with (stage / 'package.zip').open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != entry['sha256'] or (stage / 'package.zip').stat().st_size != entry['size']:
        raise ValueError('Authenticated archive changed')
    unpacked = stage / 'unpacked'
    if unpacked.exists(): shutil.rmtree(unpacked)
    extract_archive(stage / 'package.zip', unpacked)


def remove_stage(stage):
    import shutil
    stage = Path(stage)
    if not stage.exists(): return
    if not owned_stage(stage): raise ValueError('Unknown staging ownership')
    marker = (stage / 'owner.json').read_bytes()
    for item in stage.iterdir():
        if item.name == 'owner.json': continue
        if item.is_symlink() or not item.is_dir(): item.unlink()
        else: shutil.rmtree(item)
    (stage / 'owner.json').unlink()
    try: stage.rmdir()
    except Exception:
        (stage / 'owner.json').write_bytes(marker)
        raise


def remove_transaction(path):
    import shutil
    path = Path(path)
    state = path.read_bytes()
    for item in path.parent.iterdir():
        if item == path: continue
        if item.is_dir() and not item.is_symlink(): shutil.rmtree(item)
        else: item.unlink()
    path.unlink()
    try: path.parent.rmdir()
    except Exception:
        path.write_bytes(state)
        raise


def record_error(path, error):
    path = Path(path)
    try:
        state = json.loads(path.read_text())
        state['error'] = str(error)
        write(path, state)
        log = path.parent / 'errors.log'
        with log.open('a') as stream: stream.write(str(error) + '\n')
    except Exception:
        try:
            target = json.loads(path.read_text())['target']
            with (directory(target) / 'errors.log').open('a') as stream:
                stream.write(str(error) + '\n')
        except Exception:
            import logging
            logging.exception('Could not persist updater cleanup error: %s', error)


class TaskLease:
    """One check/download/install request per installation across native windows."""
    def __init__(self, path):
        self.stream = Path(path).open('a+b')
        self.acquired = False

    def acquire(self):
        try:
            if os.name == 'nt':
                import msvcrt
                self.stream.seek(0, os.SEEK_END)
                if self.stream.tell() == 0: self.stream.write(b'0'); self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.acquired = True
            return True
        except (OSError, BlockingIOError):
            self.stream.close()
            return False

    def release(self):
        if self.acquired:
            if os.name == 'nt':
                import msvcrt
                self.stream.seek(0); msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_UN)
            self.acquired = False
        self.stream.close()
