import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from viewer.workspace import ViewerWorkspace

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT/'data/power_tower_catalog.json').read_text())
ADDED = [p for p in CATALOG if p.get('data_checked_date') == '2026-10-04']


def test_expanded_catalog_has_unique_sources_and_explicit_estimates():
    assert len(ADDED) == 15
    assert len({p['id'] for p in CATALOG}) == len(CATALOG)
    for p in ADDED:
        ZoneInfo(p['timezone'])
        assert p['reported_heliostats'] > 0
        assert p['source_url'].startswith('https://')
        assert p['model_receiver_radius_m'] > 0
        assert p['receiver_geometry_status'] != 'published'
        assert '估值' in p['model_receiver_note']
        if p['tower_height_m'] is None:
            assert p['model_tower_height_m'] > 0
            assert '不作为实测塔高' in p['model_note']


@pytest.mark.parametrize('plant', ADDED, ids=lambda p:p['id'])
def test_added_plant_materializes_and_has_shared_hourly_environment(tmp_path, plant):
    workspace = ViewerWorkspace(ROOT, user_root=tmp_path)
    workspace.select(plant['id'])
    model = workspace.active
    assert len(model.mirrors) == plant['reported_heliostats']
    assert model.config['timezone'] == plant['timezone']
    assert len(model.weather) == 8760
    assert model.temperature_source == 'era5_land_reanalysis'
    assert np.isfinite(model.weather['temperature_c']).all()
    assert np.isfinite(np.array([m.centre for m in model.mirrors])).all()
    assert model.config['optical_height_m'] == (plant['tower_height_m'] or plant['model_tower_height_m'])
    metadata = workspace.metadata()
    assert metadata['plant_name'] == plant['name']
    assert metadata['plant_details']['source_url'] == plant['source_url']
    assert metadata['default_time'].startswith('2025-03-20')
