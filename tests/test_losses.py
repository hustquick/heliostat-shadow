"""Exact rectangle cases, depth clipping and independent sampled-ray checks."""

import numpy as np
import pytest
from shapely.geometry import Polygon, box

from blocking import blocking_efficiency
from heliostat import Heliostat, mirror_normal, mirror_vertices, reflection_direction
from shadow import effective_projected_area, incremental_union, shadow_efficiency

SUN = np.array([0., 0., 1.])


def flat(name, centre, width=2, height=2, aim=None):
    # Vertical outgoing rays give horizontal rectangles for SUN.
    return Heliostat(name, centre, width, height, aim or (centre[0], centre[1], centre[2] + 20))


@pytest.mark.parametrize("function", [shadow_efficiency, blocking_efficiency])
def test_empty_self_behind_and_side_mirrors_do_not_reduce_area(function):
    target = flat("target", (0, 0, 0))
    assert function(target, [], SUN) == 1
    assert function(target, [target, flat("behind", (0, 0, -3)), flat("side", (10, 0, 3))], SUN) == 1


@pytest.mark.parametrize("function", [shadow_efficiency, blocking_efficiency])
def test_full_half_and_duplicate_overlap(function):
    target = flat("target", (0, 0, 0))
    full = flat("full", (0, 0, 3))
    half = flat("half", (1, 0, 3))
    duplicate = flat("duplicate", (1, 0, 5))
    assert function(target, [full], SUN) == pytest.approx(0)
    assert function(target, [half], SUN) == pytest.approx(0.5)
    assert function(target, [half, duplicate], SUN) == pytest.approx(0.5)
    assert function(target, [duplicate, half], SUN) == pytest.approx(0.5)


def test_union_difference_empty_and_invalid():
    assert incremental_union([]).is_empty
    assert incremental_union([box(0, 0, 1, 1), box(2, 0, 3, 1)]).area == 2
    assert effective_projected_area(box(0, 0, 2, 2), [box(1, 0, 3, 2)]) == 2
    assert effective_projected_area(box(0, 0, 2, 2), []) == 4
    with pytest.raises(ValueError):
        incremental_union([Polygon([(0, 0), (1, 1), (0, 1), (1, 0)])])
    with pytest.raises(ValueError):
        effective_projected_area(Polygon(), [])


def test_receiver_cutoff_is_different_from_shadow():
    target = flat("target", (0, 0, 0), aim=(0, 0, 10))
    beyond = flat("beyond", (0, 0, 12))
    on_receiver = flat("receiver", (0, 0, 10))
    assert blocking_efficiency(target, [beyond, on_receiver], SUN) == 1
    assert shadow_efficiency(target, [beyond], SUN) == 0


@pytest.mark.parametrize("height,expected_shadow,expected_blocking", [(0, 0.75, 0.75), (10, 0.5, 0.75)])
def test_tilted_mirror_crossing_target_or_receiver_is_clipped(height, expected_shadow, expected_blocking):
    target = flat("target", (0, 0, 0), aim=(0, 0, 10))
    # n=(sin60,0,cos60); width=2 projects to width 1. Half of this
    # candidate is above its centre and half below. Area inside target=2;
    # clipping at the centre leaves area 1 out of target area 4.
    r = np.array([np.sqrt(3) / 2, 0, -0.5])
    centre = np.array([0., 0., height])
    candidate = flat("tilted", tuple(centre), aim=tuple(centre + 20 * r))
    assert shadow_efficiency(target, [candidate], SUN) == pytest.approx(expected_shadow, abs=1e-8)
    assert blocking_efficiency(target, [candidate], SUN) == pytest.approx(expected_blocking, abs=1e-8)


def test_blocking_projects_along_target_direction_not_candidate_direction():
    r = np.array([1., 0., 1.]) / np.sqrt(2)
    target = flat("target", (0, 0, 0), aim=tuple(20 * r))
    # Candidate has a different r_j (vertical), yet covers all target rays
    # along target r_i; along r_j it would miss the target entirely.
    candidate = flat("blocker", (4, 0, 4), width=8, height=8)
    assert blocking_efficiency(target, [candidate], SUN) == pytest.approx(0)


def sampled_efficiency(target, candidates, sun, direction, terminal_distance=None, grid=180):
    """Independent 3D ray/rectangle intersections; no projection or Shapely."""
    def corners(obj):
        return mirror_vertices(obj, mirror_normal(sun, reflection_direction(obj)))
    vertices = corners(target)
    coordinates = (np.arange(grid) + 0.5) / grid
    a, b = np.meshgrid(coordinates, coordinates)
    points = (vertices[0] + a.ravel()[:, None] * (vertices[1] - vertices[0])
              + b.ravel()[:, None] * (vertices[3] - vertices[0]))
    covered = np.zeros(len(points), dtype=bool)
    for candidate in candidates:
        vertices = corners(candidate)
        width = vertices[1] - vertices[0]
        height = vertices[3] - vertices[0]
        n = np.cross(width, height)
        denominator = np.dot(direction, n)
        if abs(denominator) < 1e-12:
            continue
        depth = (vertices[0] - points) @ n / denominator
        hits = points + depth[:, None] * direction
        u = (hits - vertices[0]) @ width / np.dot(width, width)
        v = (hits - vertices[0]) @ height / np.dot(height, height)
        hit = (depth > 1e-9) & (u >= 0) & (u <= 1) & (v >= 0) & (v <= 1)
        if terminal_distance is not None:
            hit &= (hits - target.centre) @ direction < terminal_distance - 1e-9
        covered |= hit
    return 1 - covered.mean()


@pytest.mark.parametrize("seed", [7, 29, 42])
def test_polygon_results_match_independent_ray_intersections(seed):
    rng = np.random.default_rng(seed)
    sun = np.array([0.3, -0.2, 0.9])
    sun /= np.linalg.norm(sun)
    target = Heliostat("target", (0, 0, 0), 4, 3, (8, 3, 12), 23)
    candidates = [Heliostat(str(i), tuple(rng.uniform([-2, -2, -1], [4, 3, 7])),
                            3, 3, tuple(rng.uniform([5, -4, 9], [10, 5, 15])),
                            float(rng.uniform(0, 180))) for i in range(8)]
    reflected = reflection_direction(target)
    shadow = shadow_efficiency(target, candidates, sun)
    blocking = blocking_efficiency(target, candidates, sun)
    assert shadow == pytest.approx(sampled_efficiency(target, candidates, sun, sun), abs=0.004)
    assert blocking == pytest.approx(sampled_efficiency(target, candidates, sun, reflected,
                                                       np.linalg.norm(target.aim_point)), abs=0.004)


def test_night_has_no_defined_efficiency():
    with pytest.raises(ValueError, match="horizon"):
        shadow_efficiency(flat("target", (0, 0, 0)), [], [0, 0, -1])
