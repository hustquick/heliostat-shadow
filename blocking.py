"""Blocking along EACH TARGET mirror's own reflected direction."""

from collections.abc import Sequence

import numpy as np
from numpy.typing import ArrayLike

from heliostat import Heliostat, reflection_direction
from shadow import _directional_efficiency


def blocking_efficiency(target: Heliostat, candidates: Sequence[Heliostat], sun_to_sky: ArrayLike) -> float:
    """Unblocked fraction [0,1] for parallel rays from target to receiver plane.

    The plane through target.aim_point is perpendicular to target r_i. Candidate
    mirrors beyond it or behind the target surface are excluded/clipped. This
    is a geometric blocking model, not receiver aperture or flux interception.
    """
    direction = reflection_direction(target)
    distance = float(np.linalg.norm(np.subtract(target.aim_point, target.centre)))
    return _directional_efficiency(target, candidates, sun_to_sky, direction, distance)
