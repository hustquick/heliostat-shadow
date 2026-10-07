"""File-journaled Windows portable/per-user update; never touches user storage."""
import json
import base64
import re
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

from desktop.update_storage import directory, owned_stage, write, authenticate_stage, confirmed, remove_stage, remove_transaction


def identity(target):
    return json.loads((target / 'server/_internal/viewer/version.json').read_text())


def files(staged):
    result = []
    for path in sorted(staged.rglob('*')):
        if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400: raise ValueError('Update contains a reparse/symlink file')
        if not path.is_file(): continue
        rel = path.relative_to(staged)
        root_file = len(rel.parts) == 1 and (
            path.suffix.lower() in ('.exe', '.dll', '.pdb')
            or path.name in ('HeliostatViewer.deps.json', 'HeliostatViewer.runtimeconfig.json')
            or (path.name.startswith('Microsoft.Web.WebView2.') and path.suffix.lower() == '.xml'))
        satellite = (len(rel.parts) == 2 and re.fullmatch(r'[a-z]{2}(?:-[A-Za-z]{2,4})?', rel.parts[0])
                     and path.name.endswith('.resources.dll'))
        runtime = (rel.parts[:3] == ('runtimes', 'win-x64', 'native') and len(rel.parts) == 4
                   and path.suffix.lower() == '.dll')
        if rel.parts[0] != 'server' and not (root_file or satellite or runtime):
            raise ValueError('Unexpected application file: ' + str(rel))
        if len(rel.parts) == 1 and path.name.lower().startswith('unins'):
            raise ValueError('Update may not replace uninstaller')
        result.append(rel.as_posix())
    if 'HeliostatViewer.exe' not in result: raise ValueError('Missing executable')
    return result


def prepare(target, stage, expected, pid):
    folder = directory(target) / 'transaction'
    folder.mkdir()  # One unresolved installation per target.
    state = dict(schema=1, token=uuid.uuid4().hex, target=str(target.resolve()), stage=str(stage.resolve()),
                 old=identity(target), expected=expected, pid=pid, phase='prepared', journal=[], allowed=files(stage/'unpacked'))
    path = folder / 'state.json'; write(path, state)
    runtime = stage / 'helper-runtime/heliostat-viewer-server.exe'
    command = folder / 'recover.cmd'
    command.write_text('@echo off\r\n"' + str(runtime) + '" --windows-recover "' + str(path) + '"\r\npause\r\n')
    return path


def load(path):
    path = Path(path); state = json.loads(path.read_text())
    target = Path(state['target']); stage = Path(state['stage'])
    if (state['schema'] != 1 or not re.fullmatch('[0-9a-f]{32}', state['token']) or path.resolve() != (directory(target)/'transaction/state.json').resolve()
            or (stage.exists() and not owned_stage(stage)) or stage.resolve().parent != directory(target).resolve()):
        raise ValueError('Invalid update transaction ownership')
    for entry in state['journal']:
        rel = Path(entry['file'])
        if rel.is_absolute() or rel.drive or ':' in entry['file'] or '..' in rel.parts or '\\' in entry['file'] or entry['file'] not in state['allowed']:
            raise ValueError('Invalid journal path')
    return state, target, stage


def wait_parent(pid, timeout):
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            if ctypes.get_last_error() in (87, 1168): return
            raise PermissionError('Cannot determine whether the old application exited')
        try:
            if kernel.WaitForSingleObject(ctypes.c_void_p(handle), int(timeout * 1000)) != 0:
                raise TimeoutError('Application is still running')
        finally: kernel.CloseHandle(ctypes.c_void_p(handle))
    else:
        deadline = time.monotonic()+timeout
        while True:
            try: os.kill(pid, 0)
            except ProcessLookupError: return
            if time.monotonic() >= deadline: raise TimeoutError('Application is still running')
            time.sleep(.1)


def rollback(path, state, target):
    backup = Path(path).parent/'backup'
    # Journal is persisted before each destination mutation. Backups are durable first.
    for entry in reversed(state['journal']):
        dest = target/entry['file']; old = backup/entry['file']
        if entry['old']:
            if not old.is_file(): raise ValueError('Missing recovery file: '+entry['file'])
            shutil.copy2(old, dest)
        elif dest.exists(): dest.unlink()
    state['phase'] = 'rolled-back'; write(Path(path), state)


def run(path, launch=None, timeout=120):
    path = Path(path)
    with transaction_lock(path) as locked:
        if not locked: return
        state, target, stage = load(path)
        launch = launch or (lambda app: subprocess.Popen([str(app/'HeliostatViewer.exe')], cwd=app))
        old_exited = False
        try:
            if state['phase'] == 'prepared':
                wait_parent(state['pid'], timeout)
                old_exited = True
                authenticate_stage(stage, 'windows-x64', state['expected'])
                staged = stage/'unpacked'
                if identity(staged) != state['expected']: raise ValueError('Staged version changed')
                inventory = files(staged)
                if inventory != state['allowed']: raise ValueError('Authenticated file inventory changed')
                # Back up *all* overwritten files before any replacement.
                plan = []
                for name in inventory:
                    dest = target/name; old = path.parent/'backup'/name
                    if dest.is_symlink() or any(p.is_symlink() or (p.exists() and getattr(p.lstat(), 'st_file_attributes', 0) & 0x400) for p in dest.parents):
                        raise ValueError('Refusing destination reparse/symlink')
                    if dest.exists():
                        old.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(dest, old)
                        # Windows FlushFileBuffers requires a writable file handle.
                        with old.open('r+b') as stream: os.fsync(stream.fileno())
                    plan.append(dict(file=name, old=dest.exists()))
                state['phase'] = 'replacing'; write(path, state)
                for entry in plan:
                    state['journal'].append(entry); write(path, state)
                    dest = target/entry['file']; dest.parent.mkdir(parents=True, exist_ok=True)
                    # Retry sharing violations, including server children shutting down.
                    for attempt in range(30):
                        try: shutil.copy2(staged/entry['file'], dest); break
                        except PermissionError:
                            if attempt == 29: raise
                            time.sleep(.2)
                state['phase'] = 'awaiting-health'; write(path, state)
                launch(target)
                deadline = time.monotonic()+timeout
                while not (path.parent/'healthy').exists():
                    if time.monotonic() >= deadline: raise TimeoutError('New version did not initialize; quit it and run recover.cmd')
                    time.sleep(.2)
            if state['phase'] == 'replacing':
                # Interrupted replacement: never continue an incomplete copy blindly.
                wait_parent(state['pid'], timeout); rollback(path, state, target); launch(target)
                return
            if state['phase'] in ('awaiting-health','recovery-required','healthy'):
                marker = path.parent/'healthy'
                if not marker.exists() or marker.read_text() != state['token']: return
                if identity(target) != state['expected']: raise ValueError('Installed version changed')
                state['phase'] = 'healthy'; write(path, state)
                cleanup(path, state, stage)
        except Exception as error:
            if state['phase'] == 'replacing':
                try: rollback(path, state, target); launch(target)
                except Exception as recovery:
                    state['phase']='recovery-required'; state['recovery_error']=str(recovery)
            elif state['phase']=='prepared':
                state['phase']='aborted'; write(path,state)
                if old_exited:
                    try: launch(target)
                    except Exception as recovery: state['recovery_error']=str(recovery)
            elif state['phase']=='awaiting-health': state['phase']='recovery-required'
            state['error']=str(error); write(path, state)
            with (path.parent/'errors.log').open('a') as stream: stream.write(str(error)+'\n')
            raise


def cleanup(path, state, stage):
    # Backup and runtime are updater-owned; no traversal of the installation or user data.
    backup = path.parent/'backup'
    if backup.exists(): shutil.rmtree(backup)
    # A running Windows helper holds its EXE/DLLs open. Leave runtime removal to
    # the next launch running from the installed server, not the helper copy.
    if not stage.exists():
        state['phase']='complete'; write(path,state); return
    runtime = stage/'helper-runtime'
    current = Path(__import__('sys').executable).resolve()
    for item in stage.iterdir():
        if item == runtime and current.is_relative_to(runtime.resolve()): continue
        if item.name == 'owner.json': continue
        if item.is_dir(): shutil.rmtree(item)
        else: item.unlink()
    if runtime.exists():
        state['phase']='cleanup-pending'; write(path, state); return
    remove_stage(stage)
    state['phase']='complete'; write(path,state)


def _healthy(path):
    path = Path(path)
    if not path.exists(): return
    if json.loads(path.read_text())['phase']=='complete': finalize(path); return
    state, target, stage = load(path)
    runtime = os.environ.get('HELIOSTAT_CONFIRM_IDENTITY')
    expected_runtime = state['old'] if state['phase'] in ('prepared','rolled-back','aborted') else state['expected']
    if runtime is not None and json.loads(runtime) != expected_runtime: return
    if state['phase']=='prepared':
        try: wait_parent(state['pid'], 0)
        except TimeoutError: return
        with transaction_lock(path) as locked:
            if not locked: return
            state['phase']='aborted'; write(path,state)
    if state['phase'] in ('awaiting-health','recovery-required','healthy','cleanup-pending') and identity(target)==state['expected']:
        confirmed(target, state['expected'], state['token'])
        (path.parent/'healthy').write_text(state['token'])
        if state['phase']=='cleanup-pending':
            with transaction_lock(path) as locked:
                if locked:
                    try: cleanup(path,state,stage)
                    except Exception as error:
                        state['error']=str(error); write(path,state)
                        with (path.parent/'errors.log').open('a') as log: log.write(str(error)+'\n')
        else:
            run(path)
            # The running helper owns the lock; wait for it to exit before
            # deleting its copied runtime, without holding up the UI process.
            deadline=time.monotonic()+150
            while path.exists() and time.monotonic()<deadline:
                try:
                    record=json.loads(path.read_text())
                    if record['phase'] in ('healthy','cleanup-pending'):
                        with transaction_lock(path) as locked:
                            if locked:
                                cleanup(path,record,stage); break
                    elif record['phase']=='complete': break
                except Exception as error:
                    record['error']=str(error); write(path,record)
                    with (path.parent/'errors.log').open('a') as log: log.write(str(error)+'\n')
                    break
                time.sleep(.2)
        finalize(path)
    elif state['phase'] in ('rolled-back','aborted') and identity(target)==state['old']:
        with transaction_lock(path) as locked:
            if locked: cleanup(path,state,stage)
        finalize(path)


def recover(path):
    path=Path(path)
    with transaction_lock(path) as locked:
        if not locked: raise RuntimeError('Update helper is still running')
        state,target,stage=load(path)
        if state['phase'] not in ('replacing','awaiting-health','recovery-required'): raise ValueError('Recovery not required')
        if os.name=='nt':
            literal=str(target.resolve()).replace("'", "''") + "\\"
            script="$found = Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith('"+literal+"', [StringComparison]::OrdinalIgnoreCase) }; if ($found) { exit 1 }"
            encoded=base64.b64encode(script.encode('utf-16le')).decode()
            result=subprocess.run(['powershell.exe','-NoProfile','-EncodedCommand',encoded],capture_output=True)
            if result.returncode: raise RuntimeError('Quit the application and its server before running recover.cmd')
        rollback(path,state,target)
        subprocess.Popen([str(target/'HeliostatViewer.exe')],cwd=target)


from contextlib import contextmanager
@contextmanager
def transaction_lock(path):
    lock=Path(path).parent/'lock'
    if not lock.parent.exists(): yield False; return
    with lock.open('a+b') as stream:
        if os.name=='nt':
            import msvcrt
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0: stream.write(b'0'); stream.flush()
            stream.seek(0)
            try: msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            except OSError: yield False; return
            try: yield True
            finally:
                stream.seek(0); msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            try: fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: yield False; return
            yield True


def finalize(path):
    path=Path(path)
    if path.exists() and json.loads(path.read_text())['phase']=='complete':
        try: remove_transaction(path)
        except Exception as error:
            state=json.loads(path.read_text()); state['error']=str(error); write(path,state)
            with (path.parent/'errors.log').open('a') as log: log.write(str(error)+'\n')


def healthy(path):
    from desktop.update_storage import record_error
    try: _healthy(path)
    except Exception as error:
        record_error(path, error)
        raise
