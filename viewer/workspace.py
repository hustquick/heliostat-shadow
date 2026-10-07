"""Persistent plant selection, CSV import and layout rearrangement for the app."""

from io import StringIO
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from threading import RLock

import numpy as np
import pandas as pd

from heliostat import Heliostat
from layouts import (rectangular_hex_field, annular_hex_field, campo_radial_stagger_candidates, deform_radial_curves,
                     dual_tower_overlap_field,
                     rescale_radial_extent,
                     select_by_score, select_by_score_with_spacing_and_north_fraction,
                     value_field_spiral_candidates)
from optics import atmospheric_transmittance
from solar import sun_vector
from viewer.model import ROOT, ViewerModel
from tower import config_with_towers


CATALOG_MODEL_VERSION = 4


def default_user_root() -> Path:
    """Return the native per-user application data directory on each OS."""
    override = os.environ.get('HELIOSTAT_VIEWER_DATA')
    if override:
        return Path(override)
    if sys.platform == 'darwin':
        return Path.home()/'Library/Application Support/Heliostat Viewer'
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData/Local'))/'Heliostat Viewer'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))/'heliostat-viewer'


def _safe_id(name: str) -> str:
    value = re.sub(r'[^A-Za-z0-9_-]+', '-', name.strip()).strip('-').lower()
    return (value or 'plant')[:48]


class ViewerWorkspace:
    def __init__(self, root=ROOT, user_root=None):
        self.root = Path(root)
        self.user_root = Path(user_root or default_user_root())
        self.user_root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self._models = {}
        self._plants = self._discover()
        active_file = self.user_root/'active.txt'
        requested = active_file.read_text(encoding='utf-8').strip() if active_file.exists() else 'gemasolar'
        self.active_id = requested if requested in self._plants else 'gemasolar'

    def _discover(self):
        config = self.root/'data/gemasolar_config.json'
        weather = self.root/'data/processed/gemasolar_dni_2023.csv'
        plants = {
            'gemasolar': dict(name='Gemasolar（西班牙）', config=config,
                layout=self.root/'data/processed/gemasolar_layout.csv', weather=weather,
                builtin=True, clear_sky=False),
        }
        catalog_path = self.root/'data/power_tower_catalog.json'
        if catalog_path.exists():
            for item in json.loads(catalog_path.read_text(encoding='utf-8')):
                plants[item['id']] = dict(name=item['name'], builtin=True, clear_sky=True,
                                           catalog=item, config=None, layout=None, weather=None)
        for metadata in self.user_root.glob('*/plant.json'):
            try:
                data = json.loads(metadata.read_text(encoding='utf-8'))
                folder = metadata.parent
                plants[data['id']] = dict(name=data['name'], config=folder/'config.json',
                    layout=folder/'layout.csv', weather=folder/'weather.csv' if (folder/'weather.csv').exists() else None,
                    builtin=False, clear_sky=bool(data.get('clear_sky', True)))
            except (OSError, KeyError, ValueError, json.JSONDecodeError):
                continue
        # Generated layouts reference the original environment rather than duplicating it.
        for plant in plants.values():
            if plant['builtin']:
                continue
            config = json.loads(plant['config'].read_text())
            source = plants.get(config.get('source_plant_id'))
            if source is not None:
                plant['weather'] = source['weather']
                plant['clear_sky'] = source['clear_sky']
        return plants

    @property
    def active(self):
        return self._model_for(self.active_id)

    def _environment_id(self, plant_id):
        visited = set()
        while plant_id not in visited:
            visited.add(plant_id)
            plant = self._plants[plant_id]
            if plant['builtin']:
                return plant_id
            config = json.loads(plant['config'].read_text())
            source_id = config.get('source_plant_id')
            if source_id in ('gemasolar-campo', 'gemasolar-curved', 'gemasolar-free'):
                source_id = 'gemasolar'
            if not source_id and plant['name'].endswith(('Campo重排', '非圆曲线重排', '自由排布重排')):
                candidates = [(key, item) for key, item in self._plants.items()
                              if item['builtin'] and plant['name'].startswith(item['name'] + ' · ')]
                if candidates:
                    source_id = max(candidates, key=lambda pair: len(pair[1]['name']))[0]
                elif plant['name'].startswith(('Gemasolar · ', 'Gemasolar 重建参考 · ')):
                    source_id = 'gemasolar'
            if source_id not in self._plants or source_id == plant_id:
                return plant_id
            plant_id = source_id
        return plant_id

    def _model_for(self, plant_id):
        with self.lock:
            if plant_id not in self._models:
                p = self._plants[plant_id]
                if p.get('catalog'):
                    self._materialize_catalog(p)
                catalog_note = None
                if p.get('catalog'):
                    catalog_note = p['catalog'].get(
                        'model_note',
                        '镜位是参数化重建；接收器公开尺寸缺失时使用逐电厂建模值。')
                source_id = self._environment_id(plant_id)
                environment = self._model_for(source_id) if source_id in self._plants and source_id != plant_id else None
                self._models[plant_id] = ViewerModel(
                    self.root, config_path=p['config'], layout_path=p['layout'],
                    weather_path=p['weather'], clear_sky=p['clear_sky'], environment_model=environment,
                    source=('Gemasolar 公开地图提取的 2,650 面定日镜参考布局 · PVGIS-SARAH3 2023 历史卫星 DNI'
                            if plant_id == 'gemasolar' else
                            p['name'] + (' · pvlib 晴空辐照度' if p['clear_sky'] else ' · PVGIS-SARAH3 2023 DNI')),
                    note=(f'参数化重建场用于方法比较；镜位不是电厂竣工坐标。{catalog_note}' if p.get('catalog') else
                          '用户导入/重排布局；坐标与参数由用户负责核验。' if not p['builtin'] else None))
            return self._models[plant_id]

    def metadata(self):
        data = self.active.metadata()
        plant = self._plants[self.active_id]
        catalog = plant.get('catalog')
        data.update(active_plant=self.active_id, plant_name=plant['name'],
                    environment_plant_id=self._environment_id(self.active_id),
                    plant_details=catalog,
                    layout_status=('公开研究重建坐标' if self.active_id == 'gemasolar' else
                                   '参数化重建场，非实测坐标' if catalog else
                                   '用户导入或重排坐标'),
                    reported_mirrors=catalog.get('reported_heliostats') if catalog else len(data['mirror_ids']),
                    plants=[dict(id=k, name=v['name'], builtin=v['builtin'],
                                 catalog=bool(v.get('catalog')),
                                 environment_plant_id=self._environment_id(k),
                                 design_method=None if v['builtin'] else json.loads(v['config'].read_text()).get('design_method'))
                            for k, v in self._plants.items()])
        return data

    def _materialize_catalog(self, plant):
        item = plant['catalog']
        folder = self.user_root/'catalog-cache'/item['id']
        layout, config = folder/'layout.csv', folder/'config.json'
        count = int(item.get('reported_heliostats') or 2650)
        tower_height = float(item.get('tower_height_m') or item['model_tower_height_m'])
        fingerprint = hashlib.sha256(json.dumps(
            {'model_version': CATALOG_MODEL_VERSION, 'simulation_year': 2025, 'plant': item},
            ensure_ascii=False, sort_keys=True,
        ).encode()).hexdigest()
        needs_build = not layout.exists() or not config.exists()
        if not needs_build:
            try:
                cached = json.loads(config.read_text(encoding='utf-8'))
                needs_build = (len(pd.read_csv(layout, usecols=['mirror_id'])) != count or
                               cached.get('catalog_model_fingerprint') != fingerprint)
            except (OSError, ValueError, KeyError, pd.errors.ParserError):
                needs_build = True
        if needs_build:
            folder.mkdir(parents=True, exist_ok=True)
            area = float(item.get('heliostat_area_m2') or item['model_heliostat_area_m2'])
            width = float(item.get('model_heliostat_width_m', np.sqrt(area*1.25)))
            height = float(item.get('model_heliostat_height_m', np.sqrt(area/1.25)))
            receiver_radius = float(item['model_receiver_radius_m'])
            receiver_height = float(item['model_receiver_height_m'])
            if item.get('model_layout_type') == 'dual_tower_overlap':
                model_towers = item['model_towers']
                by_id = {tower['id']: tower for tower in model_towers}
                east, west = by_id['east'], by_id['west']
                mirrors = dual_tower_overlap_field(
                    count=count, mirror_width_m=width, mirror_height_m=height,
                    east_tower_xy=east['base'][:2], west_tower_xy=west['base'][:2],
                    receiver_height_m=float(east['receiver']['centre'][2]),
                    receiver_radius_m=float(east['receiver']['radius']),
                    field_radius_m=float(item['model_field_radius_m']),
                    inner_radius_m=float(item['model_field_inner_radius_m']),
                    spacing_m=float(item['model_mirror_spacing_m']),
                    centre_elevation_m=float(item.get('heliostat_pedestal_height_m', 0.)),
                )
            elif item.get('model_layout_type') == 'rectangular_hex':
                mirrors = rectangular_hex_field(
                    count=count, mirror_width_m=width, mirror_height_m=height,
                    tower_height_m=tower_height, receiver_radius_m=receiver_radius,
                    field_width_m=float(item['model_field_width_m']),
                    field_height_m=float(item['model_field_height_m']),
                    inner_radius_m=float(item['model_field_inner_radius_m']),
                    spacing_m=float(item['model_mirror_spacing_m']))
            elif item.get('model_layout_type') == 'annular_hex':
                mirrors = annular_hex_field(
                    count=count, mirror_width_m=width, mirror_height_m=height,
                    tower_height_m=tower_height,
                    receiver_radius_m=receiver_radius,
                    field_radius_m=float(item['model_field_outer_radius_m']),
                    inner_radius_m=float(item['model_field_inner_radius_m']),
                    spacing_m=float(item['model_mirror_spacing_m']),
                    centre_elevation_m=float(item.get('heliostat_pedestal_height_m', 0.)),
                )
            else:
                mirrors = campo_radial_stagger_candidates(
                    mirror_width_m=width, mirror_height_m=height,
                    tower_height_m=tower_height, receiver_radius_m=receiver_radius,
                    radial_increments=(np.cos(np.pi/6), .9, 1.8), candidate_count=count,
                    first_row_count=int(item.get('model_first_row_count', min(46, count))),
                    separation_m=1.)
                if item.get('model_field_outer_radius_m') is not None:
                    mirrors = rescale_radial_extent(
                        mirrors, outer_radius_m=float(item['model_field_outer_radius_m']),
                        inner_radius_m=(float(item['model_field_inner_radius_m'])
                                        if item.get('model_field_inner_radius_m') is not None else None),
                        tower_height_m=tower_height,
                        receiver_radius_m=receiver_radius)
            pd.DataFrame([dict(mirror_id=m.mirror_id, x=m.centre[0], y=m.centre[1], z=m.centre[2],
                width=m.width, height=m.height, aim_x=m.aim_point[0], aim_y=m.aim_point[1],
                aim_z=m.aim_point[2], roll_deg=m.roll_deg, mount_type=m.mount_type,
                tower_id=m.tower_id)
                for m in mirrors]).to_csv(layout, index=False)
            base = json.loads((self.root/'data/gemasolar_config.json').read_text(encoding='utf-8'))
            # Catalog plants define their own primary tower instead of
            # inheriting Gemasolar's v1.1 tower array from the base config.
            base.pop('towers', None)
            base.update(latitude=item['latitude'], longitude=item['longitude'], timezone=item['timezone'],
                        altitude_m=float(item.get('altitude_m', 0.)),
                        coordinate_datum=item.get('coordinate_datum', 'WGS 84'),
                        coordinate_accuracy=item.get(
                            'coordinate_accuracy', 'project_site_approximate'),
                        coordinate_source_name=item.get('coordinate_source_name'),
                        coordinate_source_url=item.get(
                            'coordinate_source_url', item['source_url']),
                        coordinate_note=item.get('coordinate_note'),
                        expected_mirrors=count, optical_height_m=tower_height,
                        receiver_radius_m=receiver_radius,
                        receiver_height_m=receiver_height,
                        mirror_width_m=width, mirror_height_m=height,
                        reflective_area_m2=area, year=2025,
                        heliostat_reported_ground_height_m=item.get('heliostat_reported_ground_height_m',item.get('heliostat_pedestal_height_m')),
                        heliostat_reported_ground_height_source_url=item.get('heliostat_reported_ground_height_source_url',item.get('heliostat_pedestal_height_source_url')),
                        heliostat_reported_ground_height_note=item.get('heliostat_reported_ground_height_note',item.get('heliostat_pedestal_height_note')),
                        heliostat_pedestal_height_m=item.get('heliostat_pedestal_height_m'),
                        heliostat_pedestal_height_source_url=item.get(
                            'heliostat_pedestal_height_source_url'),
                        heliostat_pedestal_height_note=item.get(
                            'heliostat_pedestal_height_note'),
                        mirror_reflectivity=float(item.get('model_mirror_reflectivity', .88)),
                        mirror_cleanliness=float(item.get('model_mirror_cleanliness', .95)),
                        slope_error_mrad=float(item.get('model_slope_error_mrad', 2.6)),
                        tracking_error_mrad=float(item.get('model_tracking_error_mrad', 2.1)),
                        receiver_absorptivity=float(item.get('model_receiver_absorptivity', .93)),
                        towers=item.get('model_towers'),
                        tower_assignment_policy=item.get('tower_assignment_policy', 'fixed'),
                        catalog_model_version=CATALOG_MODEL_VERSION,
                        catalog_model_fingerprint=fingerprint,
                        parameter_notes={
                            'catalog': item.get('model_note', '逐电厂参数化重建场。'),
                            'source': item['source_url'],
                            'receiver': item.get('model_receiver_note',
                                '接收器尺寸为逐电厂建模值；未声明为实测尺寸。'),
                            'layout': item.get('model_layout_note',
                                'Campo 径向交错参数化坐标，不是竣工测量镜位。'),
                        })
            config.write_text(json.dumps(config_with_towers(base), ensure_ascii=False, indent=2)+'\n',
                              encoding='utf-8')
        plant['layout'], plant['config'] = layout, config

    def frame(self, value):
        return self.active.frame(value)

    def target(self, value, mirror_id):
        return self.active.target(value, mirror_id)

    def efficiencies(self, value):
        return self.active.efficiencies(value)

    def select(self, plant_id):
        if plant_id not in self._plants:
            raise ValueError('未知电厂或布局')
        self.active_id = plant_id
        (self.user_root/'active.txt').write_text(plant_id, encoding='utf-8')
        return self.metadata()

    def import_csv(self, payload):
        source_id = payload.get('plant_id', self.active_id)
        if source_id not in self._plants:
            raise ValueError('未知来源电厂')
        source_model = self._model_for(source_id)
        if payload.get('plant_id'):
            payload = dict(source_model.config, **payload)
            payload['_bind_source_environment'] = True
        name = str(payload.get('name', '')).strip()
        if not name:
            raise ValueError('请填写电厂名称')
        try:
            data = pd.read_csv(StringIO(payload['csv']))
        except Exception as exc:
            raise ValueError('CSV 无法读取') from exc
        data.columns = [str(c).strip().lower() for c in data.columns]
        if not {'x', 'y'} <= set(data.columns) or not 1 <= len(data) <= 100000:
            raise ValueError('CSV 必须包含 x、y，镜面数量须为 1–100000')
        base = dict(source_model.config)
        numeric = {'latitude': 37.562, 'longitude': -5.33, 'altitude_m': 0.,
                   'optical_height_m': 140., 'receiver_radius_m': 4.,
                   'receiver_height_m': 10.5, 'mirror_width_m': 12.305,
                   'mirror_height_m': 9.752}
        for key, default in numeric.items():
            try: base[key] = float(payload.get(key, default))
            except (TypeError, ValueError) as exc: raise ValueError(f'{key} 必须是数字') from exc
        base['timezone'] = str(payload.get('timezone', 'UTC')).strip() or 'UTC'
        base.update(
            coordinate_datum='WGS 84',
            coordinate_accuracy='user_supplied',
            coordinate_source_name='用户输入',
            coordinate_source_url=None,
            coordinate_note='经纬度由用户导入镜场时填写，应用未核验该场址。',
        )
        base['design_method'] = payload.get('design_method', 'imported')
        base['year'] = int(payload.get('year', 2023))
        try: pd.Timestamp(f"{base['year']}-01-01", tz=base['timezone'])
        except Exception as exc: raise ValueError('时区名称无效') from exc
        width, height = base['mirror_width_m'], base['mirror_height_m']
        if width <= 0 or height <= 0 or base['optical_height_m'] <= 0:
            raise ValueError('镜面尺寸和塔高必须为正数')
        out = pd.DataFrame(index=data.index)
        out['mirror_id'] = data.get('mirror_id', pd.Series([f'U{i+1}' for i in range(len(data))]))
        for column, default in [('x', None), ('y', None), ('z', 0.),
                                ('width', width), ('height', height), ('roll_deg', 0.)]:
            out[column] = pd.to_numeric(data[column] if column in data else default, errors='coerce')
        radius = np.hypot(out.x, out.y)
        safe = np.where(radius > 0, radius, 1.)
        out['aim_x'] = pd.to_numeric(data['aim_x'], errors='coerce') if 'aim_x' in data else base['receiver_radius_m']*out.x/safe
        out['aim_y'] = pd.to_numeric(data['aim_y'], errors='coerce') if 'aim_y' in data else base['receiver_radius_m']*out.y/safe
        out['aim_z'] = pd.to_numeric(data['aim_z'], errors='coerce') if 'aim_z' in data else base['optical_height_m']
        out['mount_type'] = data.get('mount_type', 'azimuth_elevation')
        out['tower_id'] = data.get('tower_id', 'tower-1')
        if not np.isfinite(out[['x','y','z','width','height','aim_x','aim_y','aim_z','roll_deg']]).all().all():
            raise ValueError('CSV 含空值或非有限数值')
        if out.mirror_id.astype(str).duplicated().any():
            raise ValueError('mirror_id 必须唯一')
        for row in out.itertuples(index=False):
            Heliostat(str(row.mirror_id), (row.x,row.y,row.z), row.width,row.height,
                      (row.aim_x,row.aim_y,row.aim_z), row.roll_deg,row.mount_type,row.tower_id)
        base['reflective_area_m2'] = float(payload.get('reflective_area_m2', width*height))
        base.pop('towers', None)
        base = config_with_towers(base)
        plant_id = self._unique_id(_safe_id(name))
        folder = self.user_root/plant_id
        folder.mkdir(parents=True)
        if payload.get('_bind_source_environment'):
            source_config = source_model.config
            environment_id = self._environment_id(source_id)
            base.update(source_plant_id=environment_id,
                        source_plant_name=self._plants[environment_id]['name'])
            for key in ('coordinate_datum', 'coordinate_accuracy', 'coordinate_source_name',
                        'coordinate_source_url', 'coordinate_note'):
                if key in source_config:
                    base[key] = source_config[key]
        out.to_csv(folder/'layout.csv', index=False)
        (folder/'config.json').write_text(json.dumps(base, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        (folder/'plant.json').write_text(json.dumps({'id': plant_id, 'name': name,
            'clear_sky': source_model.clear_sky if payload.get('_bind_source_environment') else True}, ensure_ascii=False, indent=2)+'\n',
                                          encoding='utf-8')
        self._plants = self._discover()
        return self.select(plant_id)

    def _unique_id(self, stem):
        candidate, number = stem, 2
        while candidate in self._plants:
            candidate, number = f'{stem}-{number}', number + 1
        return candidate

    def rearrange(self, payload):
        source_id = payload.get('plant_id', self.active_id)
        if source_id not in self._plants:
            raise ValueError('未知来源电厂')
        model, scheme = self._model_for(source_id), str(payload.get('scheme', 'campo'))
        count = len(model.mirrors)
        c = model.config
        width = float(np.median([m.width for m in model.mirrors]))
        height = float(np.median([m.height for m in model.mirrors]))
        factor = max(count, int(np.ceil(count * 1.46)))
        if count < 6:
            raise ValueError('镜场重排至少需要 6 面镜')
        separation = float(payload.get('separation_m', 1.0 if scheme == 'campo' else 3.0))
        base = campo_radial_stagger_candidates(mirror_width_m=width, mirror_height_m=height,
            tower_height_m=c['optical_height_m'], receiver_radius_m=c['receiver_radius_m'],
            radial_increments=(np.cos(np.pi/6), float(payload.get('dr2', .9)),
                               float(payload.get('dr3', 1.8))),
            first_row_count=min(46, count), candidate_count=factor, separation_m=separation)
        if scheme == 'curved':
            base = deform_radial_curves(base,
                north_south_m=float(payload.get('north_south_m', -25)),
                ellipticity_m=float(payload.get('ellipticity_m', 15)),
                tower_height_m=c['optical_height_m'], receiver_radius_m=c['receiver_radius_m'])
        suns = [sun_vector(c['latitude'], c['longitude'],
                f"{c.get('year', 2023)}-{month:02d}-21 12:00", c['timezone'],
                altitude=c['altitude_m']).sun_to_sky for month in (3,6,9,12)]
        if scheme in ('campo', 'curved'):
            mirrors = select_by_score(base, self._scores(base, suns), count)
        elif scheme == 'free':
            spacing = float(payload.get('spacing_m', np.hypot(width, height) + 1))
            boundary = float(payload.get('boundary_radius_m',
                max(3*c['optical_height_m'], 2.1*np.sqrt(count/np.pi)*spacing)))
            base = value_field_spiral_candidates(candidate_count=max(count*5, count+500),
                inner_radius_m=float(payload.get('inner_radius_m', 80)), boundary_radius_m=boundary,
                north_south=float(payload.get('boundary_north_south', 0)),
                ellipticity=float(payload.get('boundary_ellipticity', 0)),
                mirror_width_m=width, mirror_height_m=height,
                tower_height_m=c['optical_height_m'], receiver_radius_m=c['receiver_radius_m'])
            mirrors = select_by_score_with_spacing_and_north_fraction(
                base, self._scores(base, suns), count, spacing,
                float(payload.get('north_fraction', .6)))
        else:
            raise ValueError('未知重排方式')
        name = f"{self._plants[source_id]['name']} · {dict(campo='Campo', curved='非圆曲线', free='自由排布')[scheme]}重排"
        csv = pd.DataFrame([dict(mirror_id=m.mirror_id, x=m.centre[0], y=m.centre[1], z=m.centre[2],
            width=m.width, height=m.height, aim_x=m.aim_point[0], aim_y=m.aim_point[1],
            aim_z=m.aim_point[2], roll_deg=m.roll_deg, mount_type=m.mount_type,
            tower_id=m.tower_id) for m in mirrors]).to_csv(index=False)
        imported = dict(payload, plant_id=source_id, name=name, csv=csv, **{k: c[k] for k in
            ('latitude','longitude','altitude_m','optical_height_m','receiver_radius_m','receiver_height_m','timezone','year')})
        imported.update(_bind_source_environment=True, design_method=scheme, mirror_width_m=width, mirror_height_m=height,
                        reflective_area_m2=min(float(c.get('reflective_area_m2', width*height)), width*height))
        return self.import_csv(imported)

    @staticmethod
    def _scores(mirrors, suns):
        centres = np.array([m.centre for m in mirrors]); aims = np.array([m.aim_point for m in mirrors])
        reflected = aims-centres; distance=np.linalg.norm(reflected,axis=1); reflected/=distance[:,None]
        score=np.zeros(len(mirrors))
        for sun in suns:
            normal=reflected+sun; normal/=np.linalg.norm(normal,axis=1)[:,None]
            score += np.maximum(0, normal@sun)
        return score/len(suns)*atmospheric_transmittance(distance)

    def analyze_instant(self, payload):
        from rust_core import analyze_instant
        with self.lock:
            model = self.active
            values = dict(payload, plant_id=self.active_id)
            return analyze_instant(model.mirrors, model.config, values)

    def optical_energy(self, payload):
        from scripts.annual_energy import Sample, evaluate_energy
        from scripts.optimize_ps10_greedy import receiver_for
        samples = [Sample(pd.Timestamp('2000-01-01', tz='UTC'), np.asarray(row['sun'], dtype=float),
                          float(row['dni']), float(row['duration_hours'])) for row in payload['samples']]
        if not samples:
            raise ValueError('Energy inputs must not be empty')
        model = self.active
        from rust_core import optical_energy
        shared = optical_energy(model.mirrors, samples, model.config)
        if shared is not None:
            return shared
        return evaluate_energy(model.mirrors, samples, model.config, receiver_for(model.config))

    def optimize_energy_step(self, payload):
        from scripts.annual_energy import Sample
        from rust_core import optimize_energy_step
        samples = [Sample(pd.Timestamp('2000-01-01',tz='UTC'),np.asarray(s['sun']),float(s['dni']),float(s['duration_hours'])) for s in payload['samples']]
        candidates = [(int(c['index']), np.asarray(c['xy']), 0.) for c in payload['candidates']]
        model = self.active
        result = optimize_energy_step(model.mirrors,samples,model.config,candidates,float(payload['threshold_wh']))
        if result is None:
            raise ValueError('Please rebuild the shared Rust optimization core')
        return result
