"""Prepared full-field evaluation with exact planar union of shadow and blocking.

The reference scalar APIs remain available for cross-checks. This path caches
mirror poses and uses a conservative vectorized bounding-sphere candidate test.
"""

from dataclasses import dataclass
from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from shapely.geometry import Polygon
from shapely import set_precision

from heliostat import Heliostat, mirror_normal, mirror_vertices, reflection_direction
from optics import ATMOSPHERIC_MODEL, atmospheric_transmittance
from receiver import CylindricalReceiver, cylinder_interception, effective_angular_sigma
from rust_core import field_efficiencies as rust_field_efficiencies
from shadow import DEPTH_TOL, _clip_halfspace, incremental_union
from transform import VECTOR_TOL, projection_basis, unit_vector


@dataclass
class PreparedField:
    mirrors: tuple[Heliostat, ...]
    sun: np.ndarray
    centres: np.ndarray
    normals: np.ndarray
    reflected: np.ndarray
    vertices: np.ndarray
    radii: np.ndarray
    receiver_distances: np.ndarray
    flat_xy_tree: cKDTree | None
    max_radius: float

    @classmethod
    def from_mirrors(cls, mirrors: Sequence[Heliostat], sun_to_sky) -> 'PreparedField':
        mirrors = tuple(mirrors)
        if not mirrors or len({m.mirror_id for m in mirrors}) != len(mirrors):
            raise ValueError('Field must contain mirrors with unique IDs')
        sun = unit_vector(sun_to_sky)
        if sun[2] <= 0:
            raise ValueError('Skip field evaluation below the horizon')
        centres = np.array([m.centre for m in mirrors])
        reflected = np.array([reflection_direction(m) for m in mirrors])
        normals = np.array([mirror_normal(sun, r) for r in reflected])
        vertices = np.array([mirror_vertices(m, n) for m, n in zip(mirrors, normals)])
        radii = np.array([np.hypot(m.width, m.height) / 2 for m in mirrors])
        distances = np.linalg.norm(np.array([m.aim_point for m in mirrors]) - centres, axis=1)
        flat_xy_tree = cKDTree(centres[:, :2]) if np.ptp(centres[:, 2]) <= DEPTH_TOL else None
        return cls(mirrors, sun, centres, normals, reflected, vertices, radii, distances,
                   flat_xy_tree, float(radii.max()))

    def target_efficiencies(self, index: int, *, conservative_filter: bool = True,
                            target_mirror: Heliostat | None = None) -> dict:
        diagnostics = self.target_projection_diagnostics(
            index, conservative_filter=conservative_filter, target_mirror=target_mirror)
        target = diagnostics['target_polygon']
        shadow = diagnostics['shadow']['union_polygon']
        blocking = diagnostics['blocking']['union_polygon']
        eta_shadow = target.difference(shadow).area / target.area
        eta_blocking = target.difference(blocking).area / target.area
        eta_joint = target.difference(shadow.union(blocking)).area / target.area
        if not max(0, eta_shadow + eta_blocking - 1) - 1e-9 <= eta_joint <= min(eta_shadow, eta_blocking) + 1e-9:
            raise ArithmeticError('Joint geometry violates area bounds; refusing invalid field power')
        normal = diagnostics['target_normal']
        mirror = target_mirror or self.mirrors[index]
        return dict(mirror_id=mirror.mirror_id, eta_cosine=float(normal @ self.sun),
                    eta_shadow=float(np.clip(eta_shadow, 0, 1)),
                    eta_blocking=float(np.clip(eta_blocking, 0, 1)),
                    eta_joint=float(np.clip(eta_joint, 0, 1)),
                    shadow_occluders=len(diagnostics['shadow']['occluder_ids']),
                    blocking_occluders=len(diagnostics['blocking']['occluder_ids']))

    def target_projection_diagnostics(self, index: int, *, conservative_filter: bool = True,
                                      target_mirror: Heliostat | None = None) -> dict:
        """Return projected polygons and candidate IDs for one target mirror.

        The returned Shapely polygons use the target mirror's common 2D plane
        coordinates.  ``candidate_ids`` are mirrors surviving the conservative
        transverse-distance filter; ``occluder_ids`` have a positive overlap
        after depth clipping.  This is the geometry consumed by diagnostics,
        so visual tools do not implement a second projection algorithm.
        """
        if not isinstance(index, (int, np.integer)) or not 0 <= int(index) < len(self.mirrors):
            raise IndexError('Mirror index is out of range')
        index = int(index)
        mirror = target_mirror or self.mirrors[index]
        if mirror.mirror_id != self.mirrors[index].mirror_id or not np.allclose(
                mirror.centre, self.centres[index], rtol=0., atol=DEPTH_TOL):
            raise ValueError('Alternative target must preserve mirror ID and centre')
        origin = np.asarray(mirror.centre)
        reflected = reflection_direction(mirror)
        normal = mirror_normal(self.sun, reflected)
        target_vertices = mirror_vertices(mirror, normal)
        target_radius = np.hypot(mirror.width, mirror.height) / 2
        receiver_distance = np.linalg.norm(np.asarray(mirror.aim_point) - origin)
        plane = projection_basis(normal)[:2]
        # Shared 1 nm precision grid makes nearly coincident rectangle edges
        # consistent across GEOS intersection/union/difference operations.
        # Without it a valid polygon union can change edge rounding enough
        # that difference incorrectly drops a large overlapping component.
        target = set_precision(Polygon((target_vertices - origin) @ plane.T), DEPTH_TOL)
        modes = []
        for blocking, direction in ((False, self.sun), (True, reflected)):
            cosine = float(normal @ direction)
            if cosine <= VECTOR_TOL or target.area <= DEPTH_TOL**2:
                raise ValueError('Degenerate target projection')
            distance = receiver_distance
            if blocking and np.max((target_vertices - origin) @ direction) >= distance - DEPTH_TOL:
                raise ValueError('Receiver plane must lie beyond all target vertices')
            candidate_indices = np.arange(len(self.mirrors))
            if conservative_filter:
                # For flat fields, a mirror centre farther than R/d_z in the
                # horizontal plane cannot enter the radius-R cylinder around
                # an upward ray.  The KD-tree changes only the search cost;
                # the original exact sphere and vertex-depth tests remain.
                if self.flat_xy_tree is not None and direction[2] > VECTOR_TOL:
                    radius = (self.max_radius + target_radius + DEPTH_TOL) / direction[2]
                    candidate_indices = np.asarray(
                        self.flat_xy_tree.query_ball_point(origin[:2], radius), dtype=int)
                delta = self.centres[candidate_indices] - origin
                # Same enclosing spheres as scalar reference, no nearest-K cutoff.
                along = delta @ direction
                transverse = delta - along[:, None] * direction
                keep = np.linalg.norm(transverse, axis=1) <= (
                    self.radii[candidate_indices] + target_radius + DEPTH_TOL)
                # Depth eligibility is based on the complete mirror polygon,
                # not only its centre.  Keep a straddling mirror whenever any
                # finite-area part can lie ahead of the target plane.  For
                # blocking, also require some part before the receiver plane.
                local_vertices = self.vertices[candidate_indices] - origin
                keep &= np.max(local_vertices @ normal, axis=1) > DEPTH_TOL * cosine
                if blocking:
                    keep &= np.min(local_vertices @ direction, axis=1) < distance - DEPTH_TOL
                candidate_indices = candidate_indices[keep]
            candidate_indices = candidate_indices[candidate_indices != index]
            polygons = []
            polygon_ids = []
            decisions = []
            for j in candidate_indices:
                clipped = _clip_halfspace(self.vertices[j] - origin, normal, -DEPTH_TOL * cosine)
                if blocking:
                    clipped = _clip_halfspace(clipped, -direction, distance - DEPTH_TOL)
                if len(clipped) < 3:
                    decisions.append(dict(mirror_id=self.mirrors[j].mirror_id, reason='depth_rejected'))
                    continue
                # Project along the physical ray onto the actual target plane.
                # Both mechanisms now live in the SAME mirror coordinates.
                on_plane = clipped - ((clipped @ normal) / cosine)[:, None] * direction
                polygon = set_precision(Polygon(on_plane @ plane.T), DEPTH_TOL)
                if polygon.area <= DEPTH_TOL**2:
                    decisions.append(dict(mirror_id=self.mirrors[j].mirror_id, reason='degenerate'))
                    continue
                if not polygon.is_valid:
                    raise ValueError('Invalid projected occluder')
                overlap = target.intersection(polygon)
                if overlap.area > DEPTH_TOL**2:
                    polygons.append(overlap)
                    polygon_ids.append(self.mirrors[j].mirror_id)
                decisions.append(dict(mirror_id=self.mirrors[j].mirror_id,
                                      reason='overlap' if overlap.area > DEPTH_TOL**2 else 'no_overlap',
                                      overlap_area_m2=float(overlap.area)))
            modes.append(dict(candidate_ids=[self.mirrors[j].mirror_id for j in candidate_indices],
                              occluder_ids=polygon_ids, polygons=polygons, decisions=decisions,
                              union_polygon=incremental_union(polygons)))
        return dict(target_polygon=target, target_basis=plane, target_normal=normal,
                    shadow=modes[0], blocking=modes[1])

    def evaluate(self, dni_w_m2: float, *, reflective_area_m2: float | None = None,
                 atmospheric_model: str = ATMOSPHERIC_MODEL,
                 mirror_reflectivity: float = 1., mirror_cleanliness: float = 1.,
                 receiver: CylindricalReceiver | None = None,
                 receivers: Mapping[str, CylindricalReceiver] | None = None,
                 receiver_absorptivity: float = 1.,
                 receiver_thermal_efficiency: float = 1.,
                 sunshape_mrad: float = 2.51, slope_error_mrad: float = 2.6,
                 tracking_error_mrad: float = 2.1, receiver_quadrature_order: int = 64) -> pd.DataFrame:
        """Return per-mirror geometry and a staged optical power budget.

        Reflective area may differ from the rectangle envelope (facet gaps).
        A scalar fill factor uniformly distributed on each mirror is assumed.
        Geometric power excludes atmospheric attenuation; post-atmosphere
        power is a diagnostic with ideal mirror reflectivity. Additional
        Optical power includes reflectivity, cleanliness, atmosphere and finite
        receiver interception when a receiver is supplied. Receiver absorbed
        power additionally applies solar absorptivity. Neither is electricity.
        """
        if not np.isfinite(dni_w_m2) or dni_w_m2 < 0:
            raise ValueError('DNI must be finite and nonnegative')
        if not np.isscalar(mirror_reflectivity) or not np.isfinite(mirror_reflectivity) or not 0 <= mirror_reflectivity <= 1:
            raise ValueError('Mirror reflectivity must be a scalar in [0, 1]')
        if not np.isscalar(mirror_cleanliness) or not np.isfinite(mirror_cleanliness) or not 0 <= mirror_cleanliness <= 1:
            raise ValueError('Mirror cleanliness must be a scalar in [0, 1]')
        if not np.isscalar(receiver_absorptivity) or not np.isfinite(receiver_absorptivity) or not 0 <= receiver_absorptivity <= 1:
            raise ValueError('Receiver absorptivity must be a scalar in [0, 1]')
        if not np.isscalar(receiver_thermal_efficiency) or not np.isfinite(receiver_thermal_efficiency) or not 0 <= receiver_thermal_efficiency <= 1:
            raise ValueError('Receiver thermal efficiency must be a scalar in [0, 1]')
        envelope = np.array([m.width * m.height for m in self.mirrors])
        if reflective_area_m2 is not None and (not np.isfinite(reflective_area_m2)
                or reflective_area_m2 <= 0 or np.any(reflective_area_m2 > envelope + 1e-6 * envelope)):
            raise ValueError('Reflective area must be positive and no larger than the mirror envelope')
        rust_result = rust_field_efficiencies(self.mirrors, self.sun)
        result = pd.DataFrame(
            rust_result if rust_result is not None else
            [self.target_efficiencies(i) for i in range(len(self.mirrors))]
        )
        area = envelope if reflective_area_m2 is None else reflective_area_m2
        result['receiver_distance_m'] = self.receiver_distances
        result['eta_atmosphere'] = atmospheric_transmittance(self.receiver_distances, model=atmospheric_model)
        result['incident_normal_power_w'] = dni_w_m2 * area
        result['incident_cosine_power_w'] = result.incident_normal_power_w * result.eta_cosine
        result['geometric_usable_power_w'] = result.incident_cosine_power_w * result.eta_joint
        result['post_atmosphere_power_w'] = result.geometric_usable_power_w * result.eta_atmosphere
        result['cosine_loss_power_w'] = result.incident_normal_power_w - result.incident_cosine_power_w
        result['joint_loss_power_w'] = result.incident_cosine_power_w - result.geometric_usable_power_w
        result['atmospheric_loss_power_w'] = result.geometric_usable_power_w - result.post_atmosphere_power_w
        # Physical budget: reflection first, then air-path loss on that beam.
        # Previous ideal-reflector atmospheric columns remain diagnostic only.
        result['mirror_reflectivity'] = mirror_reflectivity
        result['mirror_cleanliness'] = mirror_cleanliness
        result['reflected_power_w'] = result.geometric_usable_power_w * mirror_reflectivity
        result['reflection_loss_power_w'] = result.geometric_usable_power_w - result.reflected_power_w
        result['clean_power_w'] = result.reflected_power_w * mirror_cleanliness
        result['cleanliness_loss_power_w'] = result.reflected_power_w - result.clean_power_w
        result['atmosphere_after_reflection_power_w'] = result.clean_power_w * result.eta_atmosphere
        result['atmospheric_after_reflection_loss_power_w'] = result.clean_power_w - result.atmosphere_after_reflection_power_w
        if receiver is not None and receivers is not None:
            raise ValueError('Pass either receiver or receivers, not both')
        result['tower_id'] = [mirror.tower_id for mirror in self.mirrors]
        if receiver is None and receivers is None:
            result['eta_intercept'] = 1.
        else:
            sigma = effective_angular_sigma(result.eta_cosine.to_numpy(), sun_mrad=sunshape_mrad,
                                            slope_mrad=slope_error_mrad, tracking_mrad=tracking_error_mrad)
            if receivers is None:
                result['eta_intercept'] = cylinder_interception(self.centres,
                    np.array([m.aim_point for m in self.mirrors]), sigma, receiver,
                    order=receiver_quadrature_order)
            else:
                unknown = sorted({m.tower_id for m in self.mirrors} - set(receivers))
                if unknown:
                    raise ValueError(f'No receiver defined for tower IDs: {", ".join(unknown)}')
                interception = np.empty(len(self.mirrors))
                tower_ids = np.array([m.tower_id for m in self.mirrors], dtype=object)
                aims = np.array([m.aim_point for m in self.mirrors])
                for tower_id, tower_receiver in receivers.items():
                    indices = np.flatnonzero(tower_ids == tower_id)
                    if len(indices):
                        interception[indices] = cylinder_interception(
                            self.centres[indices], aims[indices], sigma[indices], tower_receiver,
                            order=receiver_quadrature_order)
                result['eta_intercept'] = interception
        result['receiver_incident_power_w'] = result.atmosphere_after_reflection_power_w * result.eta_intercept
        result['interception_loss_power_w'] = result.atmosphere_after_reflection_power_w - result.receiver_incident_power_w
        result['receiver_absorptivity'] = receiver_absorptivity
        result['receiver_absorbed_power_w'] = result.receiver_incident_power_w * receiver_absorptivity
        result['receiver_absorption_loss_power_w'] = result.receiver_incident_power_w - result.receiver_absorbed_power_w
        result['receiver_thermal_efficiency'] = receiver_thermal_efficiency
        result['receiver_net_thermal_power_w'] = result.receiver_absorbed_power_w * receiver_thermal_efficiency
        result['receiver_thermal_loss_power_w'] = result.receiver_absorbed_power_w - result.receiver_net_thermal_power_w
        result['optical_upper_bound_power_w'] = result.receiver_incident_power_w
        result['absorbed_power_w'] = result.receiver_absorbed_power_w
        result['eta_optical_upper_bound'] = (result.eta_cosine * result.eta_joint * result.eta_atmosphere
                                             * mirror_reflectivity * mirror_cleanliness * result.eta_intercept)
        result['eta_absorbed'] = result.eta_optical_upper_bound * receiver_absorptivity
        result['eta_net_thermal'] = result.eta_absorbed * receiver_thermal_efficiency
        return result
