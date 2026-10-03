"""Literature-defined heliostat layout generators and explicit trimming helpers."""

from collections.abc import Sequence

import numpy as np

from heliostat import Heliostat


def _cylinder_aim(point_xy, receiver_centre, receiver_radius):
    delta = np.asarray(point_xy, dtype=float) - np.asarray(receiver_centre[:2], dtype=float)
    distance = np.linalg.norm(delta)
    if distance <= receiver_radius:
        raise ValueError('Mirror centre must lie outside its receiver cylinder')
    xy = np.asarray(receiver_centre[:2], dtype=float) + receiver_radius * delta / distance
    return float(xy[0]), float(xy[1]), float(receiver_centre[2])


def dual_tower_overlap_field(
    *, count: int, mirror_width_m: float, mirror_height_m: float,
    east_tower_xy=(500., 0.), west_tower_xy=(-500., 0.),
    receiver_height_m: float = 190., receiver_radius_m: float = 7.,
    field_radius_m: float = 780., inner_radius_m: float = 55.,
    spacing_m: float = 9.4, centre_elevation_m: float = 2.5,
) -> list[Heliostat]:
    """Build two partially overlapping circular fields on one hexagonal mesh.

    Points inside both field circles are marked ``tower_id='auto'`` so the
    viewer can select their target tower for each solar time. Coordinates are
    a deterministic reconstruction, not surveyed construction coordinates.
    """
    values = np.asarray([count, mirror_width_m, mirror_height_m, receiver_height_m,
                         receiver_radius_m, field_radius_m, inner_radius_m,
                         spacing_m, centre_elevation_m], dtype=float)
    if not np.isfinite(values).all() or count < 2 or min(values[1:8]) <= 0:
        raise ValueError('Dual-tower field parameters must be finite and positive')
    towers = {'east': np.asarray(east_tower_xy, dtype=float),
              'west': np.asarray(west_tower_xy, dtype=float)}
    if any(point.shape != (2,) or not np.isfinite(point).all() for point in towers.values()):
        raise ValueError('Tower coordinates must be finite 2-vectors')
    vertical = spacing_m * np.sqrt(3) / 2
    x_min = min(point[0] for point in towers.values()) - field_radius_m
    x_max = max(point[0] for point in towers.values()) + field_radius_m
    y_min = min(point[1] for point in towers.values()) - field_radius_m
    y_max = max(point[1] for point in towers.values()) + field_radius_m
    candidates = []
    for row, y in enumerate(np.arange(y_min, y_max + vertical, vertical)):
        offset = spacing_m / 2 if row % 2 else 0.
        for x in np.arange(x_min, x_max + spacing_m, spacing_m) + offset:
            point = np.array([x, y])
            distances = {name: float(np.linalg.norm(point - centre))
                         for name, centre in towers.items()}
            inside = [name for name, distance in distances.items()
                      if inner_radius_m <= distance <= field_radius_m]
            if not inside:
                continue
            nearest = min(inside, key=distances.get)
            candidates.append((min(distances.values()), float(x), float(y),
                               'auto' if len(inside) == 2 else nearest, nearest))
    if len(candidates) < count:
        raise ValueError(f'Only {len(candidates)} dual-tower candidates fit the boundary')
    # Fill both fields from the towers outwards while keeping the shared grid.
    candidates.sort(key=lambda item: (item[0], item[2], item[1]))
    selected = sorted(candidates[:count], key=lambda item: (item[2], item[1]))
    mirrors = []
    for index, (_, x, y, assignment, nearest) in enumerate(selected, 1):
        centre_xy = towers[nearest]
        receiver_centre = (centre_xy[0], centre_xy[1], receiver_height_m)
        aim = _cylinder_aim((x, y), receiver_centre, receiver_radius_m)
        mirrors.append(Heliostat(
            mirror_id=f'D{index:05d}', centre=(x, y, centre_elevation_m),
            width=mirror_width_m, height=mirror_height_m, aim_point=aim,
            mount_type='azimuth_elevation', tower_id=assignment,
        ))
    return mirrors


def annular_hex_field(
    *, count: int, mirror_width_m: float, mirror_height_m: float,
    tower_height_m: float, receiver_radius_m: float,
    field_radius_m: float, inner_radius_m: float, spacing_m: float,
    centre_elevation_m: float = 0.,
) -> list[Heliostat]:
    """Build a deterministic single-tower annular field on a hexagonal mesh."""
    values = np.asarray([mirror_width_m, mirror_height_m, tower_height_m,
                         receiver_radius_m, field_radius_m, inner_radius_m,
                         spacing_m, centre_elevation_m], dtype=float)
    if (count < 1 or not np.isfinite(values).all() or min(values[:7]) <= 0 or
            inner_radius_m <= receiver_radius_m or field_radius_m <= inner_radius_m):
        raise ValueError('Annular field parameters are invalid')
    vertical = spacing_m * np.sqrt(3) / 2
    candidates = []
    for row, y in enumerate(np.arange(-field_radius_m, field_radius_m + vertical, vertical)):
        offset = spacing_m / 2 if row % 2 else 0.
        for x in np.arange(-field_radius_m, field_radius_m + spacing_m, spacing_m) + offset:
            radius = float(np.hypot(x, y))
            if inner_radius_m <= radius <= field_radius_m:
                candidates.append((radius, float(x), float(y)))
    if len(candidates) < count:
        raise ValueError(f'Only {len(candidates)} annular candidates fit the boundary')
    candidates.sort(key=lambda item: (item[0], item[2], item[1]))
    selected = sorted(candidates[:count], key=lambda item: (item[2], item[1]))
    mirrors = []
    for index, (radius, x, y) in enumerate(selected, 1):
        aim = (receiver_radius_m * x / radius,
               receiver_radius_m * y / radius, tower_height_m)
        mirrors.append(Heliostat(
            mirror_id=f'C{index:05d}', centre=(x, y, centre_elevation_m),
            width=mirror_width_m, height=mirror_height_m, aim_point=aim,
            mount_type='azimuth_elevation',
        ))
    return mirrors


def rectangular_hex_field(*, count, mirror_width_m, mirror_height_m,
                          tower_height_m, receiver_radius_m,
                          field_width_m, field_height_m, inner_radius_m, spacing_m):
    """Reconstruction inside a documented rectangle, not surveyed coordinates."""
    if min(count, field_width_m, field_height_m, spacing_m) <= 0:
        raise ValueError('Invalid rectangular field dimensions')
    points = []
    dy = spacing_m * np.sqrt(3) / 2
    for row, y in enumerate(np.arange(-field_height_m/2, field_height_m/2, dy)):
        for x in np.arange(-field_width_m/2 + (row % 2)*spacing_m/2,
                           field_width_m/2, spacing_m):
            if np.hypot(x, y) >= inner_radius_m:
                points.append((float(x), float(y)))
    if len(points) < count:
        raise ValueError('Too few mirrors fit the rectangular boundary')
    # Spread retained points across the entire footprint, preserving mesh spacing.
    indices = np.linspace(0, len(points)-1, count, dtype=int)
    return [Heliostat(f'C{i+1:05d}', (*points[j], 0.), mirror_width_m,
                     mirror_height_m,
                     _cylinder_aim(points[j], (0., 0., tower_height_m), receiver_radius_m),
                     mount_type='azimuth_elevation') for i,j in enumerate(indices)]


def campo_radial_stagger_candidates(
    *,
    mirror_width_m: float,
    mirror_height_m: float,
    tower_height_m: float,
    receiver_radius_m: float,
    radial_increments: Sequence[float] = (np.cos(np.pi / 6), 1.0, 2.4),
    first_row_count: int = 46,
    candidate_count: int = 3864,
    separation_m: float = 0.0,
) -> list[Heliostat]:
    """Generate the three-zone Campo pattern used in Gemasolar-like studies.

    ``radial_increments`` are dimensionless multiples of the footprint
    diameter ``DM``.  Adjacent rows are staggered by half an angular pitch.
    Each new zone doubles the mirrors per row.  The number of complete rows in
    a zone follows the Campo 5.44 rule, except the last zone, which is extended
    only until ``candidate_count`` is reached.

    This returns the pre-trimming candidate field.  Collado and Guallar select
    2,650 mirrors from 3,864 candidates by optical performance; that separate
    selection is deliberately not hidden in this geometry generator.
    """
    width = float(mirror_width_m)
    height = float(mirror_height_m)
    if width <= 0 or height <= 0 or separation_m < 0:
        raise ValueError("Mirror dimensions must be positive and separation nonnegative")
    if first_row_count < 3 or candidate_count < first_row_count:
        raise ValueError("Invalid first-row or candidate count")
    increments = np.asarray(radial_increments, dtype=float)
    if increments.shape != (3,) or not np.isfinite(increments).all() or np.any(increments <= 0):
        raise ValueError("Campo Gemasolar layout requires three positive radial increments")

    dm = float(np.hypot(width, height) + separation_m)
    radius = dm * first_row_count / (2 * np.pi)
    mirrors: list[Heliostat] = []
    previous_last_radius: float | None = None
    for zone, increment in enumerate(increments):
        per_row = first_row_count * 2**zone
        normal_rows = int(per_row / 5.44)
        if previous_last_radius is not None:
            radius = previous_last_radius + increment * dm
        row = 0
        while row < normal_rows or (zone == len(increments) - 1 and len(mirrors) < candidate_count):
            angle = (np.arange(per_row) + (0.5 if row % 2 == 0 else 0.0)) * 2 * np.pi / per_row
            for azimuth in angle:
                if len(mirrors) >= candidate_count:
                    return mirrors
                x = float(radius * np.sin(azimuth))
                y = float(radius * np.cos(azimuth))
                horizontal_radius = np.hypot(x, y)
                aim = (receiver_radius_m * x / horizontal_radius,
                       receiver_radius_m * y / horizontal_radius,
                       tower_height_m)
                mirrors.append(Heliostat(
                    mirror_id=f"C{len(mirrors) + 1:04d}", centre=(x, y, 0.0),
                    width=width, height=height, aim_point=aim,
                    mount_type="azimuth_elevation",
                ))
            previous_last_radius = radius
            radius += increment * dm
            row += 1
    return mirrors


def select_by_score(candidates: Sequence[Heliostat], scores, count: int) -> list[Heliostat]:
    """Return the highest-scoring candidates in stable original-field order."""
    scores = np.asarray(scores, dtype=float)
    if scores.shape != (len(candidates),) or not np.isfinite(scores).all():
        raise ValueError("One finite score is required per candidate")
    if not 0 < count <= len(candidates):
        raise ValueError("Selection count is out of range")
    selected = set(np.argsort(-scores, kind="stable")[:count].tolist())
    return [mirror for index, mirror in enumerate(candidates) if index in selected]


def rescale_radial_extent(
    candidates: Sequence[Heliostat], *, outer_radius_m: float,
    tower_height_m: float, receiver_radius_m: float,
    inner_radius_m: float | None = None,
) -> list[Heliostat]:
    """Fit a generated radial field to a documented inner/outer radius.

    The azimuth, row ordering and mirror count are preserved.  This is useful
    for catalog reconstructions where the public plant data include a field
    boundary but not surveyed heliostat coordinates.
    """
    if not candidates:
        raise ValueError("At least one candidate is required")
    centres = np.asarray([m.centre for m in candidates], dtype=float)
    radii = np.linalg.norm(centres[:, :2], axis=1)
    source_inner, source_outer = float(radii.min()), float(radii.max())
    target_inner = source_inner if inner_radius_m is None else float(inner_radius_m)
    target_outer = float(outer_radius_m)
    if not np.isfinite([target_inner, target_outer, tower_height_m,
                        receiver_radius_m]).all():
        raise ValueError("Radial extent and receiver geometry must be finite")
    if source_outer <= source_inner or target_inner <= receiver_radius_m or target_outer <= target_inner:
        raise ValueError("Invalid source or target radial extent")
    scale = (target_outer - target_inner) / (source_outer - source_inner)
    result: list[Heliostat] = []
    for mirror, point, radius in zip(candidates, centres, radii):
        new_radius = target_inner + (float(radius) - source_inner) * scale
        direction = point[:2] / radius
        x, y = direction * new_radius
        aim = (receiver_radius_m * x / new_radius,
               receiver_radius_m * y / new_radius, tower_height_m)
        result.append(Heliostat(
            mirror_id=mirror.mirror_id,
            centre=(float(x), float(y), float(mirror.centre[2])),
            width=mirror.width, height=mirror.height, aim_point=aim,
            roll_deg=mirror.roll_deg, mount_type=mirror.mount_type,
        ))
    return result


def deform_radial_curves(
    candidates: Sequence[Heliostat], *, north_south_m: float, ellipticity_m: float,
    tower_height_m: float, receiver_radius_m: float,
) -> list[Heliostat]:
    """Turn circular rows into smooth non-circular periodic radial curves.

    The polar angle is measured from North, matching ``x=r*sin(theta)`` and
    ``y=r*cos(theta)``.  A bounded additive offset preserves radial row gaps
    much better than multiplying every radius by an angle-dependent factor.
    """
    parameters = np.asarray([north_south_m, ellipticity_m], dtype=float)
    if not np.isfinite(parameters).all() or np.abs(parameters).sum() >= 100:
        raise ValueError("Curve deformation is non-finite or too large")
    result = []
    for index, mirror in enumerate(candidates):
        x, y, z = mirror.centre
        radius = np.hypot(x, y)
        theta = np.arctan2(x, y)
        new_radius = radius + north_south_m * np.cos(theta) + ellipticity_m * np.cos(2 * theta)
        if new_radius <= 0:
            raise ValueError("Curve deformation produced a nonpositive radius")
        new_x, new_y = new_radius * np.sin(theta), new_radius * np.cos(theta)
        aim = (receiver_radius_m * new_x / new_radius,
               receiver_radius_m * new_y / new_radius, tower_height_m)
        result.append(Heliostat(
            mirror_id=f"N{index + 1:04d}", centre=(new_x, new_y, z),
            width=mirror.width, height=mirror.height, aim_point=aim,
            roll_deg=mirror.roll_deg, mount_type=mirror.mount_type,
        ))
    return result


def value_field_hex_candidates(
    *, spacing_m: float, inner_radius_m: float, boundary_radius_m: float,
    north_south: float, ellipticity: float, mirror_width_m: float,
    mirror_height_m: float, tower_height_m: float, receiver_radius_m: float,
) -> list[Heliostat]:
    """Generate a free hexagonal candidate mesh inside a non-circular boundary.

    No rings are imposed.  The later optical-value selection determines the
    final boundary and density.  The Fourier boundary merely limits land use.
    """
    values = np.asarray([spacing_m, inner_radius_m, boundary_radius_m,
                         north_south, ellipticity], dtype=float)
    if not np.isfinite(values).all() or spacing_m <= 0 or inner_radius_m <= 0:
        raise ValueError("Hex-field parameters must be finite and positive")
    if boundary_radius_m <= inner_radius_m or abs(north_south) + abs(ellipticity) >= 0.35:
        raise ValueError("Invalid non-circular field boundary")
    vertical = spacing_m * np.sqrt(3) / 2
    extent = boundary_radius_m * (1 + abs(north_south) + abs(ellipticity))
    mirrors = []
    row = 0
    for y in np.arange(-extent, extent + vertical, vertical):
        offset = 0.5 * spacing_m if row % 2 else 0.0
        for x in np.arange(-extent, extent + spacing_m, spacing_m) + offset:
            radius = float(np.hypot(x, y))
            if radius < inner_radius_m:
                continue
            theta = np.arctan2(x, y)
            limit = boundary_radius_m * (
                1 + north_south * np.cos(theta) + ellipticity * np.cos(2 * theta))
            if radius > limit:
                continue
            aim = (receiver_radius_m * x / radius, receiver_radius_m * y / radius,
                   tower_height_m)
            mirrors.append(Heliostat(
                mirror_id=f"V{len(mirrors) + 1:05d}", centre=(float(x), float(y), 0.0),
                width=mirror_width_m, height=mirror_height_m, aim_point=aim,
                mount_type="azimuth_elevation",
            ))
        row += 1
    return mirrors


def value_field_spiral_candidates(
    *, candidate_count: int, inner_radius_m: float, boundary_radius_m: float,
    north_south: float, ellipticity: float, mirror_width_m: float,
    mirror_height_m: float, tower_height_m: float, receiver_radius_m: float,
) -> list[Heliostat]:
    """Generate an alignment-free golden-angle candidate cloud.

    The spiral is only a deterministic low-discrepancy sampling device.  The
    final field is selected by optical value plus an explicit spacing rule, so
    it is not constrained to rings or to retain consecutive spiral points.
    """
    if candidate_count < 1 or boundary_radius_m <= inner_radius_m:
        raise ValueError("Invalid spiral candidate field")
    if abs(north_south) + abs(ellipticity) >= 0.35:
        raise ValueError("Invalid non-circular field boundary")
    golden_angle = np.pi * (3 - np.sqrt(5))
    mirrors = []
    for index in range(candidate_count):
        theta = index * golden_angle
        limit = boundary_radius_m * (
            1 + north_south * np.cos(theta) + ellipticity * np.cos(2 * theta))
        fraction = (index + 0.5) / candidate_count
        radius = np.sqrt(inner_radius_m**2 + fraction * (limit**2 - inner_radius_m**2))
        x, y = radius * np.sin(theta), radius * np.cos(theta)
        aim = (receiver_radius_m * x / radius, receiver_radius_m * y / radius,
               tower_height_m)
        mirrors.append(Heliostat(
            mirror_id=f"V{index + 1:05d}", centre=(float(x), float(y), 0.0),
            width=mirror_width_m, height=mirror_height_m, aim_point=aim,
            mount_type="azimuth_elevation",
        ))
    return mirrors


def select_by_score_with_spacing(candidates: Sequence[Heliostat], scores, count: int,
                                 minimum_spacing_m: float) -> list[Heliostat]:
    """Greedily select high-value candidates subject to exact centre spacing."""
    scores = np.asarray(scores, dtype=float)
    if scores.shape != (len(candidates),) or not np.isfinite(scores).all():
        raise ValueError("One finite score is required per candidate")
    if not 0 < count <= len(candidates) or minimum_spacing_m <= 0:
        raise ValueError("Invalid selection count or spacing")
    cell_size = float(minimum_spacing_m)
    cells: dict[tuple[int, int], list[tuple[int, np.ndarray]]] = {}
    selected = []
    for index in np.argsort(-scores, kind="stable"):
        point = np.asarray(candidates[index].centre[:2])
        cell = tuple(np.floor(point / cell_size).astype(int))
        conflict = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for _, other in cells.get((cell[0] + dx, cell[1] + dy), ()):
                    if np.linalg.norm(point - other) < minimum_spacing_m - 1e-9:
                        conflict = True
                        break
                if conflict:
                    break
            if conflict:
                break
        if conflict:
            continue
        selected.append(int(index))
        cells.setdefault(cell, []).append((int(index), point))
        if len(selected) == count:
            break
    if len(selected) != count:
        raise ValueError(f"Only {len(selected)} candidates satisfy the spacing constraint")
    keep = set(selected)
    return [mirror for index, mirror in enumerate(candidates) if index in keep]


def select_by_score_with_spacing_and_north_fraction(
    candidates: Sequence[Heliostat], scores, count: int, minimum_spacing_m: float,
    north_fraction: float,
) -> list[Heliostat]:
    """Greedy value selection with spacing and an explicit north/south capacity."""
    scores = np.asarray(scores, dtype=float)
    if scores.shape != (len(candidates),) or not np.isfinite(scores).all():
        raise ValueError("One finite score is required per candidate")
    if not 0 < count <= len(candidates) or minimum_spacing_m <= 0:
        raise ValueError("Invalid selection count or spacing")
    if not 0 < north_fraction < 1:
        raise ValueError("north_fraction must lie strictly between zero and one")
    north_limit = int(round(count * north_fraction))
    limits = {True: north_limit, False: count - north_limit}
    counts = {True: 0, False: 0}
    cell_size = float(minimum_spacing_m)
    cells: dict[tuple[int, int], list[np.ndarray]] = {}
    selected = []
    for index in np.argsort(-scores, kind="stable"):
        point = np.asarray(candidates[index].centre[:2])
        north = bool(point[1] >= 0)
        if counts[north] >= limits[north]:
            continue
        cell = tuple(np.floor(point / cell_size).astype(int))
        conflict = any(
            np.linalg.norm(point - other) < minimum_spacing_m - 1e-9
            for dx in (-1, 0, 1) for dy in (-1, 0, 1)
            for other in cells.get((cell[0] + dx, cell[1] + dy), ()))
        if conflict:
            continue
        selected.append(int(index))
        counts[north] += 1
        cells.setdefault(cell, []).append(point)
        if len(selected) == count:
            break
    if len(selected) != count:
        raise ValueError(f"Only {len(selected)} candidates satisfy spacing and sector capacity")
    keep = set(selected)
    return [mirror for index, mirror in enumerate(candidates) if index in keep]
