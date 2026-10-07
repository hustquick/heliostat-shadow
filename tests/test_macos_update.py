import json
import plistlib
import shutil
from pathlib import Path

import pytest
from desktop import macos_update as update


@pytest.fixture(autouse=True)
def private_update_storage(tmp_path, monkeypatch):
    monkeypatch.setenv('HELIOSTAT_VIEWER_DATA', str(tmp_path / 'user-data'))


def app(path, build):
    (path / 'Contents').mkdir(parents=True)
    viewer = path / 'Contents/Resources/server/_internal/viewer'
    viewer.mkdir(parents=True)
    (viewer / 'version.json').write_text(json.dumps({'version': f'1.0.{build}', 'build': build}))
    (path / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': update.BUNDLE, 'CFBundleVersion': str(build)}))


@pytest.fixture
def transaction(tmp_path, monkeypatch):
    target = tmp_path / 'Viewer.app'; app(target, 1)
    stage = tmp_path / 'heliostat-update-test'; stage.mkdir()
    (stage / 'owner.json').write_text('{"app":"heliostat-shadow"}')
    monkeypatch.setattr(update, 'authenticate_stage', lambda *a: None)
    monkeypatch.setattr(update, 'confirmed', lambda *a: None)
    app(stage / 'unpacked/塔式镜场设计与优化.app', 2)
    path = update.prepare(target, stage, 2, 99999999)
    def command(args, **kwargs):
        if args[0].endswith('ditto'): shutil.copytree(args[1], args[2])
    monkeypatch.setattr(update.subprocess, 'run', command)
    return target, stage, path


def test_success_waits_for_health(transaction):
    target, stage, path = transaction
    def launch(_):
        assert Path(str(target)+'.previous').exists()
        state = json.loads(path.read_text())
        (path.parent/'healthy').write_text(state['token'])
    update.run(path, launch, timeout=.01)
    assert update.identity(target) == '2'
    assert not stage.exists() and not path.parent.exists()
    assert not Path(str(target)+'.previous').exists()


def test_bad_validation_preserves_current(transaction, monkeypatch):
    target, stage, path = transaction
    monkeypatch.setattr(update.subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(ValueError('signature')))
    with pytest.raises(ValueError): update.run(path, lambda _: None)
    assert update.identity(target) == '1'


def test_replace_failure_restores(transaction, monkeypatch):
    target, stage, path = transaction
    original = Path.rename
    def rename(self, dest):
        if '.incoming-' in self.name: raise OSError('replace failure')
        return original(self, dest)
    monkeypatch.setattr(Path, 'rename', rename)
    with pytest.raises(OSError): update.run(path, lambda _: None)
    assert update.identity(target) == '1'
    assert json.loads(path.read_text())['phase'] == 'rolled-back'


def test_start_failure_has_executable_recovery(transaction):
    target, stage, path = transaction
    with pytest.raises(TimeoutError): update.run(path, lambda _: None, timeout=0)
    assert Path(str(target)+'.previous').exists()
    update.recover(path)
    assert update.identity(target) == '1'


def test_cleanup_failure_retries_on_healthy_start(transaction, monkeypatch):
    target, stage, path = transaction
    original = shutil.rmtree
    def fail_backup(p, *a, **k):
        if str(p).endswith('.previous'): raise PermissionError('cleanup denied')
        original(p, *a, **k)
    monkeypatch.setattr(shutil, 'rmtree', fail_backup)
    def launch(_):
        (path.parent/'healthy').write_text(json.loads(path.read_text())['token'])
    with pytest.raises(PermissionError): update.run(path, launch)
    assert 'cleanup denied' in (path.parent/'errors.log').read_text()
    monkeypatch.setattr(shutil, 'rmtree', original)
    update.healthy(path)
    assert not path.parent.exists() and not stage.exists()


def test_unknown_previous_is_preserved(transaction):
    target, stage, path = transaction
    backup = Path(str(target)+'.previous'); app(backup, 0)
    with pytest.raises(ValueError, match='Existing backup'): update.run(path, lambda _: None)
    assert update.identity(target) == '1' and backup.exists()


def test_health_without_initialization_keeps_backup(transaction):
    target, stage, path = transaction
    with pytest.raises(TimeoutError): update.run(path, lambda _: None, timeout=0)
    update.run(path, lambda _: None)
    assert Path(str(target)+'.previous').exists() and stage.exists()


def test_legacy_backup_requires_identity_and_older_build(tmp_path, monkeypatch):
    target = tmp_path/'Viewer.app'; app(target, 2)
    backup = Path(str(target)+'.previous'); app(backup, 1)
    viewer = backup/'Contents/Resources/server/_internal/viewer'
    viewer.mkdir(parents=True, exist_ok=True); (viewer/'version.json').write_text('{"build":1}')
    other = tmp_path/'Other.app.previous'; app(other, 1)
    monkeypatch.setattr(update.subprocess, 'run', lambda *a, **k: None)
    update.healthy(Path(str(target)+'.update-state/state.json'))
    assert not backup.exists() and other.exists()


def test_launch_command_failure_retains_recovery(transaction):
    target, stage, path = transaction
    def fail(_): raise OSError('open failed')
    with pytest.raises(OSError): update.run(path, fail)
    assert json.loads(path.read_text())['phase'] == 'recovery-required'
    assert Path(str(target)+'.previous').exists()


def test_partial_backup_deletion_can_retry_without_info_plist(transaction,monkeypatch):
    target,stage,path=transaction
    original=shutil.rmtree
    def partial(p,*a,**k):
        if str(p).endswith('.previous'):
            (Path(p)/'Contents/Info.plist').unlink()
            raise PermissionError('partial cleanup')
        return original(p,*a,**k)
    monkeypatch.setattr(shutil,'rmtree',partial)
    def launch(_):(path.parent/'healthy').write_text(json.loads(path.read_text())['token'])
    with pytest.raises(PermissionError):update.run(path,launch)
    assert json.loads(path.read_text())['backupValidated']
    monkeypatch.setattr(shutil,'rmtree',original)
    update.healthy(path)
    assert not path.exists() and not stage.exists()


def test_replaced_backup_directory_is_never_deleted(transaction,monkeypatch):
    target,stage,path=transaction
    backup=Path(str(target)+'.previous')
    def launch(_):
        backup.rename(Path(str(backup)+'.user-kept'))
        app(backup,1)
        (path.parent/'healthy').write_text(json.loads(path.read_text())['token'])
    with pytest.raises(ValueError,match='directory identity'):update.run(path,launch)
    assert backup.exists()


def test_old_running_window_cannot_confirm_new_installation(transaction,monkeypatch):
    target,stage,path=transaction
    with pytest.raises(TimeoutError):update.run(path,lambda _:None,timeout=0)
    monkeypatch.setenv('HELIOSTAT_CONFIRM_BUILD','1')
    update.healthy(path)
    assert not (path.parent/'healthy').exists()
    assert Path(str(target)+'.previous').exists()
