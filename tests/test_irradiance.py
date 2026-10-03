"""Clear-sky physical relations and model input handling."""

import numpy as np
import pytest

from solar import clear_sky_irradiance, sun_vector


def test_daytime_energy_balance_and_units():
    args = (37.562, -5.330, "2026-06-21 12:00", "Europe/Madrid")
    sun = sun_vector(*args)
    result = clear_sky_irradiance(*args)
    row = result.iloc[0]
    assert 0 < row.dni < 1400
    assert (row >= 0).all()
    assert row.ghi == pytest.approx(row.dni * sun.sun_to_sky[2] + row.dhi)
    assert result.index.equals(sun.solar_pos.index)
    assert result.attrs["units"] == "W/m²"
    assert result.attrs["estimate"] == "clear-sky"


def test_night_is_zero():
    result = clear_sky_irradiance(37.562, -5.330, "2026-06-21 01:00", "Europe/Madrid")
    np.testing.assert_array_equal(result.to_numpy(), np.zeros((1, 3)))


def test_same_instant_across_calendar_boundary():
    # At this Pacific site the sun is up; local and UTC calendar days differ.
    local = clear_sky_irradiance(20, -155, "2026-06-20T16:00:00-10:00")
    utc = clear_sky_irradiance(20, -155, "2026-06-21T02:00:00Z")
    assert local.dni.iloc[0] > 0
    np.testing.assert_allclose(local.to_numpy(), utc.to_numpy(), atol=1e-10)


def test_greater_turbidity_reduces_dni():
    args = (37.562, -5.330, "2026-06-21T10:00:00Z")
    clean = clear_sky_irradiance(*args, linke_turbidity=2)
    hazy = clear_sky_irradiance(*args, linke_turbidity=6)
    assert clean.dni.iloc[0] > hazy.dni.iloc[0] > 0
    assert clean.attrs["turbidity_source"] == "user input"


def test_pressure_affects_airmass_at_fixed_altitude():
    args = (37.562, -5.330, "2026-06-21T10:00:00Z")
    low = clear_sky_irradiance(*args, pressure=80000, linke_turbidity=3)
    high = clear_sky_irradiance(*args, pressure=101325, linke_turbidity=3)
    assert low.dni.iloc[0] > high.dni.iloc[0]


@pytest.mark.parametrize("turbidity", [0, 0.9, np.nan, np.inf])
def test_invalid_turbidity(turbidity):
    with pytest.raises(ValueError, match="linke_turbidity"):
        clear_sky_irradiance(0, 0, "2026-06-21T12:00:00Z", linke_turbidity=turbidity)
