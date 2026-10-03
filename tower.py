"""Validated tower/receiver definitions with legacy single-tower compatibility."""

from dataclasses import dataclass
from collections.abc import Mapping, Sequence

import numpy as np

from receiver import CylindricalReceiver
from transform import vector3


@dataclass(frozen=True)
class SolarTower:
    """One tower and its receiver in the field ENU coordinate system."""

    tower_id: str
    name: str
    base: tuple[float, float, float]
    receiver: CylindricalReceiver

    def __post_init__(self) -> None:
        tower_id = str(self.tower_id).strip()
        name = str(self.name).strip()
        if not tower_id:
            raise ValueError('tower_id must be a nonempty string')
        if not name:
            raise ValueError('Tower name must be a nonempty string')
        base = vector3(self.base, 'tower base')
        if self.receiver.centre[2] <= base[2]:
            raise ValueError('Receiver centre must be above the tower base')
        object.__setattr__(self, 'tower_id', tower_id)
        object.__setattr__(self, 'name', name)
        object.__setattr__(self, 'base', tuple(base))

    def as_dict(self) -> dict:
        return {
            'id': self.tower_id,
            'name': self.name,
            'base': list(self.base),
            'receiver': {
                'centre': list(self.receiver.centre),
                'radius': self.receiver.radius,
                'height': self.receiver.height,
            },
        }


def towers_from_config(config: Mapping) -> tuple[SolarTower, ...]:
    """Read the v1.1 ``towers`` array or upgrade legacy scalar fields in memory."""
    raw_towers: Sequence[Mapping]
    if config.get('towers'):
        raw_towers = config['towers']
    else:
        raw_towers = ({
            'id': 'tower-1',
            'name': '主塔',
            'base': [0., 0., 0.],
            'receiver': {
                'centre': [0., 0., float(config['optical_height_m'])],
                'radius': float(config['receiver_radius_m']),
                'height': float(config['receiver_height_m']),
            },
        },)
    towers = []
    for item in raw_towers:
        receiver = item.get('receiver', {})
        towers.append(SolarTower(
            tower_id=item['id'],
            name=item.get('name', item['id']),
            base=tuple(item.get('base', (0., 0., 0.))),
            receiver=CylindricalReceiver(
                centre=tuple(receiver['centre']),
                radius=float(receiver['radius']),
                height=float(receiver['height']),
            ),
        ))
    if not towers or len({tower.tower_id for tower in towers}) != len(towers):
        raise ValueError('Plant must contain towers with unique IDs')
    return tuple(towers)


def config_with_towers(config: Mapping) -> dict:
    """Return a JSON-ready v1.1 config while retaining scalar compatibility keys."""
    result = dict(config)
    towers = towers_from_config(result)
    result['schema_version'] = '1.1'
    result['towers'] = [tower.as_dict() for tower in towers]
    primary = towers[0]
    result['optical_height_m'] = float(primary.receiver.centre[2] - primary.base[2])
    result['receiver_radius_m'] = float(primary.receiver.radius)
    result['receiver_height_m'] = float(primary.receiver.height)
    return result
