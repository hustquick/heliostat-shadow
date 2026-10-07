import json
import sys
from scripts import publish_release as release


def fixture(monkeypatch,tmp_path,heads,draft=False):
    (tmp_path/'VERSION').write_text('1.0.7')
    (tmp_path/'BUILD_NUMBER').write_text('20008')
    (tmp_path/'RELEASE_NOTES.md').write_text('current notes')
    monkeypatch.setattr(release,'ROOT',tmp_path)
    monkeypatch.setattr(sys,'argv',['publish',str(tmp_path/'assets')])
    (tmp_path/'assets').mkdir()
    monkeypatch.setenv('GITHUB_SHA','current')
    monkeypatch.setenv('UPDATE_SIGNING_KEY','test placeholder')
    monkeypatch.setattr(release,'make_manifest',lambda *a:{'payload':'test','signature':'test'})
    calls=[]
    def gh(*args):
        calls.append(args)
        if args[0]=='api' and 'git/ref' in args[1]: return json.dumps({'object':{'sha':next(heads)}})
        if args[0]=='api': return json.dumps([{'tag_name':'v1.0.7','draft':True}] if draft else [])
        return ''
    monkeypatch.setattr(release,'gh',gh)
    return calls


def test_superseded_build_never_publishes(monkeypatch,tmp_path):
    calls=fixture(monkeypatch,tmp_path,iter(['newer']))
    release.main()
    assert not any(c[0]=='release' for c in calls)


def test_source_changed_during_upload_keeps_draft(monkeypatch,tmp_path):
    calls=fixture(monkeypatch,tmp_path,iter(['current','newer']))
    release.main()
    assert any(c[:2]==('release','upload') for c in calls)
    assert not any('--draft=false' in c for c in calls)


def test_retry_updates_draft_source_and_notes(monkeypatch,tmp_path):
    calls=fixture(monkeypatch,tmp_path,iter(['current','current']),draft=True)
    release.main()
    assert not any(c[:2]==('release','create') for c in calls)
    assert any('--target' in c and 'current' in c and '--notes-file' in c for c in calls)
    assert any('--draft=false' in c for c in calls)
