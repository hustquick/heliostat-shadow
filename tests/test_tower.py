import numpy as np
import pytest

from heliostat import Heliostat
from simulation import PreparedField
from tower import config_with_towers, towers_from_config


def test_legacy_config_upgrades_to_one_tower():
    config = config_with_towers({
        'optical_height_m': 140,
        'receiver_radius_m': 4,
        'receiver_height_m': 10.5,
    })
    assert config['schema_version'] == '1.1'
    assert config['towers'][0]['id'] == 'tower-1'
    assert config['towers'][0]['receiver']['centre'] == [0.0, 0.0, 140.0]


def test_multi_tower_config_and_per_mirror_receiver_assignment():
    config = {
        'towers': [
            {'id': 'east', 'name': '东塔', 'base': [100, 0, 0],
             'receiver': {'centre': [100, 0, 100], 'radius': 4, 'height': 10}},
            {'id': 'west', 'name': '西塔', 'base': [-100, 0, 0],
             'receiver': {'centre': [-100, 0, 100], 'radius': 4, 'height': 10}},
        ]
    }
    towers = towers_from_config(config)
    mirrors = [
        Heliostat('E1', (180, 0, 0), 2, 2, (104, 0, 100), tower_id='east'),
        Heliostat('W1', (-180, 0, 0), 2, 2, (-104, 0, 100), tower_id='west'),
    ]
    field = PreparedField.from_mirrors(mirrors, np.array([0., 0., 1.]))
    result = field.evaluate(800, receivers={tower.tower_id: tower.receiver for tower in towers})
    assert result.tower_id.tolist() == ['east', 'west']
    assert np.isfinite(result.eta_intercept).all()
    assert result.eta_intercept.iloc[0] == pytest.approx(result.eta_intercept.iloc[1])


def test_multi_tower_receiver_mapping_rejects_unassigned_tower():
    mirror = Heliostat('E1', (180, 0, 0), 2, 2, (104, 0, 100), tower_id='east')
    field = PreparedField.from_mirrors([mirror], [0, 0, 1])
    with pytest.raises(ValueError, match='east'):
        field.evaluate(800, receivers={})
