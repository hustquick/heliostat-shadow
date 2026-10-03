"""Depth-clipped planar mirror shadows and shared polygon operations."""

from collections.abc import Iterable, Sequence

import numpy as np
from numpy.typing import ArrayLike, NDArray
from shapely.geometry import GeometryCollection, Polygon
from shapely.geometry.base import BaseGeometry

from heliostat import Heliostat, mirror_normal, mirror_vertices, reflection_direction
from transform import VECTOR_TOL, project_vertices, projection_basis, unit_vector

# Metre tolerance for ray-depth separation. No shape snapping/buffering is used.
DEPTH_TOL = 1e-9


def _validate_polygon(polygon: Polygon) -> None:
    if not isinstance(polygon, Polygon) or not polygon.is_valid:
        raise ValueError("Expected a valid Shapely Polygon (no automatic geometry repair)")
    if not polygon.is_empty and not np.isfinite(polygon.bounds).all():
        raise ValueError("Polygon coordinates must be finite")


def incremental_union(polygons: Iterable[Polygon]) -> BaseGeometry:
    """Union valid polygons incrementally; empty input returns an empty geometry.

    Disjoint inputs may produce a MultiPolygon; overlaps are counted once.
    Invalid geometry is rejected rather than silently repaired or buffered.
    """
    merged: BaseGeometry = GeometryCollection()
    for polygon in polygons:
        _validate_polygon(polygon)
        if not polygon.is_empty:
            merged = merged.union(polygon)
    return merged


def effective_projected_area(target_polygon: Polygon, occluder_polygons: Iterable[Polygon]) -> float:
    """Visible projected area (m²); polygons must share a frame and be depth-clipped."""
    _validate_polygon(target_polygon)
    if target_polygon.is_empty or target_polygon.area <= DEPTH_TOL**2:
        raise ValueError("Target projected area is zero or numerically degenerate")
    visible = target_polygon.difference(incremental_union(occluder_polygons)).area
    return float(np.clip(visible, 0, target_polygon.area))


def _clip_halfspace(vertices: NDArray[np.float64], normal: NDArray[np.float64], offset: float) -> NDArray[np.float64]:
    """Clip a convex 3D polygon to normal·point + offset >= 0."""
    if len(vertices) == 0:
        return vertices
    distances = vertices @ normal + offset
    # A polygon wholly on the cutoff plane has no strictly-forward depth.
    if np.max(distances) <= 0:
        return np.empty((0, 3))
    clipped = []
    for i in range(len(vertices)):
        start, end = vertices[i - 1], vertices[i]
        a, b = distances[i - 1], distances[i]
        if (a >= 0) != (b >= 0):
            clipped.append(start + (end - start) * a / (a - b))
        if b >= 0:
            clipped.append(end)
    return np.asarray(clipped, dtype=float).reshape(-1, 3)


def _directional_efficiency(
    target: Heliostat,
    candidates: Sequence[Heliostat],
    sun_to_sky: ArrayLike,
    direction: ArrayLike,
    receiver_distance: float | None = None,
) -> float:
    """Trace parallel rays toward +direction through depth-clipped polygons.

    For blocking, the terminal plane passes through the target aim point and
    is perpendicular to its reflected direction. Aperture/interception is
    outside this model. Candidate poses use their OWN aim points; projection
    and depth comparisons use the TARGET frame throughout.
    """
    s = unit_vector(sun_to_sky, "sun_to_sky")
    if s[2] <= 0:
        raise ValueError("Solar direction must be above the horizon; skip night-time field calculations")
    d = unit_vector(direction)
    normal = mirror_normal(s, reflection_direction(target))
    cosine = float(normal @ d)
    if cosine <= VECTOR_TOL:
        raise ValueError("Target is edge-on or back-facing in the ray direction")
    origin = np.asarray(target.centre)
    basis = projection_basis(d)
    target_vertices = mirror_vertices(target, normal)
    target_polygon = Polygon(project_vertices(target_vertices, origin, basis))
    if receiver_distance is not None and np.max((target_vertices - origin) @ d) >= receiver_distance - DEPTH_TOL:
        raise ValueError("Receiver plane must lie beyond every target vertex")
    target_radius = np.hypot(target.width, target.height) / 2

    def projected_occluders():
        for candidate in candidates:
            if candidate.mirror_id == target.mirror_id:
                continue
            delta = np.subtract(candidate.centre, target.centre)
            transverse = delta - np.dot(delta, d) * d
            candidate_radius = np.hypot(candidate.width, candidate.height) / 2
            # Bounding spheres give a conservative test even for tilted mirrors.
            if np.linalg.norm(transverse) > target_radius + candidate_radius + DEPTH_TOL:
                continue
            candidate_normal = mirror_normal(s, reflection_direction(candidate))
            local_vertices = mirror_vertices(candidate, candidate_normal) - origin
            # For a point P on the candidate, its forward ray depth relative
            # to the target plane is dot(P-origin, normal)/dot(d, normal).
            # Clip vertices, not centres: a tilted mirror may straddle the plane.
            clipped = _clip_halfspace(local_vertices, normal, -DEPTH_TOL * cosine)
            if receiver_distance is not None:
                clipped = _clip_halfspace(clipped, -d, receiver_distance - DEPTH_TOL)
            if len(clipped) < 3:
                continue
            polygon = Polygon((clipped @ basis.T)[:, :2])
            if polygon.area <= DEPTH_TOL**2:
                continue
            _validate_polygon(polygon)
            # Bounding-box overlap is safe; Shapely handles the exact boundary.
            if target_polygon.intersects(polygon):
                yield polygon

    area = effective_projected_area(target_polygon, projected_occluders())
    return float(np.clip(area / target_polygon.area, 0, 1))


def shadow_efficiency(target: Heliostat, candidates: Sequence[Heliostat], sun_to_sky: ArrayLike) -> float:
    """Fraction [0,1] not shaded by other mirror rectangles (toward +s).

    Incident sunlight travels along -s; checking for upstream mirrors uses +s.
    Self is excluded by mirror_id. Pass all other mirrors unless an external
    candidate filter has been independently checked to be conservative.
    """
    return _directional_efficiency(target, candidates, sun_to_sky, sun_to_sky)
