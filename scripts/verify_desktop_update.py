"""Isolated real desktop update smoke test against a local signed HTTP fixture.

Requires a built application containing this updater. Never uses /Applications
or a working installation. Test-only transport maps GitHub URLs to loopback;
production trust checks and signatures remain enabled. All launched processes
are restricted to the newly created verification directory.
"""
import argparse
import base64
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from desktop.updates import UpdateManager, REPO, LATEST
from desktop.update_storage import directory
from scripts.package_macos_update import package


def stop_tree(installation):
    """Stop only processes whose executable lives inside this test installation."""
    if sys.platform == 'darwin':
        rows = subprocess.check_output(['/bin/ps', '-axo', 'pid=,command='], text=True, errors='replace')
        for line in rows.splitlines():
            pid, _, command = line.strip().partition(' ')
            if command.startswith(str(installation) + '/'):
                try: os.kill(int(pid), signal.SIGTERM)
                except ProcessLookupError: pass
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            rows=subprocess.check_output(['/bin/ps','-axo','command='],text=True,errors='replace')
            if not any(line.strip().startswith(str(installation)+'/') for line in rows.splitlines()): break
            time.sleep(.1)
    elif sys.platform == 'win32':
        literal = str(installation.resolve()).replace("'", "''") + '\\'
        script = "Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith('" + literal + "', [StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force"
        encoded = base64.b64encode(script.encode('utf-16le')).decode()
        subprocess.run(['powershell.exe', '-NoProfile', '-EncodedCommand', encoded], check=True)


def wait(predicate, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(.3)
    raise TimeoutError('Verification timed out')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('application', type=Path)
    parser.add_argument('--output', type=Path, default=Path('build/update-verification/runs'))
    args = parser.parse_args()
    if sys.platform not in ('darwin', 'win32'): parser.error('Requires macOS or Windows')
    mac = sys.platform == 'darwin'
    args.output.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='isolated-', dir=args.output.resolve()))
    user = folder/'user-data'; user.mkdir()
    preferences={'heliostat-operation-gemasolar':'{"startDni":400,"startElevation":15,"stopDni":100,"stopElevation":5}'}
    (user/'desktop-preferences.json').write_text(json.dumps(preferences))
    (user/'settings.json').write_text('{"concentratingStart":600}')
    (user/'mirror-field.csv').write_text('mirror_id,east,north,height\nuser-1,1,2,3\n')
    before = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in user.iterdir()}
    os.environ['HELIOSTAT_VIEWER_DATA'] = str(user)
    name = '塔式镜场设计与优化.app' if mac else 'installed'
    target = folder/'installed'/name if mac else folder/'installed'
    new = folder/'release'/'塔式镜场设计与优化.app' if mac else folder/'release/app'
    target.parent.mkdir(parents=True, exist_ok=True); new.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.application, target, symlinks=True)
    shutil.copytree(args.application, new, symlinks=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    def resources(app): return app/'Contents/Resources/server/_internal' if mac else app/'server/_internal'
    for app,build in [(target,90001),(new,90002)]:
        (resources(app)/'viewer/update-public.pem').write_bytes(public)
        (resources(app)/'viewer/version.json').write_text(json.dumps({'version':f'90.0.{build-90000}','build':build}))
        if mac:
            path = app/'Contents/Info.plist'; info=plistlib.loads(path.read_bytes())
            info.update(CFBundleVersion=str(build),CFBundleShortVersionString=f'90.0.{build-90000}')
            path.write_bytes(plistlib.dumps(info))
            subprocess.run(['/usr/bin/codesign','--force','--deep','--sign','-','--timestamp=none',str(app)],check=True)
    feed=folder/'feed';feed.mkdir()
    archive=feed/'package.zip'
    if mac: package(new,archive)
    else:
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            for p in new.rglob('*'):
                if p.is_file(): z.write(p,p.relative_to(new))
    platform='macos-arm64' if mac else 'windows-x64'
    manifest=dict(schema=1,version='90.0.2',build=90002,notes='Isolated test update',platforms={platform:dict(
        url=REPO+'/releases/download/v90.0.2/package.zip',size=archive.stat().st_size,
        sha256=hashlib.file_digest(archive.open('rb'),'sha256').hexdigest(),channel='desktop')})
    raw=json.dumps(manifest).encode()
    envelope=dict(payload=base64.b64encode(raw).decode(),signature=base64.b64encode(key.sign(raw,padding.PKCS1v15(),hashes.SHA256())).decode())
    (feed/'updates.json').write_text(json.dumps(envelope))
    (feed/'latest').write_text(json.dumps(dict(tag_name='v90.0.2',assets=[dict(name='updates.json',browser_download_url=REPO+'/releases/download/v90.0.2/updates.json')])))
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,directory=str(feed)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    class LocalSession(requests.Session):
        def get(self,url,**kwargs):
            suffix='latest' if url==LATEST else url.rsplit('/',1)[-1]
            return super().get(f'http://127.0.0.1:{server.server_port}/{suffix}',**kwargs)
    native=target/'Contents/MacOS/塔式镜场设计与优化' if mac else target/'HeliostatViewer.exe'
    old=subprocess.Popen([str(native)],env=dict(os.environ))
    os.environ['HELIOSTAT_APP_PATH']=str(target);os.environ['HELIOSTAT_APP_PID']=str(old.pid)
    manager=UpdateManager(resources(target));manager.http=LocalSession()
    result=dict(system=sys.platform,folder=str(folder),transport='test-only local URL mapping; real RSA signature verification',rust='build-dependent')
    try:
        manager.start('check');wait(lambda:manager.worker is None)
        assert manager.status()['state']=='available',manager.status()
        manager.start('download');wait(lambda:manager.worker is None)
        assert manager.status()['state']=='ready',manager.status()
        stage=manager.stage
        manager.start('install', {'preferences': preferences})
        wait(lambda:manager.status()['state'] in ('installing','error'))
        assert manager.status()['state']=='installing',manager.status()
        # The installer is detached. Terminate only the isolated old native
        # application and its server; never any installed working application.
        stop_tree(target);old.wait(timeout=20)
        receipt=directory(target)/'health.json'
        wait(lambda:receipt.exists() and json.loads(receipt.read_text())['identity']==dict(version='90.0.2',build=90002),150)
        wait(lambda:not stage.exists() and not manager._transaction().exists(),150)
        assert not Path(str(target)+'.previous').exists()
        assert {name:hashlib.sha256((user/name).read_bytes()).hexdigest() for name in before}==before
        result.update(passed=True,checks=['real old process exit','detached frozen helper','signed local download',
            'real installed native process start','service and main viewer initialization callback','backup and stage cleanup','operation preferences and sentinel mirror-data hashes unchanged'])
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except Exception as error:
        result.update(passed=False,error=str(error));print(json.dumps(result,ensure_ascii=False,indent=2));raise
    finally:
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        stop_tree(target);server.shutdown();server.server_close()


if __name__=='__main__': main()
