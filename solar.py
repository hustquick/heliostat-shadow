"""Solar position and unit vectors in East-North-Up coordinates."""

from datetime import datetime
from typing import NamedTuple

import numpy as np
import pandas as pd
import pvlib
from numpy.typing import NDArray


class SunPosition(NamedTuple):
    """Tuple-compatible result: ground-to-sun, incoming ray, pvlib table."""

    sun_to_sky: NDArray[np.float64]
    ray_dir: NDArray[np.float64]
    solar_pos: pd.DataFrame

    @property
    def is_daylight(self) -> bool:
        """Geometric solar centre is above the ideal horizontal horizon.

        This is a simple operating gate, not a terrain/sunrise model.
        """
        return bool(self.solar_pos["elevation"].iloc[0] > 0.0)


def sun_vector(
    latitude: float,
    longitude: float,
    time: str | datetime | pd.Timestamp,
    timezone: str | None = None,
    *,
    altitude: float = 0.0,
    pressure: float | None = None,
    temperature: float = 12.0,
    apparent: bool = True,
) -> SunPosition:
    """Calculate one solar position using pvlib's NREL SPA implementation.

    latitude/longitude: degrees, north/east positive.
    time: one timestamp; naive values require an explicit timezone (e.g.
    Europe/Madrid). Aware timestamps retain their instant and are converted
    to timezone when supplied. Ambiguous/nonexistent local DST times raise.
    altitude: metres above sea level; pressure: Pa (None estimates it from
    altitude); temperature: degrees C. Defaults are assumptions, not site data.
    apparent: use refraction-corrected elevation if True, geometric otherwise.

    Returns ENU arrays of shape (3,) and a one-row pvlib DataFrame. Vectors
    remain available at night; check result.is_daylight before field tracing.
    """
    if not np.isfinite(latitude) or not -90 <= latitude <= 90:
        raise ValueError("latitude must be finite and in [-90, 90] degrees")
    if not np.isfinite(longitude) or not -180 <= longitude <= 180:
        raise ValueError("longitude must be finite and in [-180, 180] degrees")
    if not np.isfinite(altitude):
        raise ValueError("altitude must be finite")
    if pressure is not None and (not np.isfinite(pressure) or pressure < 0):
        raise ValueError("pressure must be finite and nonnegative")
    if not np.isfinite(temperature) or temperature <= -273.15:
        raise ValueError("temperature must be finite and above absolute zero")
    if not isinstance(time, (str, datetime, pd.Timestamp)):
        raise TypeError("time must be a string, datetime, or pandas Timestamp")
    timestamp = pd.Timestamp(time)
    if pd.isna(timestamp):
        raise ValueError("time must not be NaT")
    if timestamp.tzinfo is None:
        if timezone is None:
            raise ValueError("timezone is required for a naive timestamp")
        timestamp = timestamp.tz_localize(timezone, ambiguous="raise", nonexistent="raise")
    elif timezone is not None:
        timestamp = timestamp.tz_convert(timezone)

    solar_pos = pvlib.solarposition.get_solarposition(
        time=pd.DatetimeIndex([timestamp]),
        latitude=latitude,
        longitude=longitude,
        altitude=altitude,
        pressure=pressure,
        temperature=temperature,
        method="nrel_numpy",
    )
    elevation_column = "apparent_elevation" if apparent else "elevation"
    elevation, azimuth = np.deg2rad(
        [solar_pos[elevation_column].iloc[0], solar_pos["azimuth"].iloc[0]]
    )
    sun_to_sky = np.array(
        [np.cos(elevation) * np.sin(azimuth),
         np.cos(elevation) * np.cos(azimuth),
         np.sin(elevation)],
        dtype=float,
    )
    return SunPosition(sun_to_sky, -sun_to_sky, solar_pos)


def clear_sky_irradiance(
    latitude: float,
    longitude: float,
    time: str | datetime | pd.Timestamp,
    timezone: str | None = None,
    *,
    altitude: float = 0.0,
    pressure: float | None = None,
    temperature: float = 12.0,
    linke_turbidity: float | None = None,
) -> pd.DataFrame:
    """Estimate clear-sky DNI, GHI and DHI (W/m²) using pvlib Ineichen.

    Time and site conventions match sun_vector. Linke turbidity is
    dimensionless (>= 1); None uses pvlib's bundled climatological lookup.
    These are clear-sky estimates, NOT measured/weather-adjusted irradiance.
    Irradiance is zero when the geometric solar centre is at/below horizon.
    UTC is used internally for day-of-year lookups; the returned one-row
    DataFrame retains the caller's time zone. Explicit pressure affects both
    refraction and absolute air mass, while altitude remains site elevation.
    """
    if linke_turbidity is not None and (
        not np.isfinite(linke_turbidity) or linke_turbidity < 1
    ):
        raise ValueError("linke_turbidity must be finite and >= 1")
    sun = sun_vector(
        latitude, longitude, time, timezone,
        altitude=altitude, pressure=pressure, temperature=temperature,
    )
    times = sun.solar_pos.index.tz_convert("UTC")
    position = sun.solar_pos.tz_convert("UTC")
    turbidity = (
        pvlib.clearsky.lookup_linke_turbidity(times, latitude, longitude)
        if linke_turbidity is None else linke_turbidity
    )
    relative_airmass = pvlib.atmosphere.get_relative_airmass(position["apparent_zenith"])
    site_pressure = pvlib.atmosphere.alt2pres(altitude) if pressure is None else pressure
    absolute_airmass = pvlib.atmosphere.get_absolute_airmass(relative_airmass, site_pressure)
    irradiance = pvlib.clearsky.ineichen(
        position["apparent_zenith"], absolute_airmass, turbidity,
        altitude=altitude, dni_extra=pvlib.irradiance.get_extra_radiation(times),
    )[["dni", "ghi", "dhi"]]
    if not sun.is_daylight:
        irradiance.loc[:, :] = 0.0
    if not np.isfinite(irradiance.to_numpy()).all():
        raise ValueError("Clear-sky model produced non-finite irradiance")
    irradiance.index = sun.solar_pos.index
    irradiance.attrs.update(
        model="pvlib Ineichen", units="W/m²", estimate="clear-sky",
        turbidity_source="pvlib climatology" if linke_turbidity is None else "user input",
    )
    return irradiance
