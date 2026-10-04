"""Duration-weighted field energy inputs; a complete UTC year is required."""
from dataclasses import dataclass
import numpy as np
import pandas as pd
import pvlib

@dataclass(frozen=True)
class Sample:
    timestamp: pd.Timestamp
    sun: np.ndarray
    dni: float
    duration_hours: float = 1.0

    def __post_init__(self):
        sun = np.asarray(self.sun, dtype=float)
        if sun.shape != (3,) or not np.isfinite(sun).all() or not np.isclose(np.linalg.norm(sun), 1., atol=1e-6, rtol=0):
            raise ValueError('Sample sun must be a finite unit vector')
        if not np.isfinite(self.dni) or self.dni < 0 or not np.isfinite(self.duration_hours) or self.duration_hours <= 0:
            raise ValueError('Sample DNI and duration are invalid')


def weather_samples(model, *, stratified=False):
    weather = model.weather
    index = pd.DatetimeIndex(weather.index)
    if len(index) == 0 or index.tz is None or index.has_duplicates or not index.is_monotonic_increasing:
        raise ValueError('Annual weather requires ordered, unique timezone-aware timestamps')
    index = index.tz_convert('UTC')
    year = int(index[0].year)
    expected = pd.date_range(f'{year}-01-01', f'{year+1}-01-01', freq='h', tz='UTC', inclusive='left')
    offset = index[0] - expected[0]
    # PVGIS hourly observations use a fixed :10 timestamp; retain their actual solar positions.
    if offset < pd.Timedelta(0) or offset >= pd.Timedelta(hours=1) or not index.equals(expected + offset):
        raise ValueError('Annual evaluation requires a complete UTC hourly year (8760 or 8784 rows)')
    c = model.config
    location = pvlib.location.Location(c['latitude'], c['longitude'], tz='UTC', altitude=c['altitude_m'])
    position = location.get_solarposition(index)
    dni = (location.get_clearsky(index)['dni'].to_numpy() if model.clear_sky
           else pd.to_numeric(weather['dni_w_m2'], errors='raise').to_numpy())
    if not np.isfinite(dni).all() or (dni < 0).any():
        raise ValueError('DNI must be finite and nonnegative; missing data cannot count as zero')
    elevation = np.deg2rad(position.elevation.to_numpy())
    azimuth = np.deg2rad(position.azimuth.to_numpy())
    suns = np.column_stack((np.cos(elevation)*np.sin(azimuth), np.cos(elevation)*np.cos(azimuth), np.sin(elevation)))
    active = (elevation > 0) & (dni > 0)
    rows = np.flatnonzero(active)
    samples = []
    if stratified:
        local = index.tz_convert(c['timezone'])
        for month in range(1, 13):
            for hour in range(24):
                members = rows[(local.month[rows] == month) & (local.hour[rows] == hour)]
                if not len(members):
                    continue
                centre = np.average(suns[members], axis=0, weights=dni[members])
                selected = members[np.argmin(np.linalg.norm(suns[members]-centre, axis=1))]
                samples.append(Sample(index[selected], suns[selected], float(dni[members].mean()), float(len(members))))
    else:
        samples = [Sample(index[i], suns[i], float(dni[i])) for i in rows]
    if not samples:
        raise ValueError('Annual weather has no productive daylight samples')
    provenance = dict(year=year, timestamp_offset_minutes=offset.total_seconds()/60, coverage_hours=len(index), active_hours=len(rows), sample_count=len(samples),
        method='month_local_hour_stratified' if stratified else 'hourly_full_year',
        dni_source='pvlib Ineichen clear-sky estimate' if model.clear_sky else 'bundled historical DNI; see plant source',
        plant_source=getattr(model, 'source', 'unspecified'),
        dni_kwh_m2=float(dni[active].sum()/1000),
        assumptions='Hourly instantaneous DNI treated as mean of its corresponding UTC hour; fixed observation offset retained; geometric horizon, continuous tracking, no availability/start-stop or storage model.',
        is_tmy=False)
    return samples, provenance


def evaluate_energy(mirrors, samples, config, receiver, *, progress=None):
    from rust_core import optical_energy
    shared = optical_energy(mirrors, samples, config)
    if shared is not None:
        for row, sample in zip(shared['samples'], samples):
            row.update(timestamp=sample.timestamp.isoformat(), duration_hours=sample.duration_hours, dni_w_m2=sample.dni)
        if progress:
            progress(len(samples), len(samples))
        shared['engine'] = 'shared_rust'
        return shared
    from scripts.optimize_ps10_greedy import delivered_power
    received_wh = normal_wh = 0.0
    rows = []
    for number, sample in enumerate(samples, 1):
        value, efficiency = (delivered_power(mirrors, [sample], config, receiver)
                             if sample.sun[2] > 0 and sample.dni > 0 else (0., 0.))
        received_wh += value
        normal_wh += (sample.dni if sample.sun[2] > 0 else 0.) * sample.duration_hours * sum(
            config.get('reflective_area_m2', m.width*m.height) for m in mirrors)
        rows.append(dict(timestamp=sample.timestamp.isoformat(), duration_hours=sample.duration_hours,
                         dni_w_m2=sample.dni, receiver_incident_wh=value))
        if progress:
            progress(number, len(samples))
    return dict(engine='python_reference', receiver_incident_kwh=received_wh/1000, incident_normal_kwh=normal_wh/1000,
                energy_weighted_optical_efficiency=received_wh/normal_wh if normal_wh else 0,
                samples=rows)
