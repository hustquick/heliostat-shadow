import json

import numpy as np
import pytest

from viewer.model import ROOT
from viewer.workspace import ViewerWorkspace, default_user_root


def test_default_user_root_can_be_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv('HELIOSTAT_VIEWER_DATA', str(tmp_path))
    assert default_user_root() == tmp_path


def test_catalog_selection_import_and_rearrangement(tmp_path):
    workspace = ViewerWorkspace(ROOT, tmp_path)
    initial = workspace.metadata()
    assert initial['active_plant'] == 'gemasolar'
    assert {'ps10', 'crescent-dunes', 'khi-solar-one', 'cerro-dominador', 'noor-iii',
            'shouhang-dunhuang-ii', 'powerchina-qinghai-gonghe', 'luneng-haixi',
            'supcon-delingha-50', 'ceec-hami', 'yumen-xinneng-beam-down'} <= {
        item['id'] for item in initial['plants']}
    assert len(initial['plants']) >= 15
    assert [p['id'] for p in initial['plants'] if p['id'].startswith('gemasolar')] == ['gemasolar']

    ps10 = workspace.select('ps10')
    assert ViewerWorkspace(ROOT, tmp_path).active_id == 'ps10'
    assert ps10['plant_name'].startswith('PS10')
    assert len(ps10['mirror_ids']) == 624
    assert ps10['reported_mirrors'] == 624
    assert ps10['layout_status'] == '参数化重建场，非实测坐标'
    assert ps10['site_location']['latitude'] == 37.44226
    assert ps10['site_location']['longitude'] == -6.25006
    assert ps10['site_location']['accuracy'] == 'published_project_site'

    shouhang = workspace.select('shouhang-dunhuang-ii')
    assert len(shouhang['mirror_ids']) == 12121
    assert shouhang['receiver'] == {'centre': [0.0, 0.0, 263.0], 'radius': 9.6, 'height': 40.0}
    assert shouhang['tower_count'] == 1
    assert shouhang['towers'][0]['id'] == 'tower-1'
    assert shouhang['towers'][0]['assigned_mirrors'] == 12121
    shouhang_config = json.loads(
        (tmp_path/'catalog-cache/shouhang-dunhuang-ii/config.json').read_text())
    assert shouhang_config['mirror_width_m'] == 10.8
    assert shouhang_config['mirror_height_m'] == 10.8
    assert shouhang_config['heliostat_pedestal_height_m'] == 5.5
    assert shouhang_config['expected_mirrors'] == 12121
    assert shouhang_config['catalog_model_version'] == 4
    assert shouhang_config['schema_version'] == '1.1'
    assert shouhang['heliostat_summary']['ground_height_m'] == 5.5
    assert 'Height of pedestal = 5.5 m' in shouhang['heliostat_summary']['ground_height_note']
    assert shouhang['site_location']['latitude'] == 40.062
    assert shouhang['site_location']['longitude'] == 94.425
    assert shouhang['site_location']['accuracy'] == 'project_site_approximate'
    shouhang_layout = np.genfromtxt(
        tmp_path/'catalog-cache/shouhang-dunhuang-ii/layout.csv', delimiter=',',
        names=True, usecols=('x', 'y'))
    assert np.hypot(shouhang_layout['x'], shouhang_layout['y']).max() == pytest.approx(1500)
    assert len(np.unique(np.round(
        np.hypot(shouhang_layout['x'], shouhang_layout['y']), 6))) == 78

    gonghe = workspace.select('powerchina-qinghai-gonghe')
    assert len(gonghe['mirror_ids']) == 30016
    assert gonghe['reported_mirrors'] == 30016
    gonghe_layout = np.genfromtxt(
        tmp_path/'catalog-cache/powerchina-qinghai-gonghe/layout.csv', delimiter=',',
        names=True, usecols=('x', 'y', 'width', 'height'))
    gonghe_radii = np.hypot(gonghe_layout['x'], gonghe_layout['y'])
    assert 800 < gonghe_radii.max() <= 823.4
    assert gonghe_layout['width'][0] == pytest.approx(5.8)
    assert gonghe_layout['height'][0] == pytest.approx(3.5)
    gonghe_efficiencies = workspace.efficiencies('2025-03-21T04:00:00+00:00')
    assert gonghe_efficiencies['minimum'] > .20
    assert gonghe_efficiencies['mean'] > .45

    luneng = workspace.select('luneng-haixi')
    assert len(luneng['mirror_ids']) == 4400
    assert luneng['reported_mirrors'] == 4400
    assert not luneng['field_efficiencies_deferred']

    delingha = workspace.select('supcon-delingha-50')
    assert len(delingha['mirror_ids']) == 27135
    assert delingha['reported_mirrors'] == 27135
    assert delingha['field_efficiencies_deferred']
    assert delingha['efficiency_sample_count'] == 0
    assert delingha['default_time'].startswith('2025-03-20T06:00:00')

    dual = workspace.select('ctg-hengji-guazhou-dual-tower')
    assert len(dual['mirror_ids']) == 26944
    assert dual['tower_count'] == 2
    assert dual['flexible_mirrors'] > 0
    assert dual['tower_assignment_policy'] == 'best_optical_at_time'
    assert {tower['id'] for tower in dual['towers']} == {'east', 'west'}
    model = workspace.active
    flexible = [i for i, mirror in enumerate(model.mirrors) if mirror.tower_id == 'auto']
    assert flexible
    model.config['tower_assignment_strategy'] = 'independent'
    morning = model._mirrors_for_sun(np.array([-.7, .2, .68]))
    for i in flexible:
        scores = model._last_tower_choice_scores[model.mirrors[i].mirror_id]
        assert scores[morning[i].tower_id] == max(scores.values())
    afternoon = model._mirrors_for_sun(np.array([.7, .2, .68]))
    assert any(morning[i].tower_id != afternoon[i].tower_id for i in flexible)

    angles = np.linspace(0, 2*np.pi, 60, endpoint=False)
    csv = 'x,y\n' + '\n'.join(f'{150*np.sin(a)},{150*np.cos(a)}' for a in angles)
    imported = workspace.import_csv(dict(
        name='测试镜场', csv=csv, latitude=35, longitude=105, timezone='Asia/Shanghai',
        altitude_m=1000, optical_height_m=120, receiver_radius_m=4,
        receiver_height_m=10, mirror_width_m=10, mirror_height_m=8,
    ))
    assert imported['plant_name'] == '测试镜场'
    assert len(imported['mirror_ids']) == 60
    active = (tmp_path/'active.txt').read_text()
    assert active == imported['active_plant']

    source_weather = workspace.active.weather.copy()
    source_temperature = workspace.active.temperature_source
    rearranged = workspace.rearrange(dict(scheme='campo', separation_m=1, dr2=.9, dr3=1.8))
    assert len(rearranged['mirror_ids']) == 60
    assert 'Campo重排' in rearranged['plant_name']
    config = json.loads((tmp_path/rearranged['active_plant']/'config.json').read_text())
    assert config['design_method'] == 'campo'
    assert config['source_plant_id'] == imported['active_plant']
    assert workspace.active.temperature_source == source_temperature
    assert np.allclose(workspace.active.weather.temperature_c, source_weather.temperature_c)
    restored = ViewerWorkspace(ROOT, tmp_path)
    assert restored.active.config['design_method'] == 'campo'
    assert np.allclose(restored.active.weather.temperature_c, source_weather.temperature_c)
    assert config['latitude'] == 35
    assert config['coordinate_accuracy'] == 'user_supplied'
    assert config['coordinate_source_url'] is None


def test_catalog_coordinates_have_provenance_and_valid_ranges():
    catalog = json.loads((ROOT/'data/power_tower_catalog.json').read_text())
    assert len(catalog) >= 29
    for plant in catalog:
        assert -90 <= plant['latitude'] <= 90
        assert -180 <= plant['longitude'] <= 180
        assert plant['coordinate_datum'] == 'WGS 84'
        assert plant['coordinate_accuracy'] in {
            'exact_project_site', 'published_project_site',
            'project_site_coordinate', 'project_site_approximate',
            'regional_location_approximate'}
        assert plant['coordinate_source_name']
        assert plant['coordinate_source_url'].startswith('https://')
        assert plant['coordinate_note']

    by_id = {plant['id']: plant for plant in catalog}
    exact = {
        'crescent-dunes': (38.2389, -117.3636),
        'khi-solar-one': (-28.5364, 21.0782),
        'cerro-dominador': (-22.7707, -69.4792),
        'noor-iii': (31.0623, -6.8704),
    }
    for plant_id, (latitude, longitude) in exact.items():
        assert by_id[plant_id]['latitude'] == latitude
        assert by_id[plant_id]['longitude'] == longitude
        assert by_id[plant_id]['coordinate_accuracy'] == 'exact_project_site'


def test_viewer_html_exposes_workspace_controls():
    from desktop.updates import version_tuple
    version = (ROOT/'VERSION').read_text().strip()
    version_tuple(version)
    assert json.loads((ROOT/'viewer/version.json').read_text()) == {
        'version': version, 'build': int((ROOT/'BUILD_NUMBER').read_text())}
    html = (ROOT/'viewer/index.html').read_text()
    for element_id in ('plant', 'importPlant', 'rearrange', 'efficiencyColor', 'towerColor',
                       'efficiencyScale', 'viewIso', 'viewTop', 'importDialog', 'designMethod', 'currentLayout'):
        assert f'id="{element_id}"' in html
    app = (ROOT/'viewer/app.js').read_text()
    assert '中心坐标 x=${centre[0].toFixed(3)} m, y=${centre[1].toFixed(3)} m\\n' in app
    assert '总效率 ${(eta * 100).toFixed(2)}%（' in app


def test_crescent_dunes_published_receiver_and_extent(tmp_path):
    workspace = ViewerWorkspace(ROOT, tmp_path)
    workspace.select('crescent-dunes')
    model = workspace.active
    assert model.config['receiver_radius_m'] == 7.9
    assert model.config['receiver_height_m'] == 35.
    assert max(np.hypot(*m.centre[:2]) for m in model.mirrors) == pytest.approx(1620.)
    result = workspace.efficiencies('2025-03-21T19:00:00+00:00')
    i = int(np.argmin(result['values']))
    product = np.prod([result['factors'][k][i] for k in
        ('eta_cosine', 'eta_joint', 'eta_atmosphere', 'eta_intercept',
         'mirror_reflectivity', 'mirror_cleanliness')])
    assert result['minimum'] == pytest.approx(product)


def test_every_catalog_receiver_has_explicit_audit_status():
    catalog = json.loads((ROOT/'data/power_tower_catalog.json').read_text())
    assert len(catalog) >= 29
    for plant in catalog:
        assert plant['receiver_geometry_status'] in {
            'published', 'unverified-estimate', 'unsupported-geometry'}
        assert plant['receiver_audit_sources']
        assert plant['model_receiver_note']
    by_id = {p['id']: p for p in catalog}
    for key, radius, height in [
        ('noor-iii',10.,22.), ('cerro-dominador',10.,18.4),
        ('powerchina-qinghai-gonghe',6.45,14.2),
        ('supcon-delingha-50',6.07,15.03), ('ceec-hami',7.075,15.69)]:
        assert by_id[key]['model_receiver_radius_m'] == radius
        assert by_id[key]['model_receiver_height_m'] == height
    for key in ['ps10', 'khi-solar-one', 'yumen-xinneng-beam-down']:
        assert by_id[key]['receiver_geometry_status'] == 'unsupported-geometry'


def test_rearranged_reference_keeps_historical_weather_after_reload(tmp_path):
    workspace = ViewerWorkspace(ROOT, tmp_path)
    source = workspace.active
    weather = source.weather.copy()
    expected = {key: source.config[key] for key in ('latitude', 'longitude', 'timezone', 'year')}
    workspace.rearrange(dict(scheme='campo', separation_m=1, dr2=.9, dr3=1.8))
    restored = ViewerWorkspace(ROOT, tmp_path)
    assert restored.metadata()['environment_plant_id'] == 'gemasolar'
    assert restored.active.weather is restored._model_for('gemasolar').weather
    assert not restored.active.clear_sky
    assert restored.active.config['source_plant_id'] == 'gemasolar'
    assert not (tmp_path/restored.active_id/'weather.csv').exists()
    assert restored._plants[restored.active_id]['weather'] == restored._plants['gemasolar']['weather']
    assert {key: restored.active.config[key] for key in expected} == expected
    assert restored.active.weather.index.equals(weather.index)
    assert np.allclose(restored.active.weather.dni_w_m2, weather.dni_w_m2)
    assert np.allclose(restored.active.weather.temperature_c, weather.temperature_c)


def test_legacy_rearrangement_resolves_source_environment(tmp_path):
    workspace = ViewerWorkspace(ROOT, tmp_path)
    workspace.rearrange(dict(scheme='campo', separation_m=1, dr2=.9, dr3=1.8))
    path = tmp_path/workspace.active_id/'config.json'
    config = json.loads(path.read_text())
    config.pop('source_plant_id')
    path.write_text(json.dumps(config))
    restored = ViewerWorkspace(ROOT, tmp_path)
    assert restored.active.weather is restored._model_for('gemasolar').weather


def test_generate_and_import_use_explicit_top_plant_not_active_design(tmp_path):
    workspace = ViewerWorkspace(ROOT, tmp_path)
    workspace.select('ps10')
    original = workspace.active
    imported = workspace.import_csv(dict(plant_id='ps10', name='PS10 导入方案',
        csv='x,y\n50,0\n45,20\n25,45\n-25,45\n-45,20\n-50,0\n'))
    assert len(imported['mirror_ids']) == 6
    assert imported['environment_plant_id'] == 'ps10'
    assert workspace.active.weather is original.weather
    assert workspace.active.config['latitude'] == original.config['latitude']
    assert workspace.active.config['timezone'] == original.config['timezone']
    assert workspace.active.config['mirror_width_m'] == original.config['mirror_width_m']
    generated = workspace.rearrange(dict(plant_id='ps10', scheme='campo'))
    assert len(generated['mirror_ids']) == 624
    assert generated['environment_plant_id'] == 'ps10'
    assert workspace.active.weather is original.weather
    assert all(p['environment_plant_id'] == 'ps10' for p in generated['plants'] if not p['builtin'])
    active_id = workspace.active_id
    with pytest.raises(ValueError, match='未知来源电厂'):
        workspace.rearrange(dict(plant_id='unknown', scheme='campo'))
    assert workspace.active_id == active_id
