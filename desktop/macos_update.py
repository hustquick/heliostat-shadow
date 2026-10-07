"""macOS-only durable update transaction; runnable from a detached server copy."""
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import time
import uuid
import tempfile
from desktop.update_storage import owned_stage, authenticate_stage, confirmed, write, remove_stage, remove_transaction, directory

BUNDLE = 'com.hustquick.heliostat-shadow.viewer'


def identity(app):
    if app.is_symlink():
        raise ValueError('Refusing symlink application')
    with (app / 'Contents/Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    if info['CFBundleIdentifier'] != BUNDLE:
        raise ValueError('Application identity mismatch')
    return str(info['CFBundleVersion'])


def launch_application(app):
    command = ['/usr/bin/open', '-n']
    if os.environ.get('HELIOSTAT_VIEWER_DATA'):
        command += ['--env', 'HELIOSTAT_VIEWER_DATA=' + os.environ['HELIOSTAT_VIEWER_DATA']]
    subprocess.run([*command, str(app)], check=True)


def save(path, state):
    write(path, state)


def prepare(target, stage, build, pid):
    identity(target)
    if not owned_stage(stage): raise ValueError('Unknown staging ownership')
    folder = Path(str(target) + '.update-state')
    folder.mkdir(mode=0o700)  # Existing transactions must be resolved first.
    state = dict(schema=1, token=uuid.uuid4().hex, target=str(target), stage=str(stage),
                 build=str(build), old_build=identity(target), old_inode=target.stat().st_ino, old_device=target.stat().st_dev,
                 pid=pid, phase='prepared',
                 expected=json.loads((stage / 'unpacked/塔式镜场设计与优化.app/Contents/Resources/server/_internal/viewer/version.json').read_text()))
    path = folder / 'state.json'
    save(path, state)
    return path


def _run(path, launch=None, timeout=120):
    path = Path(path)
    state = json.loads(path.read_text())
    target = Path(state['target']); stage = Path(state['stage'])
    backup = Path(str(target) + '.previous')
    incoming = Path(str(target) + '.incoming-' + state['token'])
    if (state['schema'] != 1 or path != Path(str(target) + '.update-state/state.json')
            or not re.fullmatch('[0-9a-f]{32}', state['token']) or stage.is_symlink()
            or (stage.exists() and not owned_stage(stage))
            or (not stage.exists() and state['phase'] not in ('healthy', 'rolled-back', 'aborted'))):
        raise ValueError('Invalid update transaction')
    launch = launch or launch_application
    def persist(phase):
        state['phase'] = phase; save(path, state)
    def cleanup():
        # Only transaction-owned staging and an identity-checked backup are removed.
        if backup.exists():
            if backup.is_symlink() or backup.stat().st_ino != state['old_inode'] or backup.stat().st_dev != state['old_device']:
                raise ValueError('Backup directory identity changed')
            if not state.get('backupValidated'):
                if identity(backup) != state['old_build']: raise ValueError('Backup identity changed')
                state['backupValidated'] = True; save(path, state)
            shutil.rmtree(backup)
        if incoming.exists(): shutil.rmtree(incoming)
        if stage.exists(): remove_stage(stage)
        remove_transaction(path)
    old_exited = False
    try:
        if state['phase'] == 'prepared':
            deadline = time.monotonic() + timeout
            while True:
                try: os.kill(state['pid'], 0)
                except ProcessLookupError: break
                if time.monotonic() >= deadline: raise TimeoutError('Application did not exit')
                time.sleep(.2)
            old_exited = True
            authenticate_stage(stage, 'macos-arm64', state['expected'])
            staged = stage / 'unpacked/塔式镜场设计与优化.app'
            if identity(staged) != state['build']: raise ValueError('Staged version mismatch')
            subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(staged)], check=True)
            if backup.exists(): raise ValueError('Existing backup requires healthy-start cleanup first')
            subprocess.run(['/usr/bin/ditto', str(staged), str(incoming)], check=True)
            subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(incoming)], check=True)
            persist('replacing')
            target.rename(backup)
            incoming.rename(target)
            persist('awaiting-health')
            launch(target)
            deadline = time.monotonic() + timeout
            while not (path.parent / 'healthy').exists():
                if time.monotonic() >= deadline:
                    persist('recovery-required')
                    raise TimeoutError('New application did not confirm initialization; run recover.command after quitting it')
                time.sleep(.2)
        if state['phase'] == 'replacing':
            _recover(path)
            return
        if state['phase'] in ('awaiting-health', 'recovery-required', 'healthy'):
            marker = path.parent / 'healthy'
            if not marker.exists() or marker.read_text() != state['token']: return
            if identity(target) != state['build']: raise ValueError('Installed version changed')
            confirmed(target, state['expected'], state['token'])
            persist('healthy')
            cleanup()
    except Exception as error:
        if state['phase'] == 'prepared':
            persist('aborted')
            if old_exited:
                try: launch(target)
                except Exception as recovery: state['recovery_error'] = str(recovery)
        if state['phase'] == 'awaiting-health': persist('recovery-required')
        if state['phase'] == 'replacing':
            if backup.exists() and not target.exists(): backup.rename(target)
            if target.exists() and not backup.exists():
                persist('rolled-back')
                try: launch(target)
                except Exception as recovery: state['recovery_error'] = str(recovery)
            else: persist('recovery-required')
        if path.exists():
            state['error'] = str(error); save(path, state)
            with (path.parent / 'errors.log').open('a') as log: log.write(str(error) + '\n')
        raise


def _recover(path):
    path = Path(path); state = json.loads(path.read_text()); target = Path(state['target'])
    if path != Path(str(target) + '.update-state/state.json') or not re.fullmatch('[0-9a-f]{32}', state['token']): raise ValueError('Invalid state path or token')
    processes = subprocess.run(['/usr/bin/pgrep', '-f', '^' + re.escape(str(target / 'Contents/MacOS')) + '/'], capture_output=True)
    if getattr(processes, 'returncode', 1) == 0:
        raise RuntimeError('Quit the application before running recover.command')
    backup = Path(str(target) + '.previous')
    if state['phase'] not in ('awaiting-health', 'recovery-required', 'replacing'): raise ValueError('Recovery not required')
    if identity(backup) != state['old_build']: raise ValueError('Backup identity mismatch')
    failed = Path(str(target) + '.failed-' + state['token'])
    if target.exists(): target.rename(failed)
    try: backup.rename(target)
    except Exception:
        if failed.exists(): failed.rename(target)
        raise
    state['phase'] = 'rolled-back'; save(path, state)
    launch_application(target)


def _healthy(path):
    """Called only after the native window, server and viewer initialized."""
    path = Path(path)
    if not path.exists():
        target = Path(str(path.parent).removesuffix('.update-state'))
        backup = Path(str(target) + '.previous')
        if os.environ.get('HELIOSTAT_CONFIRM_BUILD', identity(target)) != identity(target): return
        if backup.exists():
            try:
                old = identity(backup); current = identity(target)
                metadata = json.loads((backup / 'Contents/Resources/server/_internal/viewer/version.json').read_text())
                if str(metadata['build']) != old or int(old) >= int(current): return
                subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(backup)], check=True)
                # Persist ownership before deletion so partial cleanup can retry
                # even if Info.plist has already been removed.
                stage = Path(tempfile.mkdtemp(prefix='heliostat-update-', dir=directory(target)))
                write(stage / 'owner.json', {'app': 'heliostat-shadow'})
                expected = json.loads((target / 'Contents/Resources/server/_internal/viewer/version.json').read_text())
                path.parent.mkdir(mode=0o700)
                state = dict(schema=1, token=uuid.uuid4().hex, target=str(target), stage=str(stage),
                             build=current, old_build=old, old_inode=backup.stat().st_ino, old_device=backup.stat().st_dev,
                             expected=expected, phase='healthy', pid=-1)
                save(path, state); (path.parent / 'healthy').write_text(state['token'])
                run(path)
            except Exception as error:
                log = Path.home() / 'Library/Logs/heliostat-update.log'
                log.parent.mkdir(parents=True, exist_ok=True)
                with log.open('a') as stream: stream.write(f'Legacy backup cleanup: {error}\n')
        return
    state = json.loads(path.read_text())
    target = Path(state['target'])
    if path != Path(str(target) + '.update-state/state.json') or not re.fullmatch('[0-9a-f]{32}', state['token']): raise ValueError('Invalid state path or token')
    runtime = os.environ.get('HELIOSTAT_CONFIRM_BUILD')
    expected_runtime = state['old_build'] if state['phase'] in ('prepared', 'rolled-back', 'aborted') else state['build']
    if runtime is not None and runtime != expected_runtime: return
    # Migrate the previous repair's transaction format only after this native
    # version has initialized and both application identities are confirmed.
    if state['phase'] in ('awaiting-health', 'recovery-required', 'healthy') and identity(target) == state['build']:
        backup = Path(str(target) + '.previous')
        if 'expected' not in state:
            state['expected'] = json.loads((target / 'Contents/Resources/server/_internal/viewer/version.json').read_text())
            if str(state['expected']['build']) != state['build']: raise ValueError('Version mismatch')
        if 'old_inode' not in state and backup.exists():
            if identity(backup) != state['old_build']: raise ValueError('Legacy transaction backup mismatch')
            state.update(old_inode=backup.stat().st_ino, old_device=backup.stat().st_dev)
        stage = Path(state['stage'])
        if stage.exists() and not owned_stage(stage):
            if (stage.is_symlink() or stage.parent.resolve() != Path(tempfile.gettempdir()).resolve()
                    or not stage.name.startswith('heliostat-update-')
                    or not ((stage / 'install.sh').is_file() or (stage / 'helper-runtime/heliostat-viewer-server').is_file())):
                raise ValueError('Legacy staging ownership is not confirmed')
            write(stage / 'owner.json', {'app': 'heliostat-shadow'})
        save(path, state)
    if state['phase'] == 'prepared':
        try: os.kill(state['pid'], 0)
        except ProcessLookupError: pass
        else: return
        import fcntl
        with (path.parent / 'lock').open('a') as lock:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: return
            state['phase'] = 'aborted'; save(path, state)
    if state['phase'] in ('rolled-back', 'aborted') and identity(target) == state['old_build']:
        stage = Path(state['stage'])
        incoming = Path(str(target) + '.incoming-' + state['token'])
        if incoming.exists(): shutil.rmtree(incoming)
        failed = Path(str(target) + '.failed-' + state['token'])
        if failed.exists():
            if identity(failed) != state['build']: raise ValueError('Failed application identity changed')
            shutil.rmtree(failed)
        if owned_stage(stage): remove_stage(stage)
        remove_transaction(path)
        return
    if state['phase'] in ('awaiting-health', 'recovery-required', 'healthy') and identity(target) == state['build']:
        installed = json.loads((target / 'Contents/Resources/server/_internal/viewer/version.json').read_text())
        if installed != state['expected']: raise ValueError('Installed package version mismatch')
        confirmed(target, installed, state['token'])
        (path.parent / 'healthy').write_text(state['token'])
        # The detached installer owns cleanup until it exits; later starts retry it.
        run(path)


def run(path, launch=None, timeout=120):
    import fcntl
    path = Path(path)
    if not path.exists(): return
    with (path.parent / 'lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return
        _run(path, launch, timeout)


def recover(path):
    import fcntl
    path = Path(path)
    with (path.parent / 'lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Update helper is still running; wait for its startup timeout, then rerun recover.command')
        _recover(path)


def healthy(path):
    from desktop.update_storage import record_error
    try: _healthy(path)
    except Exception as error:
        record_error(path, error)
        raise
