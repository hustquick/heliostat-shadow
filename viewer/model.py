"""One physical geometry source for the browser's 3D and 2D views."""
from collections import OrderedDict
from dataclasses import replace
import json
from pathlib import Path
import sys
import threading

import numpy as np
import pandas as pd
from shapely.affinity import affine_transform

from field import load_layout
from receiver import cylinder_interception, effective_angular_sigma
from simulation import PreparedField
from rust_core import field_efficiencies as rust_field_efficiencies
from solar import clear_sky_irradiance, sun_vector
from tower import config_with_towers, towers_from_config
from transform import projection_basis
from heliostat import mirror_normal
from optics import atmospheric_transmittance

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
FULL_FIELD_EFFICIENCY_LIMIT = 5000


def polygon_rings(geometry):
    """Preserve holes and disconnected components; ignore zero-area edges."""
    if geometry.is_empty:
        return []
    if geometry.geom_type == 'Polygon':
        return [{
            'outer': list(map(list, geometry.exterior.coords)),
            'holes': [list(map(list, ring.coords)) for ring in geometry.interiors]}]
    if hasattr(geometry, 'geoms'):
        return [part for child in geometry.geoms for part in polygon_rings(child)]
    return []


class ViewerModel:
    def __init__(self, root=ROOT, *, config_path=None, layout_path=None,
                 weather_path=None, source=None, note=None, clear_sky=False):
        config_path = Path(config_path or root/'data/gemasolar_config.json')
        layout_path = Path(layout_path or root/'data/processed/gemasolar_layout.csv')
        # Plant catalogues carry Chinese names and notes.  Windows defaults to
        # a legacy ANSI code page, so the application data must always be
        # decoded explicitly as UTF-8.
        self.config = config_with_towers(json.loads(config_path.read_text(encoding='utf-8')))
        self.towers = towers_from_config(self.config)
        self.tower_by_id = {tower.tower_id: tower for tower in self.towers}
        self.mirrors = load_layout(layout_path)
        self.dynamic_tower_assignment = self.config.get('tower_assignment_policy') == 'best_optical_at_time'
        allowed_towers = set(self.tower_by_id) | ({'auto'} if self.dynamic_tower_assignment else set())
        unknown_towers = sorted({mirror.tower_id for mirror in self.mirrors} - allowed_towers)
        if unknown_towers:
            raise ValueError(f'Layout references unknown tower IDs: {", ".join(unknown_towers)}')
        self.ids = [m.mirror_id for m in self.mirrors]
        self.index = {name: i for i, name in enumerate(self.ids)}
        count = len(self.mirrors)
        if count <= FULL_FIELD_EFFICIENCY_LIMIT:
            self.efficiency_indices = np.arange(count, dtype=int)
        else:
            self.efficiency_indices = np.array([], dtype=int)
        self.clear_sky = bool(clear_sky)
        self.source = source or 'Gemasolar 公开研究重建布局 · PVGIS-SARAH3 2023 历史卫星 DNI'
        self.note = note or '平坦地形、140 m 名义瞄准高度；非业主竣工测量。界面显示平面矩形几何，不显示接收器通量。'
        if self.clear_sky:
            year = int(self.config.get('year', 2023))
            index = pd.date_range(f'{year}-01-01', f'{year + 1}-01-01', freq='h',
                                  inclusive='left', tz='UTC')
            self.weather = pd.DataFrame({'time_utc': index, 'dni_w_m2': 0.})
        else:
            self.weather = pd.read_csv(weather_path or root/'data/processed/gemasolar_dni_2023.csv')
        self.weather.index = pd.to_datetime(self.weather.time_utc, utc=True)
        self.temperature_source = 'historical'
        if self.clear_sky:
            archive_path = root/'data/temperature_archive.json'
            archive = json.loads(archive_path.read_text(encoding='utf-8')) if archive_path.exists() else {'plants': []}
            profile = next((entry for entry in archive['plants']
                            if abs(entry['latitude'] - self.config['latitude']) < 1e-5
                            and abs(entry['longitude'] - self.config['longitude']) < 1e-5
                            and entry['year'] == year), None)
            if profile is not None and len(profile['temperature_series_c']) == len(self.weather):
                self.weather['temperature_c'] = profile['temperature_series_c']
                self.temperature_source = 'era5_land_reanalysis'
            else:
                # Explicit illustrative fallback for custom sites without an archive.
                latitude = float(self.config['latitude'])
                altitude = float(self.config.get('altitude_m', 0.))
                day = self.weather.index.dayofyear.to_numpy()
                solar_hour = self.weather.index.hour.to_numpy() + self.config['longitude'] / 15.
                peak_day = 200 if latitude >= 0 else 18
                seasonal = (5 + abs(latitude) * .3) * np.cos(2 * np.pi * (day - peak_day) / 365.25)
                diurnal = 5 * np.cos(2 * np.pi * (solar_hour - 15) / 24)
                self.weather['temperature_c'] = 27 - .52 * abs(latitude) - .006 * altitude + seasonal + diurnal
                self.temperature_source = 'illustrative_simulation'

        self.cache = OrderedDict()
        self.optical_efficiency_cache = OrderedDict()
        self.lock = threading.RLock()

    def metadata(self):
        local_noon = pd.Timestamp(
            f"{self.config.get('year', 2023)}-03-20 12:00",
            tz=self.config['timezone'],
        )
        offset_hours = local_noon.utcoffset().total_seconds() / 3600
        clock_noon = local_noon + pd.Timedelta(hours=offset_hours - float(self.config['longitude']) / 15)
        requested = clock_noon.round('h').tz_convert('UTC')
        nearest = self.weather.index[self.weather.index.get_indexer([requested], method='nearest')[0]]
        widths = np.asarray([mirror.width for mirror in self.mirrors], dtype=float)
        heights = np.asarray([mirror.height for mirror in self.mirrors], dtype=float)
        centre_heights = np.asarray([mirror.centre[2] for mirror in self.mirrors], dtype=float)
        uniform_dimensions = bool(
            np.allclose(widths, widths[0], rtol=0., atol=1e-9)
            and np.allclose(heights, heights[0], rtol=0., atol=1e-9)
        )
        heliostat_summary = dict(
            shape='rectangle',
            shape_label='矩形平面镜',
            uniform_dimensions=uniform_dimensions,
            width_range_m=[float(widths.min()), float(widths.max())],
            height_range_m=[float(heights.min()), float(heights.max())],
            centre_elevation_range_m=[float(centre_heights.min()), float(centre_heights.max())],
            ground_height_m=self.config.get('heliostat_pedestal_height_m'),
            ground_height_source_url=self.config.get('heliostat_pedestal_height_source_url'),
            ground_height_note=self.config.get('heliostat_pedestal_height_note'),
            reflective_area_m2=float(self.config['reflective_area_m2']),
            mount_types=sorted({mirror.mount_type for mirror in self.mirrors}),
        )
        coordinate_accuracy = self.config.get(
            'coordinate_accuracy', 'unverified_model_coordinate')
        coordinate_labels = {
            'exact_project_site': '精确项目场址',
            'published_project_site': '公开项目场址',
            'project_site_coordinate': '项目场址坐标',
            'project_site_approximate': '项目场址近似坐标',
            'regional_location_approximate': '区域级近似场址',
            'user_supplied': '用户输入坐标',
            'unverified_model_coordinate': '未核验模型坐标',
        }
        site_location = dict(
            latitude=float(self.config['latitude']),
            longitude=float(self.config['longitude']),
            datum=self.config.get('coordinate_datum', 'WGS 84'),
            accuracy=coordinate_accuracy,
            accuracy_label=coordinate_labels.get(coordinate_accuracy, coordinate_accuracy),
            source_name=self.config.get('coordinate_source_name'),
            source_url=self.config.get('coordinate_source_url'),
            note=self.config.get('coordinate_note'),
        )
        tower_payload = [tower.as_dict() | {
            'assigned_mirrors': sum(mirror.tower_id == tower.tower_id for mirror in self.mirrors),
        } for tower in self.towers]
        return dict(mirror_dimensions=[[m.width, m.height] for m in self.mirrors], mirror_ids=self.ids, timestamps=[t.isoformat() for t in self.weather.index],
                    timezone=self.config['timezone'], default_time=nearest.isoformat(),
                    towers=tower_payload, tower_count=len(tower_payload),
                    flexible_mirrors=sum(mirror.tower_id == 'auto' for mirror in self.mirrors),
                    tower_assignment_policy=self.config.get('tower_assignment_policy', 'fixed'),
                    receiver=tower_payload[0]['receiver'],
                    source=self.source, note=self.note, temperature_source=self.temperature_source,
                    default_mirror=self.ids[min(260, len(self.ids) - 1)],
                    heliostat_summary=heliostat_summary,
                    site_location=site_location,
                    efficiency_sample_count=len(self.efficiency_indices),
                    field_efficiencies_deferred=not len(self.efficiency_indices))

    def timestamp(self, value):
        try:
            ts = pd.Timestamp(value)
            if pd.isna(ts) or ts.tzinfo is None:
                raise ValueError()
            ts = ts.tz_convert('UTC')
            if ts not in self.weather.index:
                raise ValueError()
            return ts
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError('请选择数据集内带时区的小时采样时间') from exc

    def frame(self, value):
        ts = self.timestamp(value)
        with self.lock:
            if ts in self.cache:
                self.cache.move_to_end(ts)
                return self.cache[ts]
            row = self.weather.loc[ts]
            c = self.config
            dni = float(row.dni_w_m2)
            if self.clear_sky:
                dni = float(clear_sky_irradiance(
                    c['latitude'], c['longitude'], ts, altitude=c['altitude_m'],
                    temperature=float(row.temperature_c))['dni'].iloc[0])
            sun = sun_vector(c['latitude'], c['longitude'], ts, altitude=c['altitude_m'],
                             temperature=float(row.temperature_c))
            result = dict(timestamp=ts.isoformat(), local_time=ts.tz_convert(c['timezone']).isoformat(),
                          daylight=sun.is_daylight, dni=dni,
                          temperature=float(row.temperature_c), temperature_source=self.temperature_source, elevation=float(sun.solar_pos.elevation.iloc[0]),
                          sun=sun.sun_to_sky.tolist())
            prepared = None
            if sun.is_daylight:
                mirrors = self._mirrors_for_sun(sun.sun_to_sky)
                result["tower_assignment"] = getattr(self, "_tower_assignment_report", {"strategy":"independent"})
                prepared = PreparedField.from_mirrors(mirrors, sun.sun_to_sky)
                eta_joint = [None] * len(self.mirrors)
                for i in self.efficiency_indices:
                    eta_joint[int(i)] = prepared.target_efficiencies(int(i))['eta_joint']
                result.update(vertices=prepared.vertices.tolist(),
                              tower_ids=[mirror.tower_id for mirror in prepared.mirrors],
                              eta_joint=eta_joint,
                              efficiency_sample_count=len(self.efficiency_indices))
            else:
                # No invented tracking or stow orientation at night.
                result.update(centres=[list(m.centre) for m in self.mirrors], vertices=[], eta_joint=None)
            self.cache[ts] = (prepared, result)
            while len(self.cache) > 3:
                self.cache.popitem(last=False)
            return prepared, result

    def _mirrors_for_sun(self, sun_to_sky):
        """Assign each flexible mirror to the tower with maximum received power."""
        if not self.dynamic_tower_assignment or not any(m.tower_id == 'auto' for m in self.mirrors):
            return self.mirrors
        self._tower_assignment_report = {'strategy':'independent'}
        return self._independent_mirrors_for_sun(sun_to_sky)

    def _independent_mirrors_for_sun(self, sun_to_sky):
        proxy = []
        for mirror in self.mirrors:
            if mirror.tower_id != 'auto':
                proxy.append(mirror)
                continue
            centre = np.asarray(mirror.centre)
            best = None
            for tower in self.towers:
                candidate = self._mirror_for_tower(mirror, tower)
                reflected = np.asarray(candidate.aim_point) - centre
                distance = np.linalg.norm(reflected)
                reflected /= distance
                cosine = float(mirror_normal(sun_to_sky, reflected) @ sun_to_sky)
                score = cosine * float(atmospheric_transmittance(distance))
                choice = (score, candidate)
                if best is None or choice[0] > best[0]:
                    best = choice
            proxy.append(best[1])

        flexible = np.array([i for i, mirror in enumerate(self.mirrors)
                             if mirror.tower_id == 'auto'], dtype=int)
        alternatives = []
        for tower_index, tower in enumerate(self.towers):
            candidates = [self._mirror_for_tower(self.mirrors[i], tower) for i in flexible]
            centres = np.array([mirror.centre for mirror in candidates])
            aims = np.array([mirror.aim_point for mirror in candidates])
            distance = np.linalg.norm(aims - centres, axis=1)
            reflected = (aims - centres) / distance[:, None]
            cosine = np.array([mirror_normal(sun_to_sky, direction) @ sun_to_sky
                               for direction in reflected])
            sigma = effective_angular_sigma(
                cosine, sun_mrad=float(self.config.get('sunshape_mrad', 2.51)),
                slope_mrad=float(self.config.get('slope_error_mrad', 2.6)),
                tracking_mrad=float(self.config.get('tracking_error_mrad', 2.1)))
            intercept = cylinder_interception(
                centres, aims, sigma, tower.receiver,
                order=int(self.config.get('receiver_quadrature_order', 64)))
            alternatives.append((candidates,
                cosine * atmospheric_transmittance(distance) * intercept))

        # Hold all other mirrors at the same proxy pose, then compare the two
        # complete optical chains independently for every flexible mirror.
        # DNI, mirror area, reflectivity and cleanliness are common factors for
        # one mirror's tower candidates, so omitting them preserves the argmax.
        scores = np.empty((len(self.towers), len(flexible)))
        field = PreparedField.from_mirrors(proxy, sun_to_sky)
        for tower_index, (candidates, static_factor) in enumerate(alternatives):
            rows = rust_field_efficiencies(
                proxy,
                sun_to_sky,
                target_indices=flexible.tolist(),
                target_overrides=candidates,
            )
            if rows is None:
                joint = np.array([
                    field.target_efficiencies(int(i), target_mirror=mirror)['eta_joint']
                    for i, mirror in zip(flexible, candidates)])
            else:
                joint = np.array([row['eta_joint'] for row in rows])
            scores[tower_index] = static_factor * joint
        choices = np.argmax(scores, axis=0)
        resolved = list(proxy)
        for position, mirror_index in enumerate(flexible):
            resolved[int(mirror_index)] = alternatives[int(choices[position])][0][position]
        self._last_tower_choice_scores = {
            self.mirrors[int(mirror_index)].mirror_id: {
                tower.tower_id: float(scores[tower_index, position])
                for tower_index, tower in enumerate(self.towers)
            }
            for position, mirror_index in enumerate(flexible)
        }
        return tuple(resolved)

    @staticmethod
    def _mirror_for_tower(mirror, tower):
        centre = np.asarray(mirror.centre)
        delta = centre[:2] - np.asarray(tower.receiver.centre[:2])
        radial = np.linalg.norm(delta)
        if radial <= tower.receiver.radius:
            raise ValueError('Flexible mirror lies inside a receiver cylinder')
        aim_xy = np.asarray(tower.receiver.centre[:2]) + tower.receiver.radius * delta / radial
        aim = (float(aim_xy[0]), float(aim_xy[1]), float(tower.receiver.centre[2]))
        return replace(mirror, tower_id=tower.tower_id, aim_point=aim)

    def efficiencies(self, value):
        """Return exact per-mirror total optical efficiencies for 3D coloring."""
        ts = self.timestamp(value)
        with self.lock:
            if ts in self.optical_efficiency_cache:
                self.optical_efficiency_cache.move_to_end(ts)
                return self.optical_efficiency_cache[ts]
            field, frame = self.frame(ts)
            if field is None:
                raise ValueError('太阳在地平线以下，不能计算光学效率')
            c = self.config
            receivers = {tower.tower_id: tower.receiver for tower in self.towers}
            result = field.evaluate(
                1., reflective_area_m2=float(c['reflective_area_m2']),
                atmospheric_model=c.get('atmospheric_model', 'clear_air_40km'),
                mirror_reflectivity=float(c.get('mirror_reflectivity', 1.)),
                mirror_cleanliness=float(c.get('mirror_cleanliness', 1.)),
                receivers=receivers,
                sunshape_mrad=float(c.get('sunshape_mrad', 2.51)),
                slope_error_mrad=float(c.get('slope_error_mrad', 2.6)),
                tracking_error_mrad=float(c.get('tracking_error_mrad', 2.1)),
                receiver_quadrature_order=int(c.get('receiver_quadrature_order', 64)),
            )
            values = (result.receiver_incident_power_w / result.incident_normal_power_w).to_numpy()
            payload = dict(
                timestamp=frame['timestamp'], metric='eta_optical',
                label='总光学效率', values=values.tolist(),
                factors={name: result[name].to_numpy().tolist() for name in (
                    'eta_cosine', 'eta_shadow', 'eta_blocking', 'eta_joint',
                    'mirror_reflectivity', 'mirror_cleanliness',
                    'eta_atmosphere', 'eta_intercept')},
                minimum=float(values.min()), maximum=float(values.max()),
                mean=float(values.mean()), mirror_count=len(values),
                formula='余弦 × 联合阴影遮挡 × 反射率 × 清洁度 × 沿程透过率 × 接收器截获率',
                tower_ids=result.tower_id.tolist(),
            )
            self.optical_efficiency_cache[ts] = payload
            while len(self.optical_efficiency_cache) > 3:
                self.optical_efficiency_cache.popitem(last=False)
            return payload

    def target(self, value, mirror_id):
        if mirror_id not in self.index:
            raise ValueError('未知镜面编号')
        field, frame = self.frame(value)
        if field is None:
            return dict(timestamp=frame['timestamp'], mirror_id=mirror_id, daylight=False)
        i = self.index[mirror_id]
        d = field.target_projection_diagnostics(i)
        target = d['target_polygon']
        shadow, block = d['shadow']['union_polygon'], d['blocking']['union_polygon']
        geometries = dict(target=target, shadow=shadow, blocking=block,
                          overlap=shadow.intersection(block),
                          shadow_only=shadow.difference(block), blocking_only=block.difference(shadow),
                          visible=target.difference(shadow.union(block)))
        plane = d['target_basis']
        views = {}
        # Map the SAME clipped masks into light-normal frames. No new tracing.
        for name, direction in [('mirror', field.normals[i]), ('shadow', field.sun),
                                ('blocking', field.reflected[i])]:
            basis = plane if name == 'mirror' else projection_basis(direction)[:2]
            matrix = basis @ plane.T
            coefficients = [*matrix[0], *matrix[1], 0, 0]
            projected = {k: affine_transform(g, coefficients) for k, g in geometries.items()}
            views[name] = dict(polygons={k: polygon_rings(g) for k, g in projected.items()},
                               areas={k: float(g.area) for k, g in projected.items()}, basis=basis.tolist(),
                               contributors={m: [dict(mirror_id=id, polygons=polygon_rings(affine_transform(poly, coefficients)))
                                   for id, poly in zip(d[m]['occluder_ids'], d[m]['polygons'])]
                                   for m in ('shadow', 'blocking')})
        modes = {name: {k: d[name][k] for k in ('candidate_ids','occluder_ids','decisions')}
                 for name in ('shadow','blocking')}
        efficiencies = dict(eta_shadow=1-shadow.area/target.area, eta_blocking=1-block.area/target.area,
                            eta_joint=geometries['visible'].area/target.area,
                            eta_cosine=float(field.normals[i] @ field.sun))
        c = self.config
        mirror = field.mirrors[i]
        distance = np.linalg.norm(np.asarray(mirror.aim_point) - field.centres[i])
        efficiencies['eta_atmosphere'] = float(atmospheric_transmittance(distance, model=c.get('atmospheric_model', 'clear_air_40km')))
        sigma = effective_angular_sigma(efficiencies['eta_cosine'], sun_mrad=c.get('sunshape_mrad',2.51), slope_mrad=c.get('slope_error_mrad',2.6), tracking_mrad=c.get('tracking_error_mrad',2.1))
        efficiencies['eta_intercept'] = float(cylinder_interception(np.asarray([mirror.centre]), np.asarray([mirror.aim_point]), np.asarray([sigma]), self.tower_by_id[mirror.tower_id].receiver, order=int(c.get('receiver_quadrature_order',64)))[0])
        efficiencies['mirror_reflectivity'] = float(c.get('mirror_reflectivity',1.))
        efficiencies['mirror_cleanliness'] = float(c.get('mirror_cleanliness',1.))
        efficiencies['eta_optical'] = float(np.prod([efficiencies[k] for k in ('eta_cosine','eta_joint','eta_atmosphere','eta_intercept','mirror_reflectivity','mirror_cleanliness')]))
        return dict(timestamp=frame['timestamp'], mirror_id=mirror_id, daylight=True,
                    tower_id=field.mirrors[i].tower_id,
                    centre=field.centres[i].tolist(), normal=field.normals[i].tolist(),
                    reflected=field.reflected[i].tolist(), aim=list(field.mirrors[i].aim_point),
                    views=views, modes=modes, efficiencies=efficiencies,
                    envelope_area_m2=float(target.area), reflective_area_m2=self.config['reflective_area_m2'],
                    total_other_mirrors=len(self.mirrors)-1)
