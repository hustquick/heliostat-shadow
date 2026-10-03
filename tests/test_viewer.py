"""Check viewer geometry independently of rendering, including holes and HTTP errors."""
from functools import partial
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor
from http.cookiejar import CookieJar
from urllib.request import urlopen

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Polygon, MultiPolygon

from scripts.serve_viewer import ViewerHandler, LocalViewerServer
from viewer.model import ROOT, ViewerModel, polygon_rings


@pytest.fixture(scope='module')
def model():
    return ViewerModel()


def area(parts):
    return sum(Polygon(p['outer'], p['holes']).area for p in parts)


def test_holes_and_disconnected_components_survive_serialization():
    a = Polygon([(0,0),(4,0),(4,4),(0,4)], holes=[[(1,1),(1,3),(3,3),(3,1)]])
    b = Polygon([(5,0),(6,0),(6,1),(5,1)])
    parts = polygon_rings(MultiPolygon([a,b]))
    assert len(parts) == 2
    assert len(parts[0]['holes']) == 1
    assert area(parts) == pytest.approx(13.)


@pytest.mark.parametrize('timestamp,ids',[
    ('2023-03-21T12:10:00+00:00', ['G261','G1','G2650']),
    ('2023-12-21T16:10:00+00:00', ['G640','G100']),
    ('2023-06-21T05:10:00+00:00', ['G1709']),
])
def test_views_match_saved_results_and_cosine_area(model,timestamp,ids):
    saved = pd.read_csv(ROOT/'reports/gemasolar_2023/per_mirror.csv.gz',
                        usecols=['time_utc','mirror_id','eta_shadow','eta_blocking','eta_joint'])
    saved = saved[saved.time_utc==timestamp].set_index('mirror_id')
    _, frame = model.frame(timestamp)
    for id in ids:
        target = model.target(timestamp,id)
        e = target['efficiencies']
        factors = ('eta_cosine','eta_joint','eta_atmosphere','eta_intercept','mirror_reflectivity','mirror_cleanliness')
        assert all(0 <= e[key] <= 1 for key in factors)
        assert e['eta_optical'] == pytest.approx(np.prod([e[key] for key in factors]))
        physical = target['views']['mirror']['areas']
        for name in ('eta_shadow','eta_blocking','eta_joint'):
            assert e[name] == pytest.approx(saved.loc[id,name],abs=1e-8)
        assert frame['eta_joint'][model.index[id]] == pytest.approx(e['eta_joint'])
        for viewname,view in target['views'].items():
            for name,parts in view['polygons'].items():
                assert area(parts) == pytest.approx(view['areas'][name],abs=1e-8)
            assert view['areas']['visible']/view['areas']['target'] == pytest.approx(e['eta_joint'])
            expected = physical['target'] if viewname=='mirror' else physical['target']*e['eta_cosine']
            assert view['areas']['target'] == pytest.approx(expected,abs=1e-7)
        assert sum(physical[k] for k in ('visible','shadow_only','blocking_only','overlap')) == pytest.approx(physical['target'])
        # The same 2D points lift into the true 3D mirror plane.
        basis=np.array(target['views']['mirror']['basis']);normal=np.array(target['normal'])
        pts=np.array(target['views']['mirror']['polygons']['target'][0]['outer']) @ basis
        np.testing.assert_allclose(pts@normal,0,atol=1e-10)
        for mode in target['modes'].values():
            assert {d['mirror_id'] for d in mode['decisions']} == set(mode['candidate_ids'])
            assert {d['mirror_id'] for d in mode['decisions'] if d['reason']=='overlap'} == set(mode['occluder_ids'])
    json.dumps(frame,allow_nan=False)
    json.dumps(target,allow_nan=False)


def test_night_has_no_invented_tracking_pose(model):
    prepared,frame=model.frame('2023-03-21T00:10:00+00:00')
    assert prepared is None
    assert not frame['daylight'] and not frame['vertices'] and frame['eta_joint'] is None
    assert len(frame['centres'])==2650
    assert model.target(frame['timestamp'],'G1')['daylight'] is False


@pytest.mark.parametrize('time',['2023-03-21','2022-01-01T00:10:00Z','NaT','bad'])
def test_bad_times_are_rejected(model,time):
    with pytest.raises(ValueError):model.frame(time)


def test_local_dst_times_are_unambiguous(model):
    times=pd.DatetimeIndex(model.metadata()['timestamps']).tz_convert('Europe/Madrid')
    assert sum(times.strftime('%Y-%m-%d')=='2023-03-26')==23
    assert sum(times.strftime('%Y-%m-%d')=='2023-10-29')==25
    assert len(set(model.metadata()['timestamps']))==8760
    with pytest.raises(ValueError):model.target('2023-03-21T12:10:00Z','invalid')


def test_metadata_describes_whole_field_heliostat_geometry(model):
    metadata = model.metadata()
    summary = metadata['heliostat_summary']
    assert summary == {
        'shape': 'rectangle',
        'shape_label': '矩形平面镜',
        'uniform_dimensions': True,
        'width_range_m': [12.305, 12.305],
        'height_range_m': [9.752, 9.752],
        'centre_elevation_range_m': [0., 0.],
        'ground_height_m': None,
        'ground_height_source_url': None,
        'ground_height_note': None,
        'reflective_area_m2': 115.7,
        'mount_types': ['azimuth_elevation'],
    }
    assert metadata['site_location'] == {
        'latitude': 37.5607,
        'longitude': -5.3316,
        'datum': 'WGS 84',
        'accuracy': 'published_project_site',
        'accuracy_label': '公开项目场址',
        'source_name': 'Global Energy Observatory',
        'source_url': 'https://globalenergyobservatory.org/geoid/43848',
        'note': ('Global Energy Observatory 公布的 Gemasolar 电厂场址坐标；'
                 '用于太阳位置计算，但不解释为塔中心测量控制点。'),
    }


def test_efficiency_coloring_returns_every_mirror_and_full_optical_chain(model):
    timestamp = '2023-03-21T12:10:00+00:00'
    payload = model.efficiencies(timestamp)
    assert payload['metric'] == 'eta_optical'
    assert payload['mirror_count'] == len(model.mirrors) == len(payload['values'])
    assert set(payload['factors']) == {
        'eta_cosine', 'eta_shadow', 'eta_blocking', 'eta_joint',
        'mirror_reflectivity', 'mirror_cleanliness', 'eta_atmosphere', 'eta_intercept'}
    assert all(len(values) == len(model.mirrors) for values in payload['factors'].values())
    assert 0 <= payload['minimum'] <= payload['mean'] <= payload['maximum'] <= 1
    saved = pd.read_csv(ROOT/'reports/gemasolar_2023/peak_snapshot.csv', nrows=1).iloc[0]
    expected = saved.receiver_incident_power_w / saved.incident_normal_power_w
    assert payload['values'][model.index[saved.mirror_id]] == pytest.approx(expected, abs=1e-10)
    assert '联合阴影遮挡' in payload['formula']


def test_server_routes_and_error_responses(model):
    server=LocalViewerServer(('127.0.0.1',0),partial(ViewerHandler,model=model))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        with urlopen(base+'/') as r:assert '塔式镜场设计与优化' in r.read().decode()
        with urlopen(base+'/api/meta') as r:assert len(json.load(r)['mirror_ids'])==2650
        with urlopen(base+'/api/health') as r:
            health=json.load(r);assert health['status']=='ok'
        with pytest.raises(HTTPError) as err:urlopen(base+'/api/frame?time=invalid')
        assert err.value.code==400
        assert 'error' in json.loads(err.value.read())
        with pytest.raises(HTTPError) as err:urlopen(base+'/api/missing')
        assert err.value.code==404
    finally:
        server.shutdown();server.server_close();thread.join()


def test_lan_access_token_becomes_http_only_cookie(model):
    server=LocalViewerServer(('127.0.0.1',0),partial(ViewerHandler,model=model))
    server.access_token='1234567890abcdef'
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        with pytest.raises(HTTPError) as error:urlopen(base+'/api/health')
        assert error.value.code==403
        opener=build_opener(HTTPCookieProcessor(CookieJar()))
        with opener.open(base+'/?token=1234567890abcdef') as response:
            assert '塔式镜场设计与优化' in response.read().decode()
        with opener.open(base+'/api/health') as response:
            assert json.load(response)['status']=='ok'
    finally:
        server.shutdown();server.server_close();thread.join()
