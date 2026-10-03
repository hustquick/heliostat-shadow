import numpy as np
import pytest

from layouts import (campo_radial_stagger_candidates, deform_radial_curves,
                     dual_tower_overlap_field,
                     rescale_radial_extent,
                     select_by_score, select_by_score_with_spacing,
                     select_by_score_with_spacing_and_north_fraction,
                     value_field_hex_candidates, value_field_spiral_candidates)


def test_dual_tower_overlap_field_has_fixed_and_switchable_zones():
    field = dual_tower_overlap_field(
        count=1200, mirror_width_m=6.6, mirror_height_m=4.5,
        east_tower_xy=(100, 0), west_tower_xy=(-100, 0),
        receiver_height_m=80, receiver_radius_m=4,
        field_radius_m=180, inner_radius_m=20, spacing_m=9,
    )
    assert len(field) == 1200
    assert {'east', 'west', 'auto'} <= {mirror.tower_id for mirror in field}
    assert len({mirror.mirror_id for mirror in field}) == len(field)
    assert all(mirror.centre[2] == 2.5 for mirror in field)


def test_campo_gemasolar_geometry_and_staggering():
    field = campo_radial_stagger_candidates(
        mirror_width_m=12.305, mirror_height_m=9.752,
        tower_height_m=140, receiver_radius_m=4, candidate_count=3864,
    )
    assert len(field) == 3864
    dm = np.hypot(12.305, 9.752)
    first = np.array([m.centre[:2] for m in field[:46]])
    assert np.linalg.norm(first, axis=1).mean() == pytest.approx(dm * 46 / (2 * np.pi))
    assert np.arctan2(first[0, 0], first[0, 1]) == pytest.approx(np.pi / 46)
    second_row_first = np.array(field[46].centre[:2])
    assert np.arctan2(second_row_first[0], second_row_first[1]) == pytest.approx(0.0)
    assert np.linalg.norm(second_row_first) - np.linalg.norm(first[0]) == pytest.approx(np.cos(np.pi / 6) * dm)


def test_score_selection_is_ranked_but_keeps_field_order():
    field = campo_radial_stagger_candidates(
        mirror_width_m=2, mirror_height_m=2, tower_height_m=20,
        receiver_radius_m=1, first_row_count=6, candidate_count=12,
    )
    selected = select_by_score(field, np.arange(12), 3)
    assert [m.mirror_id for m in selected] == ["C0010", "C0011", "C0012"]


def test_radial_rescale_preserves_count_azimuth_and_hits_boundary():
    field = campo_radial_stagger_candidates(
        mirror_width_m=2, mirror_height_m=2, tower_height_m=20,
        receiver_radius_m=1, first_row_count=6, candidate_count=30,
    )
    changed = rescale_radial_extent(
        field, outer_radius_m=30, inner_radius_m=5,
        tower_height_m=20, receiver_radius_m=1,
    )
    old = np.array([m.centre[:2] for m in field])
    new = np.array([m.centre[:2] for m in changed])
    assert len(changed) == len(field)
    assert np.linalg.norm(new, axis=1).min() == pytest.approx(5)
    assert np.linalg.norm(new, axis=1).max() == pytest.approx(30)
    assert np.allclose(old[:, 0]*new[:, 1], old[:, 1]*new[:, 0])
    assert all(np.hypot(*m.aim_point[:2]) == pytest.approx(1) for m in changed)


@pytest.mark.parametrize("increments", [(1, 2), (1, -1, 2)])
def test_invalid_campo_increments(increments):
    with pytest.raises(ValueError):
        campo_radial_stagger_candidates(
            mirror_width_m=2, mirror_height_m=2, tower_height_m=20,
            receiver_radius_m=1, radial_increments=increments,
        )


def test_non_circular_deformation_changes_radius_by_azimuth():
    field = campo_radial_stagger_candidates(
        mirror_width_m=2, mirror_height_m=2, tower_height_m=20,
        receiver_radius_m=1, first_row_count=8, candidate_count=16,
    )
    changed = deform_radial_curves(field, north_south_m=1.0, ellipticity_m=0.5,
                                   tower_height_m=20, receiver_radius_m=1)
    original = np.array([np.hypot(*m.centre[:2]) for m in field])
    deformed = np.array([np.hypot(*m.centre[:2]) for m in changed])
    assert not np.allclose(original, deformed)
    assert all(np.hypot(*m.aim_point[:2]) == pytest.approx(1) for m in changed)


def test_value_field_candidates_are_hexagonal_and_non_circular():
    field = value_field_hex_candidates(
        spacing_m=4, inner_radius_m=5, boundary_radius_m=20,
        north_south=0.1, ellipticity=0.05, mirror_width_m=2,
        mirror_height_m=2, tower_height_m=20, receiver_radius_m=1,
    )
    assert len(field) > 30
    radii = np.array([np.hypot(*m.centre[:2]) for m in field])
    assert radii.min() >= 5
    assert radii.max() <= 23 + 1e-9


def test_spiral_value_selection_enforces_spacing():
    candidates = value_field_spiral_candidates(
        candidate_count=200, inner_radius_m=5, boundary_radius_m=40,
        north_south=0.05, ellipticity=0.02, mirror_width_m=2,
        mirror_height_m=2, tower_height_m=20, receiver_radius_m=1)
    selected = select_by_score_with_spacing(
        candidates, -np.array([np.hypot(*m.centre[:2]) for m in candidates]), 30, 5)
    points = np.array([m.centre[:2] for m in selected])
    distances = np.linalg.norm(points[:, None] - points[None, :], axis=2)
    distances += np.eye(len(points)) * 1e9
    assert distances.min() >= 5 - 1e-9


def test_value_selection_enforces_north_fraction():
    candidates = value_field_spiral_candidates(
        candidate_count=400, inner_radius_m=5, boundary_radius_m=60,
        north_south=0, ellipticity=0, mirror_width_m=2,
        mirror_height_m=2, tower_height_m=20, receiver_radius_m=1)
    selected = select_by_score_with_spacing_and_north_fraction(
        candidates, np.ones(len(candidates)), 40, 4, 0.65)
    assert sum(m.centre[1] >= 0 for m in selected) == 26


@pytest.mark.parametrize("scores,count,spacing", [
    (np.ones(399), 40, 4),
    (np.r_[np.nan, np.ones(399)], 40, 4),
    (np.ones(400), 0, 4),
    (np.ones(400), 40, 0),
])
def test_sector_limited_selection_rejects_invalid_inputs(scores, count, spacing):
    candidates = value_field_spiral_candidates(
        candidate_count=400, inner_radius_m=5, boundary_radius_m=60,
        north_south=0, ellipticity=0, mirror_width_m=2,
        mirror_height_m=2, tower_height_m=20, receiver_radius_m=1)
    with pytest.raises(ValueError):
        select_by_score_with_spacing_and_north_fraction(
            candidates, scores, count, spacing, 0.65)


def test_rectangular_reconstruction_respects_boundary_and_clearance():
    from layouts import rectangular_hex_field
    from scipy.spatial import cKDTree
    mirrors = rectangular_hex_field(count=100, mirror_width_m=5., mirror_height_m=4.,
        tower_height_m=200., receiver_radius_m=6., field_width_m=120.,
        field_height_m=100., inner_radius_m=10., spacing_m=10.)
    points = np.array([m.centre[:2] for m in mirrors])
    assert len(points) == 100
    assert np.abs(points[:,0]).max() <= 60.
    assert np.abs(points[:,1]).max() <= 50.
    assert cKDTree(points).query(points,k=2)[0][:,1].min() >= 10.-1e-8
