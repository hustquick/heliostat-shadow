"""Physical direction, time handling and NREL reference checks."""

import numpy as np
import pandas as pd
import pytest

import solar
from solar import sun_vector


@pytest.mark.parametrize(
    "elevation,azimuth,expected",
    [(0, 0, [0, 1, 0]), (0, 90, [1, 0, 0]),
     (0, 180, [0, -1, 0]), (0, 270, [-1, 0, 0]), (90, 0, [0, 0, 1])],
)
def test_enu_cardinal_directions(monkeypatch, elevation, azimuth, expected):
    def position(**kwargs):
        return pd.DataFrame(
            {"apparent_elevation": [elevation], "elevation": [elevation],
             "azimuth": [azimuth]}, index=kwargs["time"]
        )
    monkeypatch.setattr(solar.pvlib.solarposition, "get_solarposition", position)
    s, d, _ = sun_vector(0, 0, "2026-06-21 12:00", "UTC")
    np.testing.assert_allclose(s, expected, atol=1e-15)
    np.testing.assert_array_equal(d, -s)


def test_nrel_spa_reference():
    # Published SPA example: https://midcdmz.nlr.gov/spa/spa_tester.c
    result = sun_vector(
        39.742476, -105.1786, "2003-10-17T12:30:30-07:00",
        altitude=1830.14, pressure=82000, temperature=11,
    )
    row = result.solar_pos.iloc[0]
    assert row["azimuth"] == pytest.approx(194.34024, abs=0.02)
    assert row["apparent_zenith"] == pytest.approx(50.11162, abs=0.02)
    assert np.linalg.norm(result.sun_to_sky) == pytest.approx(1.0, abs=1e-12)
    assert result.is_daylight


def test_timezone_equivalence_and_aware_conversion():
    local = sun_vector(37.562, -5.330, "2026-06-21 12:00", "Europe/Madrid")
    utc = sun_vector(37.562, -5.330, "2026-06-21T10:00:00Z")
    converted = sun_vector(37.562, -5.330, "2026-06-21T10:00:00Z", "Europe/Madrid")
    np.testing.assert_allclose(local.sun_to_sky, utc.sun_to_sky, atol=1e-12)
    np.testing.assert_allclose(local.sun_to_sky, converted.sun_to_sky, atol=1e-12)


def test_night_is_reported_without_inventing_zero_direction():
    result = sun_vector(37.562, -5.330, "2026-06-21 01:00", "Europe/Madrid")
    assert not result.is_daylight
    assert result.sun_to_sky[2] < 0
    assert np.linalg.norm(result.sun_to_sky) == pytest.approx(1.0)


def test_geometric_elevation_option():
    result = sun_vector(37.562, -5.330, "2026-06-21 08:00", "Europe/Madrid", apparent=False)
    elevation = np.rad2deg(np.arcsin(result.sun_to_sky[2]))
    assert elevation == pytest.approx(result.solar_pos["elevation"].iloc[0])


def test_naive_time_requires_timezone():
    with pytest.raises(ValueError, match="timezone"):
        sun_vector(0, 0, "2026-06-21 12:00")


@pytest.mark.parametrize("latitude,longitude", [(91, 0), (0, 181), (np.nan, 0), (0, np.inf)])
def test_bad_coordinates(latitude, longitude):
    with pytest.raises(ValueError):
        sun_vector(latitude, longitude, "2026-06-21T12:00:00Z")


@pytest.mark.parametrize("time", ["NaT", pd.NaT, 123456789])
def test_invalid_timestamp(time):
    with pytest.raises((TypeError, ValueError)):
        sun_vector(0, 0, time, "UTC")


@pytest.mark.parametrize("time", ["2026-03-29 02:30", "2026-10-25 02:30"])
def test_dst_transition_requires_unambiguous_instant(time):
    # pandas timezone backends expose different exception classes.
    with pytest.raises(Exception) as caught:
        sun_vector(37.562, -5.330, time, "Europe/Madrid")
    assert type(caught.value).__name__ in {"AmbiguousTimeError", "NonExistentTimeError", "ValueError"}
