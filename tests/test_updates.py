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
