import json
from pathlib import Path
import shutil
import pytest
from desktop import windows_update as update
from desktop.update_storage import write


def app(path, build):
    viewer = path / 'server/_internal/viewer'; viewer.mkdir(parents=True)
    write(viewer/'version.json', {'version': f'1.0.{build}', 'build': build})
    (path/'HeliostatViewer.exe').write_text(f'exe-{build}')
    (path/'runtime.dll').write_text(f'dll-{build}')


@pytest.fixture
def transaction(tmp_path, monkeypatch):
    base=tmp_path/'private-updates'; base.mkdir()
    monkeypatch.setattr(update,'directory',lambda _:base)
    monkeypatch.setattr(update,'authenticate_stage',lambda *a:None)
    monkeypatch.setattr(update,'confirmed',lambda *a:None)
    target=tmp_path/'installed'; app(target,1)
    (target/'unins000.dat').write_text('uninstaller')
    (target/'user-settings.txt').write_text('settings')
    stage=base/'heliostat-update-test'; stage.mkdir(); write(stage/'owner.json',{'app':'heliostat-shadow'})
    app(stage/'unpacked',2)
    path=update.prepare(target,stage,{'version':'1.0.2','build':2},99999999)
    return target,stage,path


def confirm(path):
    (path.parent/'healthy').write_text(json.loads(path.read_text())['token'])


def test_success_preserves_data_and_cleans(transaction):
    target,stage,path=transaction
    def launch(_):
        assert (path.parent/'backup/HeliostatViewer.exe').read_text()=='exe-1'
        confirm(path)
    update.run(path,launch)
    update.finalize(path)
    assert update.identity(target)['build']==2
    assert (target/'user-settings.txt').read_text()=='settings'
    assert (target/'unins000.dat').read_text()=='uninstaller'
    assert not stage.exists() and not path.parent.exists()


def test_failed_copy_rolls_back_all_mutations(transaction,monkeypatch):
    target,stage,path=transaction
    original=shutil.copy2
    def fail(src,dst,*a,**k):
        if Path(src).name=='runtime.dll' and 'unpacked' in str(src): raise OSError('copy failed')
        return original(src,dst,*a,**k)
    monkeypatch.setattr(shutil,'copy2',fail)
    with pytest.raises(OSError): update.run(path,lambda _:None)
    assert (target/'HeliostatViewer.exe').read_text()=='exe-1'
    assert update.identity(target)['build']==1
    assert json.loads(path.read_text())['phase']=='rolled-back'


def test_failed_start_and_recovery(transaction,monkeypatch):
    target,stage,path=transaction
    with pytest.raises(TimeoutError): update.run(path,lambda _:None,timeout=0)
    assert (path.parent/'backup').exists()
    monkeypatch.setattr(update.subprocess,'Popen',lambda *a,**k:None)
    update.recover(path)
    assert update.identity(target)['build']==1
    assert (target/'user-settings.txt').read_text()=='settings'


def test_cleanup_failure_retries(transaction,monkeypatch):
    target,stage,path=transaction
    original=shutil.rmtree
    def fail(path,*a,**k):
        if Path(path).name=='backup': raise PermissionError('cleanup denied')
        return original(path,*a,**k)
    monkeypatch.setattr(shutil,'rmtree',fail)
    with pytest.raises(PermissionError): update.run(path,lambda _:confirm(path))
    assert json.loads(path.read_text())['phase']=='healthy'
    monkeypatch.setattr(shutil,'rmtree',original)
    update.healthy(path)
    assert not path.exists() and not stage.exists()


def test_reboot_mid_replacement_restores(transaction):
    target,stage,path=transaction
    state=json.loads(path.read_text())
    old=path.parent/'backup/HeliostatViewer.exe'; old.parent.mkdir(); shutil.copy2(target/'HeliostatViewer.exe',old)
    state.update(phase='replacing',journal=[{'file':'HeliostatViewer.exe','old':True}]); write(path,state)
    (target/'HeliostatViewer.exe').write_text('partial')
    update.run(path,lambda _:None)
    assert (target/'HeliostatViewer.exe').read_text()=='exe-1'
    assert json.loads(path.read_text())['phase']=='rolled-back'


def test_unexpected_user_paths_rejected(transaction):
    target,stage,path=transaction
    (stage/'unpacked/user.txt').write_text('forbidden')
    with pytest.raises(ValueError,match='Unexpected'): update.run(path,lambda _:None)
    assert update.identity(target)['build']==1


def test_new_build_number_alone_does_not_confirm(transaction):
    target,stage,path=transaction
    with pytest.raises(TimeoutError): update.run(path,lambda _:None,timeout=0)
    update.run(path,lambda _:None)
    assert (path.parent/'backup').exists() and stage.exists()


def test_prepared_interruption_is_cancelled_on_old_healthy_start(transaction):
    target,stage,path=transaction
    update.healthy(path)
    assert update.identity(target)['build']==1 and not path.exists()


def test_helper_cannot_delete_own_runtime_and_installed_process_finishes_cleanup(transaction, monkeypatch):
    import sys
    target,stage,path=transaction
    runtime=stage/'helper-runtime';runtime.mkdir();(runtime/'heliostat-viewer-server.exe').write_text('helper')
    original=sys.executable
    monkeypatch.setattr(sys,'executable',str(runtime/'heliostat-viewer-server.exe'))
    update.run(path,lambda _:confirm(path))
    assert json.loads(path.read_text())['phase']=='cleanup-pending' and runtime.exists()
    monkeypatch.setattr(sys,'executable',original)
    update.healthy(path)
    assert not path.exists() and not stage.exists()


@pytest.mark.skipif(__import__('sys').platform!='win32',reason='Requires actual Windows sharing semantics')
def test_real_windows_file_sharing_failure_retains_recovery(transaction,monkeypatch):
    import ctypes
    from ctypes import wintypes
    target,stage,path=transaction
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    handle=kernel.CreateFileW(str(target/'runtime.dll'),0x80000000,1,None,3,0,None)
    assert handle not in (None,ctypes.c_void_p(-1).value)
    try:
        with pytest.raises(PermissionError):update.run(path,lambda _:None)
        assert (path.parent/'backup').exists()
    finally:kernel.CloseHandle(ctypes.c_void_p(handle))
    monkeypatch.setattr(update.subprocess,'Popen',lambda *a,**k:None)
    update.recover(path)
    assert update.identity(target)['build']==1


def test_existing_published_windows_runtime_layout_is_allowed(tmp_path):
    app(tmp_path,1)
    (tmp_path/'Microsoft.Web.WebView2.Core.xml').write_text('documentation')
    (tmp_path/'HeliostatViewer.deps.json').write_text('{}')
    (tmp_path/'zh-Hans').mkdir();(tmp_path/'zh-Hans/PresentationCore.resources.dll').write_text('satellite')
    (tmp_path/'runtimes/win-x64/native').mkdir(parents=True)
    (tmp_path/'runtimes/win-x64/native/WebView2Loader.dll').write_text('loader')
    assert 'runtimes/win-x64/native/WebView2Loader.dll' in update.files(tmp_path)


def test_old_running_window_cannot_confirm_new_installation(transaction,monkeypatch):
    target,stage,path=transaction
    with pytest.raises(TimeoutError):update.run(path,lambda _:None,timeout=0)
    monkeypatch.setenv('HELIOSTAT_CONFIRM_IDENTITY','{"version":"1.0.1","build":1}')
    update.healthy(path)
    assert not (path.parent/'healthy').exists() and (path.parent/'backup').exists()
