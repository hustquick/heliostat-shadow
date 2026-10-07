"""macOS fault checks using real signed bundles, codesign and filesystem errors.

Uses only a freshly copied fixture and private data. Network responses come from
loopback, with a test-only transport adapter; production trust checks are active.
Interruption is injected at a journal checkpoint, never by rebooting the host.
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
import subprocess
import tempfile
import threading
import time

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from desktop import macos_update
from desktop.updates import UpdateManager, REPO, LATEST
from desktop.update_storage import directory
from scripts.package_macos_update import package
from scripts.verify_desktop_update import wait, stop_tree


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('application',type=Path)
    parser.add_argument('--output',type=Path,default=Path('build/update-verification/failures'))
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    folder=Path(tempfile.mkdtemp(prefix='isolated-',dir=args.output.resolve()))
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    source=folder/'release/塔式镜场设计与优化.app';source.parent.mkdir()
    shutil.copytree(args.application,source,symlinks=True)
    def configure(app,build):
        internal=app/'Contents/Resources/server/_internal'
        (internal/'viewer/update-public.pem').write_bytes(public)
        (internal/'viewer/version.json').write_text(json.dumps(dict(version=f'90.0.{build-90000}',build=build)))
        path=app/'Contents/Info.plist';info=plistlib.loads(path.read_bytes())
        info.update(CFBundleVersion=str(build),CFBundleShortVersionString=f'90.0.{build-90000}')
        path.write_bytes(plistlib.dumps(info))
        subprocess.run(['/usr/bin/codesign','--force','--deep','--sign','-',str(app)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    configure(source,90002)
    feed=folder/'feed';feed.mkdir();archive=feed/'package.zip';package(source,archive)
    entry=dict(url=REPO+'/releases/download/v90.0.2/package.zip',size=archive.stat().st_size,sha256=hashlib.file_digest(archive.open('rb'),'sha256').hexdigest())
    manifest=dict(schema=1,version='90.0.2',build=90002,notes='Fault fixture',platforms={'macos-arm64':entry})
    def publish(value=manifest):
        raw=json.dumps(value).encode()
        envelope=dict(payload=base64.b64encode(raw).decode(),signature=base64.b64encode(key.sign(raw,padding.PKCS1v15(),hashes.SHA256())).decode())
        (feed/'updates.json').write_text(json.dumps(envelope))
        (feed/'latest').write_text(json.dumps(dict(tag_name='v'+value['version'],assets=[dict(name='updates.json',browser_download_url=REPO+'/releases/download/v'+value['version']+'/updates.json')])))
    publish()
    gate=threading.Event();mode={'value':'normal'}
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            if self.path=='/package.zip' and mode['value']=='network':
                self.send_error(503);return
            if self.path=='/package.zip' and mode['value']=='corrupt':
                self.send_response(200);self.end_headers();self.wfile.write(b'corrupt');return
            if self.path=='/package.zip' and mode['value']=='slow':
                self.send_response(200);self.end_headers()
                with archive.open('rb') as stream:
                    self.wfile.write(stream.read(2*1024*1024));self.wfile.flush();gate.wait(10)
                    try: shutil.copyfileobj(stream,self.wfile)
                    except (BrokenPipeError,ConnectionResetError):pass
                return
            super().do_GET()
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,directory=str(feed)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    class LocalSession(requests.Session):
        def get(self,url,**kwargs):
            suffix='latest' if url==LATEST else url.rsplit('/',1)[-1]
            return super().get(f'http://127.0.0.1:{server.server_port}/{suffix}',**kwargs)
    results=[];targets=[]
    def fixture(name):
        base=folder/name;base.mkdir();target=base/'塔式镜场设计与优化.app'
        shutil.copytree(source,target,symlinks=True);configure(target,90001);targets.append(target)
        user=base/'user-data';user.mkdir();(user/'settings.json').write_text('{"preserve":true}');(user/'mirrors.csv').write_text('id,e,n,z\nuser,1,2,3\n')
        os.environ.update(HELIOSTAT_APP_PATH=str(target),HELIOSTAT_APP_PID='99999999',HELIOSTAT_VIEWER_DATA=str(user))
        manager=UpdateManager(target/'Contents/Resources/server/_internal');manager.http=LocalSession()
        return manager,target,user
    def preserved(target,user):
        assert macos_update.identity(target)=='90001'
        subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(target)],check=True)
        assert (user/'settings.json').read_text()=='{"preserve":true}'
        assert (user/'mirrors.csv').read_text()=='id,e,n,z\nuser,1,2,3\n'
    def passed(name,details):
        results.append(dict(name=name,passed=True,method=details));print('PASS '+name,flush=True)
    def ready(manager):
        manager._check();assert manager.status()['state']=='available',manager.status()
        manager._download();assert manager.status()['state']=='ready',manager.status()
        shutil.copytree(manager.target/'Contents/Resources/server',manager.stage/'helper-runtime',symlinks=True)
        (manager.stage/'authorization.json').write_text(json.dumps(manager.envelope))
        state=macos_update.prepare(manager.target,manager.stage,90002,99999999)
        recovery=state.parent/'recover.command'
        import shlex
        recovery.write_text('#!/bin/bash\nset -e\n'+shlex.join([str(manager.stage/'helper-runtime/heliostat-viewer-server'),'--macos-recover',str(state)])+'\n');recovery.chmod(0o700)
        return state
    try:
        for name in ['network','corrupt']:
            manager,target,user=fixture(name);mode['value']=name
            manager._check();manager._download();assert manager.status()['state']=='error',manager.status();preserved(target,user)
            passed(name,'real HTTP failure or damaged response; real old bundle codesign remains valid')
        mode['value']='normal'
        manager,target,user=fixture('signature')
        bad=json.loads((feed/'updates.json').read_text());bad['signature']=base64.b64encode(b'bad-signature').decode();(feed/'updates.json').write_text(json.dumps(bad))
        manager._check();assert manager.status()['state']=='error';preserved(target,user);publish()
        passed('signature','real RSA verification rejects mismatched signature')
        manager,target,user=fixture('current')
        current=dict(manifest,version='90.0.1',build=90001,platforms={'macos-arm64':dict(entry,url=REPO+'/releases/download/v90.0.1/package.zip')})
        publish(current);manager._check();assert manager.status()['state']=='current';preserved(target,user);publish()
        passed('current','signed feed has no newer compatible build')
        manager,target,user=fixture('cancel');mode['value']='slow';manager._check();manager.start('download')
        wait(lambda:manager.status().get('progress',0)>0,10);worker=manager.worker;manager.start('cancel');gate.set();worker.join(15)
        assert manager.status()['state']=='cancelled' and manager.stage is None;preserved(target,user);mode['value']='normal'
        passed('cancel','cancel a live loopback desktop ZIP stream')
        manager,target,user=fixture('replace');state=ready(manager)
        original=macos_update.subprocess.run;flagged=[]
        def command(args,**kwargs):
            result=original(args,**kwargs)
            if args[0]=='/usr/bin/codesign' and '--verify' in args and '.incoming-' in args[-1]:
                original(['/usr/bin/chflags','uchg',args[-1]],check=True);flagged.append(Path(args[-1]))
            return result
        macos_update.subprocess.run=command
        try:
            try:macos_update.run(state,launch=lambda _:None)
            except PermissionError:pass
            else:raise AssertionError('Immutable incoming app did not prevent replacement')
        finally:
            macos_update.subprocess.run=original
            for p in flagged:original(['/usr/bin/chflags','nouchg',str(p)],check=True)
        preserved(target,user);assert not Path(str(target)+'.previous').exists()
        passed('replace','real macOS immutable flag rejects incoming rename after old app backup; old app restored')
        tampered=folder/'tampered/塔式镜场设计与优化.app';tampered.parent.mkdir();shutil.copytree(source,tampered,symlinks=True)
        version_file=tampered/'Contents/Resources/server/_internal/viewer/version.json'
        version_file.write_text(version_file.read_text()+'\n')
        good=feed/'good.zip';archive.rename(good);package(tampered,archive)
        tampered_manifest=dict(manifest,platforms={'macos-arm64':dict(entry,size=archive.stat().st_size,sha256=hashlib.file_digest(archive.open('rb'),'sha256').hexdigest())});publish(tampered_manifest)
        manager,target,user=fixture('codesign');manager._check();manager._download()
        assert manager.status()['state']=='error';preserved(target,user)
        archive.unlink();good.rename(archive);publish()
        passed('codesign','authenticated archive with an altered code-signed resource is rejected by real codesign')
        # Actual installed application intentionally exits before initializing.
        failed=folder/'failed/塔式镜场设计与优化.app';failed.parent.mkdir();shutil.copytree(source,failed,symlinks=True)
        swift=folder/'exit.swift';swift.write_text('import Foundation\nexit(42)\n')
        subprocess.run(['xcrun','swiftc',str(swift),'-o',str(failed/'Contents/MacOS/塔式镜场设计与优化')],check=True)
        subprocess.run(['/usr/bin/codesign','--force','--deep','--sign','-',str(failed)],check=True,stderr=subprocess.DEVNULL)
        good=feed/'good.zip';archive.rename(good);package(failed,archive)
        failure_manifest=dict(manifest,platforms={'macos-arm64':dict(entry,size=archive.stat().st_size,sha256=hashlib.file_digest(archive.open('rb'),'sha256').hexdigest())});publish(failure_manifest)
        manager,target,user=fixture('startup');state=ready(manager)
        try:macos_update.run(state,timeout=3)
        except TimeoutError:pass
        else:raise AssertionError('Failed native startup was incorrectly confirmed')
        assert Path(str(target)+'.previous').exists() and manager.stage.exists()
        subprocess.run(['/bin/bash',str(state.parent/'recover.command')],check=True)
        preserved(target,user);wait(lambda:not state.exists(),120);stop_tree(target)
        archive.unlink();good.rename(archive);publish()
        passed('startup','real signed Mach-O exits 42; backup retained; generated recovery command restores old native app')
        broken=folder/'broken/塔式镜场设计与优化.app';broken.parent.mkdir();shutil.copytree(source,broken,symlinks=True)
        (broken/'Contents/Resources/server/_internal/data/processed/gemasolar_layout.csv').write_text('invalid,column\nfoo,bar\n')
        subprocess.run(['/usr/bin/codesign','--force','--deep','--sign','-',str(broken)],check=True,stderr=subprocess.DEVNULL)
        archive.rename(good);package(broken,archive)
        broken_manifest=dict(manifest,platforms={'macos-arm64':dict(entry,size=archive.stat().st_size,sha256=hashlib.file_digest(archive.open('rb'),'sha256').hexdigest())});publish(broken_manifest)
        manager,target,user=fixture('critical-init');state=ready(manager)
        try:macos_update.run(state,timeout=6)
        except TimeoutError:pass
        else:raise AssertionError('Broken critical initialization was incorrectly confirmed')
        assert Path(str(target)+'.previous').exists() and manager.stage.exists()
        stop_tree(target)
        subprocess.run(['/bin/bash',str(state.parent/'recover.command')],check=True)
        preserved(target,user);wait(lambda:not state.exists(),120);stop_tree(target)
        archive.unlink();good.rename(archive);publish()
        passed('critical-init','real native app and service launch with invalid mirror data; failed main initialization retains backup and recovery works')
        manager,target,user=fixture('interruption');state=ready(manager)
        data=json.loads(state.read_text());data['phase']='replacing';macos_update.save(state,data)
        target.rename(Path(str(target)+'.previous'))
        subprocess.run(['/bin/bash',str(state.parent/'recover.command')],check=True)
        preserved(target,user);wait(lambda:not state.exists(),120);stop_tree(target)
        passed('interruption','checkpoint injection after actual old-to-backup rename; generated recovery command survives absence of app path')
        manager,target,user=fixture('cleanup');state=ready(manager)
        backup=Path(str(target)+'.previous')
        def launch(app):
            (backup/'Contents').chmod(0o555)
            original(['/usr/bin/open','-n','--env','HELIOSTAT_VIEWER_DATA='+str(user),str(app)],check=True)
        try:macos_update.run(state,launch=launch,timeout=120)
        except PermissionError:pass
        else:raise AssertionError('Read-only backup did not produce cleanup failure')
        assert json.loads(state.read_text())['phase']=='healthy' and (state.parent/'errors.log').exists()
        if (backup/'Contents').exists():(backup/'Contents').chmod(0o755)
        macos_update.healthy(state)
        assert not state.exists() and not manager.stage.exists() and not backup.exists()
        assert (user/'settings.json').read_text()=='{"preserve":true}'
        assert (user/'mirrors.csv').read_text()=='id,e,n,z\nuser,1,2,3\n';stop_tree(target)
        passed('cleanup','real read-only directory causes partial deletion; inode-checked retry cleans backup after permissions restored')
    finally:
        (folder/'result.json').write_text(json.dumps(dict(results=results,folder=str(folder)),ensure_ascii=False,indent=2))
        for target in targets:stop_tree(target)
        server.shutdown();server.server_close()
    print(json.dumps(dict(results=results,folder=str(folder)),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
