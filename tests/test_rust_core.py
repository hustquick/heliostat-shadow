"""Rust/Python geometry equivalence at algorithm boundaries and real layouts."""

from dataclasses import replace

import numpy as np
import pytest

from field import load_layout
from heliostat import Heliostat
from rust_core import available, field_efficiencies, version
from simulation import PreparedField
from solar import sun_vector


pytestmark = pytest.mark.skipif(not available(), reason="Rust extension is not installed")


def assert_matches_python(mirrors, sun, indices, absolute=3e-8):
    prepared = PreparedField.from_mirrors(mirrors, sun)
    rows = field_efficiencies(mirrors, sun, target_indices=indices)
    assert [row["mirror_id"] for row in rows] == [mirrors[index].mirror_id for index in indices]
    for index, rust in zip(indices, rows):
        python = prepared.target_efficiencies(index)
        for key in ("eta_cosine", "eta_shadow", "eta_blocking", "eta_joint"):
            assert rust[key] == pytest.approx(python[key], abs=absolute)
        assert rust["shadow_occluders"] == python["shadow_occluders"]
        assert rust["blocking_occluders"] == python["blocking_occluders"]


def test_rust_core_version_matches_release():
    assert version() == "1.0.2"


def test_random_tilted_field_matches_python():
    rng = np.random.default_rng(52)
    mirrors = [
        Heliostat(str(index), tuple(rng.uniform([-5, -5, 0], [5, 5, 5])), 4, 3,
                  (0, 0, 30), float(rng.uniform(-180, 180)), "azimuth_elevation")
        for index in range(32)
    ]
    sun = np.array([0.5, -0.2, 0.8])
    sun /= np.linalg.norm(sun)
    assert_matches_python(mirrors, sun, list(range(len(mirrors))))


def test_alternative_tower_target_matches_python_override():
    mirrors = (
        Heliostat("target", (0, 0, 2.5), 4, 3, (0, 0, 60)),
        Heliostat("nearby", (2, 1, 2.5), 4, 3, (0, 0, 60)),
        Heliostat("far", (-4, 5, 2.5), 4, 3, (0, 0, 60)),
    )
    sun = np.array([0.35, -0.25, 0.9027735])
    sun /= np.linalg.norm(sun)
    alternative = replace(mirrors[0], aim_point=(45, 0, 75), tower_id="tower-2")
    python = PreparedField.from_mirrors(mirrors, sun).target_efficiencies(
        0, target_mirror=alternative)
    rust = field_efficiencies(
        mirrors,
        sun,
        target_indices=[0],
        target_overrides=[alternative],
    )[0]
    for key in ("eta_cosine", "eta_shadow", "eta_blocking", "eta_joint"):
        assert rust[key] == pytest.approx(python[key], abs=3e-8)
    assert rust["shadow_occluders"] == python["shadow_occluders"]
    assert rust["blocking_occluders"] == python["blocking_occluders"]


@pytest.mark.parametrize("timestamp,index", [
    ("2023-12-21 16:10:00+00:00", 639),
    ("2023-06-21 05:10:00+00:00", 1708),
    ("2023-09-21 07:10:00+00:00", 630),
])
def test_real_nearly_coincident_edges_match_python(timestamp, index):
    mirrors = load_layout("data/processed/gemasolar_layout.csv")
    sun = sun_vector(37.562, -5.33, timestamp, altitude=170).sun_to_sky
    assert_matches_python(mirrors, sun, [index])
