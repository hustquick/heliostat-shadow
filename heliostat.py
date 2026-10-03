"""Validated rectangular planar heliostats; lengths in metres, angles in degrees."""

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from transform import unit_vector, vector3


@dataclass(frozen=True)
class Heliostat:
    """Centre and aim_point share one ENU origin/elevation datum.

    At roll=0, the width axis is the ENU East axis projected onto the mirror
    plane (North is used if this projection is nearly singular). The height
    axis is normal × width. Positive roll follows the right-hand rule about
    the outward mirror normal. This is an explicit mathematical pose convention,
    not a calibrated azimuth/elevation drive model for a particular plant.
    """

    mirror_id: str
    centre: tuple[float, float, float]
    width: float
    height: float
    aim_point: tuple[float, float, float]
    roll_deg: float = 0.0
    mount_type: str = "projected_east"
    tower_id: str = "tower-1"

    def __post_init__(self) -> None:
        if not isinstance(self.mirror_id, str) or not self.mirror_id.strip():
            raise ValueError("mirror_id must be a nonempty string")
        if self.mount_type not in {"projected_east", "azimuth_elevation"}:
            raise ValueError("mount_type must be projected_east or azimuth_elevation")
        if not isinstance(self.tower_id, str) or not self.tower_id.strip():
            raise ValueError("tower_id must be a nonempty string")
        centre = vector3(self.centre, "centre")
        aim = vector3(self.aim_point, "aim_point")
        unit_vector(aim - centre, "aim_point - centre")
        for name in ("width", "height", "roll_deg"):
            value = float(getattr(self, name))
            if not np.isfinite(value) or (name != "roll_deg" and value <= 0):
                raise ValueError(f"{name} must be finite" + (" and positive" if name != "roll_deg" else ""))
            object.__setattr__(self, name, value)
        object.__setattr__(self, "mirror_id", self.mirror_id.strip())
        object.__setattr__(self, "centre", tuple(centre))
        object.__setattr__(self, "aim_point", tuple(aim))
        object.__setattr__(self, "tower_id", self.tower_id.strip())


def reflection_direction(mirror: Heliostat) -> NDArray[np.float64]:
    """Unit direction from the target mirror centre to its own aim point."""
    return unit_vector(np.subtract(mirror.aim_point, mirror.centre), "reflection direction")


def mirror_normal(sun_to_sky: ArrayLike, reflected_direction: ArrayLike) -> NDArray[np.float64]:
    """Bisect s and r; incoming ray -s reflects into r. Reject antiparallel rays."""
    s = unit_vector(sun_to_sky, "sun_to_sky")
    r = unit_vector(reflected_direction, "reflected_direction")
    return unit_vector(s + r, "normal bisector (sun and reflection must not be antiparallel)")


def mirror_vertices(mirror: Heliostat, normal: ArrayLike) -> NDArray[np.float64]:
    """Four ENU corners, counterclockwise when viewed from +normal."""
    n = unit_vector(normal, "normal")
    reference = np.array([1.0, 0.0, 0.0])
    if abs(n[0]) > 1 - 1e-10:
        reference = np.array([0.0, 1.0, 0.0])
    width_axis = unit_vector(reference - np.dot(reference, n) * n)
    if mirror.mount_type == "azimuth_elevation":
        # Horizontal cross-elevation axis for a conventional azimuth/elevation
        # mount; at zenith the azimuth is indeterminate, so use the East axis.
        horizontal = np.cross([0.0, 0.0, 1.0], n)
        if np.linalg.norm(horizontal) > 1e-10:
            width_axis = unit_vector(horizontal)
    height_axis = np.cross(n, width_axis)
    roll = np.deg2rad(mirror.roll_deg % 360)
    u = np.cos(roll) * width_axis + np.sin(roll) * height_axis
    v = -np.sin(roll) * width_axis + np.cos(roll) * height_axis
    signs = np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]])
    return (np.asarray(mirror.centre)
            + signs[:, :1] * mirror.width / 2 * u
            + signs[:, 1:] * mirror.height / 2 * v)
