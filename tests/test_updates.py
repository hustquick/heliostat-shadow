import base64
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from desktop.updates import REPO, UpdateManager, extract_archive, verify_manifest, version_tuple


@pytest.fixture
def identity(tmp_path):
    viewer = tmp_path / 'viewer'; viewer.mkdir()
    (viewer / 'version.json').write_text('{"version":"1.0.3","build":20004}')
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    (viewer / 'update-public.pem').write_bytes(public)
    return tmp_path, key, public


def signed(key, **changes):
    manifest = {'schema': 1, 'version': '1.0.4', 'build': 20005, 'notes': 'test', 'platforms': {
        'windows-x64': {'url': REPO + '/releases/download/v1.0.4/package.zip', 'size': 100, 'sha256': 'a' * 64}}}
    manifest.update(changes)
    raw = json.dumps(manifest).encode()
    return {'payload': base64.b64encode(raw).decode(),
            'signature': base64.b64encode(key.sign(raw, padding.PKCS1v15(), hashes.SHA256())).decode()}


def test_manifest_tamper_and_wrong_origin_are_rejected(identity):
    _, key, public = identity
    envelope = signed(key)
    assert verify_manifest(envelope, public)['version'] == '1.0.4'
    envelope['payload'] = base64.b64encode(base64.b64decode(envelope['payload']).replace(b'1.0.4', b'9.9.9')).decode()
    with pytest.raises(InvalidSignature): verify_manifest(envelope, public)
    bad = signed(key, platforms={'windows-x64': {'url': 'https://evil.example/app.zip', 'sha256': 'a'*64, 'size': 100}})
    with pytest.raises(ValueError, match='来源'): verify_manifest(bad, public)


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'dir/../../escape', 'dir\\escape'])
def test_archive_cannot_write_outside_stage(tmp_path, name):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as z: z.writestr(name, b'bad')
    with pytest.raises(ValueError): extract_archive(archive, tmp_path / 'out')


def test_safe_symlinks_preserved_and_escaping_links_rejected(tmp_path):
    archive = tmp_path / 'links.zip'
    link = zipfile.ZipInfo('app/link'); link.external_attr = 0o120777 << 16
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('app/file', b'data'); z.writestr(link, 'file')
    extract_archive(archive, tmp_path / 'out')
    assert (tmp_path / 'out/app/link').is_symlink()
    assert (tmp_path / 'out/app/link').read_bytes() == b'data'
    with zipfile.ZipFile(archive, 'w') as z: z.writestr(link, '../../outside')
    with pytest.raises(ValueError): extract_archive(archive, tmp_path / 'other')


class Response:
    def __init__(self, data=None, content=b''):
        self.data, self.content = data, content
    def raise_for_status(self): pass
    def json(self): return self.data
    def iter_content(self, _): yield self.content
    def __enter__(self): return self
    def __exit__(self, *_): pass


def test_downgrade_or_nonincreasing_build_is_not_available(identity, monkeypatch):
    root, key, _ = identity
    for version, build in [('1.0.2', 20005), ('1.0.4', 20003), ('1.0.3', 20005)]:
        manager = UpdateManager(root)
        responses = iter([Response({'tag_name': 'v'+version, 'assets': [{'name':'updates.json', 'browser_download_url':REPO+'/releases/download/v'+version+'/updates.json'}]}),
                          Response(signed(key, version=version, build=build, platforms={}))])
        monkeypatch.setattr(manager.http, 'get', lambda *a, **k: next(responses))
        manager._check()
        assert manager.status()['state'] == 'current'
        assert manager.manifest is None


def test_verified_download_and_corruption_never_becomes_installable(identity, monkeypatch):
    root, _, _ = identity
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr('HeliostatViewer.exe', b'test')
        z.writestr('server/_internal/viewer/version.json', '{"version":"1.0.4","build":20005}')
    content = stream.getvalue()
    manager = UpdateManager(root); manager.platform = 'windows-x64'
    manager.manifest = {'version':'1.0.4','build':20005,'platforms': {'windows-x64': {
        'url':REPO+'/releases/download/v1.0.4/package.zip','size':len(content),'sha256':hashlib.sha256(content).hexdigest()}}}
    monkeypatch.setattr(manager.http, 'get', lambda *a, **k: Response(content=content))
    manager._download()
    assert manager.status()['state'] == 'ready'
    import shutil
    shutil.rmtree(manager.stage)
    manager.stage = None
    monkeypatch.setattr(manager.http, 'get', lambda *a, **k: Response(content=content[:-1]))
    manager._download()
    assert manager.status()['state'] == 'error'
    assert manager.stage is None
    with pytest.raises(ValueError): manager.start('install')


def test_numeric_version_comparison():
    assert version_tuple('1.10.0') > version_tuple('1.9.9')
    with pytest.raises(ValueError): version_tuple('1.0.3-beta')


def test_publication_rejects_existing_version_and_nonincreasing_build(identity, monkeypatch):
    from scripts import publish_release
    root, key, _ = identity
    (root / 'VERSION').write_text('1.0.4')
    (root / 'BUILD_NUMBER').write_text('20004')
    monkeypatch.setattr(publish_release, 'ROOT', root)
    monkeypatch.setattr('sys.argv', ['publish', str(root / 'assets')])
    release = {'tag_name':'v1.0.4','draft':False,'prerelease':False,'assets':[]}
    monkeypatch.setattr(publish_release, 'gh', lambda *args: json.dumps([release]))
    with pytest.raises(ValueError, match='禁止覆盖'): publish_release.main()
    release.update(tag_name='v1.0.3', assets=[{'name':'updates.json'}])
    monkeypatch.setattr(publish_release, 'gh', lambda *args: json.dumps([release]) if args[0]=='api' else json.dumps(signed(key,version='1.0.3',build=20004,platforms={})))
    with pytest.raises(ValueError, match='构建号'): publish_release.main()


def test_update_routes_reject_cross_origin_install_requests(identity):
    from functools import partial
    import threading
    import requests
    from scripts.serve_viewer import LocalViewerServer, ViewerHandler
    server = LocalViewerServer(('127.0.0.1',0), partial(ViewerHandler,model=None))
    server.updater = UpdateManager(identity[0])
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    url = f'http://127.0.0.1:{server.server_port}/api/update/'
    try:
        assert requests.get(url+'status').json()['current'] == '1.0.3'
        response = requests.post(url+'install',json={},headers={'Origin':'https://evil.example'})
        assert response.status_code == 400
        assert '来源' in response.json()['error']
    finally:
        server.shutdown();server.server_close();thread.join()


def test_download_network_failure_preserves_installed_app(identity, monkeypatch, tmp_path):
    root, _, _ = identity
    target = tmp_path / 'installed'; target.mkdir()
    (target / 'keep').write_text('old application')
    manager = UpdateManager(root); manager.target = target
    def fail(*args, **kwargs): raise ConnectionError('download failed')
    monkeypatch.setattr(manager.http, 'get', fail)
    manager.platform = 'windows-x64'
    manager.manifest = {'platforms': {'windows-x64': {'url': REPO, 'size': 10, 'sha256': 'a'*64}}}
    manager._download()
    assert manager.status()['state'] == 'error' and manager.stage is None
    assert (target / 'keep').read_text() == 'old application'


def test_cancel_during_download_preserves_app_and_discards_owned_stage(identity, monkeypatch, tmp_path):
    root, _, _ = identity
    manager = UpdateManager(root); manager.platform = 'windows-x64'
    manager.manifest = {'platforms': {'windows-x64': {'url': REPO, 'size': 20, 'sha256': 'a'*64}}}
    class CancelResponse(Response):
        def iter_content(self, _):
            manager.start('cancel')
            yield b'download'
    monkeypatch.setattr(manager.http, 'get', lambda *a, **k: CancelResponse())
    manager._download()
    assert manager.status()['state'] == 'cancelled' and manager.stage is None


def test_cancel_does_not_start_an_overlapping_task(identity, monkeypatch):
    import threading
    root, _, _ = identity
    manager = UpdateManager(root)
    released, running = threading.Event(), threading.Event()
    def check():
        running.set(); released.wait(3)
    monkeypatch.setattr(manager, '_check', check)
    manager.start('check'); assert running.wait(1)
    worker = manager.worker
    manager.start('cancel'); manager.start('check')
    assert manager.worker is worker
    released.set(); worker.join(3)
    assert manager.worker is None


def test_interrupted_download_is_recoverable_and_unknown_stage_is_not_deleted(identity, monkeypatch, tmp_path):
    from desktop.update_storage import write
    root, _, _ = identity
    user = tmp_path/'private'; monkeypatch.setenv('HELIOSTAT_VIEWER_DATA',str(user))
    target=tmp_path/'installed';target.mkdir();monkeypatch.setenv('HELIOSTAT_APP_PATH',str(target))
    manager=UpdateManager(root)
    unknown=tmp_path/'heliostat-update-unknown';unknown.mkdir();(unknown/'user-data').write_text('keep')
    write(manager.session_file,{'status':{'state':'downloading'},'envelope':None,'stage':str(unknown)})
    restarted=UpdateManager(root)
    assert restarted.status()['state']=='error' and restarted.stage is None
    assert (unknown/'user-data').read_text()=='keep'


def test_ready_stage_reauthenticated_after_restart_and_tampering_rejected(identity, monkeypatch, tmp_path):
    from desktop import updates
    from desktop.update_storage import write
    root, key, _=identity
    monkeypatch.setenv('HELIOSTAT_VIEWER_DATA',str(tmp_path/'private'))
    target=tmp_path/'installed';target.mkdir();monkeypatch.setenv('HELIOSTAT_APP_PATH',str(target))
    monkeypatch.setattr(updates,'desktop_platform',lambda:'windows-x64')
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:
        z.writestr('HeliostatViewer.exe','app')
        z.writestr('server/_internal/viewer/version.json','{"version":"1.0.4","build":20005}')
    content=stream.getvalue()
    envelope=signed(key,platforms={'windows-x64':{'url':REPO+'/releases/download/v1.0.4/package.zip','size':len(content),'sha256':hashlib.sha256(content).hexdigest()}})
    manager=UpdateManager(root);manager.envelope=envelope;manager.manifest=verify_manifest(envelope,manager.public_key())
    monkeypatch.setattr(manager.http,'get',lambda *a,**k:Response(content=content))
    manager._download();assert manager.status()['state']=='ready'
    (manager.stage/'unpacked/HeliostatViewer.exe').write_text('tampered unpacked')
    restored=UpdateManager(root)
    assert restored.status()['state']=='ready'
    assert (restored.stage/'unpacked/HeliostatViewer.exe').read_text()=='app'
    (restored.stage/'package.zip').write_bytes(b'corrupt')
    rejected=UpdateManager(root)
    assert rejected.status()['state']=='error'


def test_operation_preferences_survive_update_cleanup_and_restart(identity, monkeypatch, tmp_path):
    from desktop.update_storage import remove_stage, write
    root, _, _ = identity
    monkeypatch.setenv('HELIOSTAT_VIEWER_DATA',str(tmp_path/'user-data'))
    target=tmp_path/'installed';target.mkdir();monkeypatch.setenv('HELIOSTAT_APP_PATH',str(target))
    manager=UpdateManager(root)
    values={'heliostat-operation-gemasolar':'{"startDni":400,"startElevation":15,"stopDni":100,"stopElevation":5}'}
    manager._save_preferences(values)
    stage=manager.session_file.parent/'heliostat-update-owned';stage.mkdir();write(stage/'owner.json',{'app':'heliostat-shadow'})
    remove_stage(stage)
    assert UpdateManager(root).preferences()==values
    with pytest.raises(ValueError): manager._save_preferences({'unrelated-user-key':'{}'})
    assert manager.preferences()==values


def test_duplicate_native_window_cannot_start_a_second_update(identity, monkeypatch, tmp_path):
    import threading
    root, _, _=identity
    monkeypatch.setenv('HELIOSTAT_VIEWER_DATA',str(tmp_path/'private'))
    target=tmp_path/'installed';target.mkdir();monkeypatch.setenv('HELIOSTAT_APP_PATH',str(target))
    first=UpdateManager(root);entered=threading.Event();release=threading.Event()
    def paused_check():
        entered.set();release.wait(3);first.state.update(state='current')
    monkeypatch.setattr(first,'_check',paused_check)
    first.start('check');assert entered.wait(1)
    second=UpdateManager(root)
    assert second.status()['state']=='checking'
    with pytest.raises(ValueError,match='另一个'):second.start('check')
    worker=first.worker;release.set();worker.join(3)
    assert second.status()['state']=='current'
