"""Validated adapters for public Gemasolar research layouts and PVGIS history."""

import calendar
import json
from pathlib import Path

import numpy as np
import pandas as pd


def read_pvgis_dni(path: str | Path, expected_year: int) -> tuple[pd.DataFrame, dict]:
    """Read one actual calendar year, not a TMY or a clear-sky simulation.

    Two-axis tracking is mandatory: its beam component Gb(i) is normal to the
    rays and equals DNI. Gd(i) is diffuse on that tracking plane, NOT DHI.
    Preserve PVGIS's :10 timestamps; do not round or apply local-time shifts.
    """
    raw = json.loads(Path(path).read_text())
    inputs = raw['inputs']
    if 'two_axis' not in inputs['mounting_system']:
        raise ValueError('DNI extraction requires two-axis tracking data')
    if inputs['meteo_data']['radiation_db'] != 'PVGIS-SARAH3':
        raise ValueError('Expected PVGIS-SARAH3 satellite history')
    if inputs['meteo_data']['year_min'] != expected_year or inputs['meteo_data']['year_max'] != expected_year:
        raise ValueError('Requested and returned calendar years differ')
    units = raw['meta']['outputs']['hourly']['variables']['Gb(i)']['units']
    if units != 'W/m2':
        raise ValueError(f'Unexpected irradiance units: {units}')
    data = pd.DataFrame(raw['outputs']['hourly'])
    required = ['time', 'Gb(i)', 'Gd(i)', 'Gr(i)', 'H_sun', 'T2m', 'WS10m', 'Int']
    if not set(required).issubset(data.columns):
        raise ValueError('Missing PVGIS hourly columns')
    times = pd.DatetimeIndex(pd.to_datetime(data.pop('time'), format='%Y%m%d:%H%M', utc=True))
    expected_count = 8784 if calendar.isleap(expected_year) else 8760
    if len(times) != expected_count or times.has_duplicates or not times.is_monotonic_increasing:
        raise ValueError('Incomplete, duplicated or unordered hourly history')
    expected_times = pd.date_range(f'{expected_year}-01-01', periods=expected_count, freq='h', tz='UTC')
    expected_times += pd.Timedelta(minutes=times[0].minute)
    if not times.equals(expected_times):
        raise ValueError('Calendar coverage or hourly cadence is inconsistent')
    if not np.isfinite(data[required[1:]].to_numpy(dtype=float)).all():
        raise ValueError('Non-finite PVGIS observations')
    if (data[['Gb(i)', 'Gd(i)', 'Gr(i)', 'WS10m']] < 0).any().any():
        raise ValueError('Negative irradiance or wind speed')
    if not data['Int'].isin([0, 1]).all():
        raise ValueError('Unknown PVGIS reconstruction flags')
    data.index = times
    data.index.name = 'time_utc'
    data = data.rename(columns={'Gb(i)': 'dni_w_m2', 'Gd(i)': 'tracking_diffuse_w_m2',
                                'Gr(i)': 'tracking_ground_reflected_w_m2', 'H_sun': 'pvgis_sun_elevation_deg',
                                'T2m': 'temperature_c', 'WS10m': 'wind_speed_m_s', 'Int': 'reconstructed'})
    quality = dict(source='PVGIS-SARAH3 satellite-derived historical irradiance',
                   is_on_site_measurement=False, rows=len(data), duplicate_times=0,
                   missing_cells=0, cadence_minutes=60, timestamp_minute=times[0].minute,
                   start_utc=str(times[0]), end_utc=str(times[-1]),
                   reconstructed_hours=int(data.reconstructed.sum()),
                   positive_dni_hours=int((data.dni_w_m2 > 0).sum()),
                   maximum_dni_w_m2=float(data.dni_w_m2.max()),
                   annual_dni_kwh_m2_hourly_sample_estimate=float(data.dni_w_m2.sum() / 1000),
                   integration_note='Sum of hourly SARAH instantaneous samples times 1 h; approximate irradiation, not continuous measurements.',
                   location=inputs['location'], raw_api_timestamp_description=raw['meta']['outputs']['hourly']['timestamp'])
    return data, quality


def convert_gemasolar_layout(raw_csv: str | Path, config: dict) -> tuple[pd.DataFrame, dict]:
    """Convert published XY locations without inventing missing heliostats.

    z=0 is a common mirror-centre datum, not a claim of zero pedestal height.
    The receiver optical height is measured above that datum. Target points
    lie at mid-height on the near cylinder mantle (single equatorial aiming).
    """
    raw = pd.read_csv(raw_csv, dtype={'id': str})
    if not {'id', 'x', 'y'}.issubset(raw.columns):
        raise ValueError('Layout requires id,x,y columns')
    if len(raw) != config['expected_mirrors']:
        raise ValueError('Unexpected mirror count; do not pad or synthesize missing mirrors')
    if raw.id.isna().any() or raw.id.duplicated().any() or raw[['x', 'y']].duplicated().any():
        raise ValueError('Missing IDs or duplicate layout locations/IDs')
    coordinates = raw[['x', 'y']].to_numpy(dtype=float)
    if not np.isfinite(coordinates).all():
        raise ValueError('Non-finite coordinates')
    radius = np.linalg.norm(coordinates, axis=1)
    if np.any(radius <= config['receiver_radius_m']):
        raise ValueError('Mirror is inside the receiver radius')
    aims = coordinates / radius[:, None] * config['receiver_radius_m']
    layout = pd.DataFrame(dict(mirror_id=['G'+identifier for identifier in raw.id],
                               x=raw.x, y=raw.y, z=0., width=config['mirror_width_m'],
                               height=config['mirror_height_m'], aim_x=aims[:,0], aim_y=aims[:,1],
                               aim_z=config['optical_height_m'], roll_deg=0., mount_type='azimuth_elevation'))
    quality = dict(rows=len(layout), missing_cells=0, duplicate_ids=0, duplicate_coordinates=0,
                   radius_min_m=float(radius.min()), radius_max_m=float(radius.max()),
                   x_range_m=[float(raw.x.min()),float(raw.x.max())],
                   y_range_m=[float(raw.y.min()),float(raw.y.max())],
                   datum='flat mirror-centre plane; no surveyed terrain or pedestal elevations',
                   layout_classification='public map-extracted Gemasolar research reconstruction, not owner as-built survey')
    return layout, quality
