"""Analytic geometry tests independent of the projection implementation."""

import numpy as np
import pytest
from shapely.geometry import Polygon

from heliostat import Heliostat, mirror_normal, mirror_vertices, reflection_direction
from transform import project_vertices, projection_basis


def mirror(**kwargs):
    args = dict(mirror_id="test", centre=(1, 2, 3), width=4, height=2, aim_point=(1, 2, 30))
    args.update(kwargs)
    return Heliostat(**args)


def test_reflection_law():
    s = np.array([0.2, -0.4, 0.8])
    s /= np.linalg.norm(s)
    r = reflection_direction(mirror(aim_point=(10, 20, 30)))
    n = mirror_normal(s, r)
    incoming = -s
    reflected = incoming - 2 * np.dot(incoming, n) * n
    np.testing.assert_allclose(reflected, r, atol=1e-12)
    assert np.dot(n, s) == pytest.approx(np.dot(n, r))


@pytest.mark.parametrize("roll", [0, 31, 90, 360])
def test_vertices_coplanarity_dimensions_winding_and_projected_area(roll):
    obj = mirror(roll_deg=roll)
    n = np.array([1, 2, 3]) / np.sqrt(14)
    vertices = mirror_vertices(obj, n)
    np.testing.assert_allclose(vertices.mean(axis=0), obj.centre, atol=1e-12)
    np.testing.assert_allclose((vertices - obj.centre) @ n, 0, atol=1e-12)
    edges = np.roll(vertices, -1, axis=0) - vertices
    np.testing.assert_allclose(np.linalg.norm(edges, axis=1), [4, 2, 4, 2])
    np.testing.assert_allclose(np.cross(edges[0], edges[1]), 8 * n)
    direction = np.array([0, 0, 1])
    polygon = Polygon(project_vertices(vertices, obj.centre, projection_basis(direction)))
    assert polygon.area == pytest.approx(8 * abs(n @ direction))


def test_roll_zero_and_ninety_have_explicit_axes():
    at_zero = mirror_vertices(mirror(centre=(0, 0, 0)), [0, 0, 1])
    at_ninety = mirror_vertices(mirror(centre=(0, 0, 0), roll_deg=90), [0, 0, 1])
    np.testing.assert_allclose(at_zero[1] - at_zero[0], [4, 0, 0], atol=1e-12)
    np.testing.assert_allclose(at_ninety[1] - at_ninety[0], [0, 4, 0], atol=1e-12)


@pytest.mark.parametrize("direction", [[0, 0, 1], [1, 0, 0], [0, -1, 0], [1e-14, 0, 1], [1, 2, 3]])
def test_projection_frame_handles_axis_aligned_rays(direction):
    basis = projection_basis(direction)
    np.testing.assert_allclose(basis @ basis.T, np.eye(3), atol=1e-12)
    assert np.linalg.det(basis) == pytest.approx(1)
    np.testing.assert_allclose(basis[2], direction / np.linalg.norm(direction))


@pytest.mark.parametrize("kwargs", [
    {"width": 0}, {"height": -1}, {"roll_deg": np.nan}, {"centre": (0, 0, np.inf)},
    {"centre": (1, 2)}, {"aim_point": (1, 2, 3)}, {"mirror_id": " "},
])
def test_bad_mirror_input(kwargs):
    with pytest.raises(ValueError):
        mirror(**kwargs)


def test_degenerate_directions_and_bad_frame():
    with pytest.raises(ValueError):
        mirror_normal([0, 0, 1], [0, 0, -1])
    with pytest.raises(ValueError):
        projection_basis([0, 0, 0])
    with pytest.raises(ValueError):
        project_vertices([[0, 0, 0]], [0, 0, 0], np.zeros((3, 3)))
