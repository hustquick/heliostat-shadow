"""Shared ENU vector validation and orthographic projection primitives."""

import numpy as np
from numpy.typing import ArrayLike, NDArray

VECTOR_TOL = 1e-12


def vector3(value: ArrayLike, name: str = "vector") -> NDArray[np.float64]:
    """Convert a finite 3-vector without mutating the caller's data."""
    vector = np.asarray(value, dtype=float)
    if vector.shape != (3,) or not np.isfinite(vector).all():
        raise ValueError(f"{name} must be a finite vector of shape (3,)")
    return vector.copy()


def unit_vector(value: ArrayLike, name: str = "direction") -> NDArray[np.float64]:
    vector = vector3(value, name)
    length = float(np.linalg.norm(vector))
    if not np.isfinite(length) or length <= VECTOR_TOL:
        raise ValueError(f"{name} must have nonzero finite length")
    return vector / length


def projection_basis(direction: ArrayLike) -> NDArray[np.float64]:
    """Rows u, v, w form an orthonormal right-handed frame; w=unit(direction).

    The least-aligned ENU axis is selected as reference to avoid singularities
    for vertical rays. This computational frame is not a mirror roll convention.
    """
    w = unit_vector(direction)
    reference = np.eye(3)[np.argmin(np.abs(w))]
    u = unit_vector(np.cross(reference, w))
    v = np.cross(w, u)
    return np.array([u, v, w])


def project_vertices(
    vertices: ArrayLike, origin: ArrayLike, basis: ArrayLike
) -> NDArray[np.float64]:
    """Project (N,3) ENU vertices to (N,2) using a common origin and frame.

    Projection loses depth; callers must filter/clip upstream geometry first.
    """
    points = np.asarray(vertices, dtype=float)
    frame = np.asarray(basis, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise ValueError("vertices must be a finite array of shape (N, 3)")
    if (frame.shape != (3, 3) or not np.isfinite(frame).all()
            or not np.allclose(frame @ frame.T, np.eye(3), atol=1e-10, rtol=0)
            or not np.isclose(np.linalg.det(frame), 1.0, atol=1e-10, rtol=0)):
        raise ValueError("basis must be an orthonormal right-handed (3, 3) frame")
    return ((points - vector3(origin, "origin")) @ frame.T)[:, :2]
