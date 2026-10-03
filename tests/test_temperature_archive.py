import json
import numpy as np
from viewer.model import ROOT
from viewer.workspace import ViewerWorkspace


def test_archive_covers_every_catalog_plant_with_hourly_temperatures():
    catalog = json.loads((ROOT/'data/power_tower_catalog.json').read_text())
    archive = json.loads((ROOT/'data/temperature_archive.json').read_text())
    entries = {entry['plant_id']: entry for entry in archive['plants']}
    assert set(entries) == {plant['id'] for plant in catalog}
    for plant in catalog:
        entry = entries[plant['id']]
        values = np.asarray(entry['temperature_series_c'])
        assert entry['year'] == 2025
        assert entry['latitude'] == plant['latitude']
        assert entry['longitude'] == plant['longitude']
        assert len(values) == 8760 and np.isfinite(values).all()
        assert values.min() > -90 and values.max() < 65
        assert np.ptp(values) > 10


def test_catalog_model_uses_timestamp_aligned_archive(tmp_path):
    workspace = ViewerWorkspace(ROOT, tmp_path)
    workspace.select('ps10')
    model = workspace.active
    archive = json.loads((ROOT/'data/temperature_archive.json').read_text())
    entry = next(entry for entry in archive['plants'] if entry['plant_id'] == 'ps10')
    assert model.temperature_source == 'era5_land_reanalysis'
    assert model.metadata()['temperature_source'] == 'era5_land_reanalysis'
    np.testing.assert_array_equal(model.weather.temperature_c, entry['temperature_series_c'])
    _, frame = model.frame(model.weather.index[0].isoformat())
    assert frame['temperature'] == entry['temperature_series_c'][0]
    assert frame['temperature_source'] == 'era5_land_reanalysis'
