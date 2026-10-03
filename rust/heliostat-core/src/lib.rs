//! Platform-neutral heliostat geometry kernel.
//!
//! The target mirror plane is the single area frame for shadow and blocking.
//! Candidate rectangles are clipped by physical ray depth before projection;
//! polygon unions ensure coincident shadow/blocking is deducted only once.

use geo::{Area, BooleanOps, Coord, LineString, MultiPolygon, Polygon};
use rayon::prelude::*;
use rstar::{RTree, primitives::GeomWithData};
use serde::{Deserialize, Serialize};
use std::ffi::{CStr, CString, c_char};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::{Mutex, OnceLock};
use thiserror::Error;

const VECTOR_TOL: f64 = 1e-12;
const DEPTH_TOL: f64 = 1e-9;
const GRID: f64 = 1e-9;

fn default_builtin() -> bool {
    true
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MobilePlant {
    pub id: String,
    pub name: String,
    pub layout_status: String,
    pub reported_mirrors: Option<usize>,
    pub source: String,
    pub note: String,
    pub config: serde_json::Value,
    #[serde(default)]
    pub metadata: serde_json::Value,
    pub weather: MobileWeather,
    pub mirrors: Vec<HeliostatInput>,
    #[serde(default = "default_builtin")]
    pub builtin: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MobileWeather {
    pub start_utc: String,
    pub step_seconds: i64,
    pub dni_w_m2: Vec<f64>,
    pub temperature_c: f64,
    #[serde(default)]
    pub temperature_series_c: Vec<f64>,
    #[serde(default)]
    pub temperature_source: String,
    pub source: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MobileBundle {
    pub schema_version: u32,
    pub plants: Vec<MobilePlant>,
}

#[derive(Debug, Serialize)]
pub struct MobileBundleSummary {
    pub schema_version: u32,
    pub plant_count: usize,
    pub mirror_count: usize,
    pub plants: Vec<MobilePlantSummary>,
}

#[derive(Debug, Serialize)]
pub struct MobilePlantSummary {
    pub id: String,
    pub name: String,
    pub mirror_count: usize,
    pub reported_mirrors: usize,
}

pub fn read_mobile_bundle_gzip(bytes: &[u8]) -> Result<MobileBundle, String> {
    use std::io::Read;
    let mut decoder = flate2::read::GzDecoder::new(bytes);
    let mut json = Vec::new();
    decoder
        .read_to_end(&mut json)
        .map_err(|error| format!("invalid mobile bundle gzip: {error}"))?;
    let bundle: MobileBundle = serde_json::from_slice(&json)
        .map_err(|error| format!("invalid mobile bundle JSON: {error}"))?;
    if bundle.schema_version != 1 {
        return Err(format!(
            "unsupported mobile bundle schema {}",
            bundle.schema_version
        ));
    }
    if bundle.plants.is_empty() {
        return Err("mobile bundle contains no plants".into());
    }
    for plant in &bundle.plants {
        if plant.id.trim().is_empty() || plant.name.trim().is_empty() || plant.mirrors.is_empty() {
            return Err("mobile bundle contains an incomplete plant".into());
        }
    }
    Ok(bundle)
}

#[derive(Debug)]
pub struct MobileRuntime {
    bundle: MobileBundle,
    active: usize,
}

#[derive(Clone, Copy)]
struct Receiver {
    centre: Vec3,
    radius: f64,
    height: f64,
}

fn receivers_from_config(
    config: &serde_json::Value,
) -> Result<std::collections::HashMap<String, Receiver>, String> {
    let towers = config
        .get("towers")
        .and_then(serde_json::Value::as_array)
        .ok_or("plant towers missing")?;
    towers
        .iter()
        .map(|tower| {
            let id = tower
                .get("id")
                .and_then(serde_json::Value::as_str)
                .ok_or("tower ID missing")?
                .to_string();
            let receiver = tower.get("receiver").ok_or("tower receiver missing")?;
            let centre_values = receiver
                .get("centre")
                .and_then(serde_json::Value::as_array)
                .ok_or("receiver centre missing")?;
            if centre_values.len() != 3 {
                return Err("receiver centre must have three coordinates".into());
            }
            let centre = [
                centre_values[0].as_f64().ok_or("invalid receiver centre")?,
                centre_values[1].as_f64().ok_or("invalid receiver centre")?,
                centre_values[2].as_f64().ok_or("invalid receiver centre")?,
            ];
            let radius = receiver
                .get("radius")
                .and_then(serde_json::Value::as_f64)
                .ok_or("receiver radius missing")?;
            let height = receiver
                .get("height")
                .and_then(serde_json::Value::as_f64)
                .ok_or("receiver height missing")?;
            Ok((
                id,
                Receiver {
                    centre,
                    radius,
                    height,
                },
            ))
        })
        .collect()
}

fn atmospheric_transmittance(distance: f64) -> f64 {
    if distance <= 1000.0 {
        0.99321 - 0.0001176 * distance + 1.97e-8 * distance * distance
    } else {
        (-0.0001106 * distance).exp()
    }
}

fn gauss_legendre(order: usize) -> (Vec<f64>, Vec<f64>) {
    let mut nodes = vec![0.0; order];
    let mut weights = vec![0.0; order];
    let half = order.div_ceil(2);
    for i in 0..half {
        let mut z = (std::f64::consts::PI * (i as f64 + 0.75) / (order as f64 + 0.5)).cos();
        loop {
            let mut p1 = 1.0;
            let mut p2 = 0.0;
            for j in 1..=order {
                let p3 = p2;
                p2 = p1;
                p1 = ((2 * j - 1) as f64 * z * p2 - (j - 1) as f64 * p3) / j as f64;
            }
            let derivative = order as f64 * (z * p1 - p2) / (z * z - 1.0);
            let next = z - p1 / derivative;
            if (next - z).abs() < 1e-15 {
                z = next;
                let weight = 2.0 / ((1.0 - z * z) * derivative * derivative);
                nodes[i] = -z;
                nodes[order - 1 - i] = z;
                weights[i] = weight;
                weights[order - 1 - i] = weight;
                break;
            }
            z = next;
        }
    }
    (nodes, weights)
}

fn cylinder_interception(
    mirror: &HeliostatInput,
    cosine: f64,
    receiver: Receiver,
    config: &serde_json::Value,
) -> Result<f64, String> {
    let sun_mrad = config
        .get("sunshape_mrad")
        .and_then(serde_json::Value::as_f64)
        .unwrap_or(2.51);
    let slope = config
        .get("slope_error_mrad")
        .and_then(serde_json::Value::as_f64)
        .unwrap_or(2.6);
    let tracking = config
        .get("tracking_error_mrad")
        .and_then(serde_json::Value::as_f64)
        .unwrap_or(2.1);
    let sigma =
        (sun_mrad * sun_mrad + 2.0 * (1.0 + cosine.min(1.0)) * slope * slope + tracking * tracking)
            .sqrt()
            * 1e-3;
    let delta = sub(mirror.centre, receiver.centre);
    let radial = (delta[0] * delta[0] + delta[1] * delta[1]).sqrt();
    if radial <= receiver.radius {
        return Err("mirror lies inside receiver cylinder".into());
    }
    let w = reflected(mirror).map_err(|e| e.to_string())?;
    let horizontal = (w[0] * w[0] + w[1] * w[1]).sqrt();
    if horizontal <= VECTOR_TOL {
        return Err("central ray has no horizontal component".into());
    }
    let u = [-w[1] / horizontal, w[0] / horizontal, 0.0];
    let v = cross(w, u);
    let order = config
        .get("receiver_quadrature_order")
        .and_then(serde_json::Value::as_u64)
        .unwrap_or(64) as usize;
    let (nodes, weights) = gauss_legendre(order.max(8));
    let half = (receiver.radius / radial).acos();
    let centre_phi = delta[1].atan2(delta[0]);
    let mut integral = 0.0;
    for (j, &nj) in nodes.iter().enumerate() {
        let phi = centre_phi + half * nj;
        let nx = phi.cos();
        let ny = phi.sin();
        for (k, &nk) in nodes.iter().enumerate() {
            let z = receiver.centre[2] + nk * receiver.height / 2.0;
            let q = [
                receiver.centre[0] + receiver.radius * nx - mirror.centre[0],
                receiver.centre[1] + receiver.radius * ny - mirror.centre[1],
                z - mirror.centre[2],
            ];
            let forward = dot(q, w);
            if forward <= 0.0 {
                continue;
            }
            let a = dot(q, u) / forward;
            let b = dot(q, v) / forward;
            let density = (-(a * a + b * b) / (2.0 * sigma * sigma)).exp()
                / (2.0 * std::f64::consts::PI * sigma * sigma);
            let inward = -(nx * q[0] + ny * q[1]);
            integral += density * inward / (forward * forward * forward) * weights[j] * weights[k];
        }
    }
    Ok((integral * receiver.radius * half * receiver.height / 2.0).clamp(0.0, 1.0))
}

fn mirror_for_receiver(
    mirror: &HeliostatInput,
    tower_id: &str,
    receiver: Receiver,
) -> Result<HeliostatInput, String> {
    let dx = mirror.centre[0] - receiver.centre[0];
    let dy = mirror.centre[1] - receiver.centre[1];
    let radial = (dx * dx + dy * dy).sqrt();
    if radial <= receiver.radius {
        return Err("flexible mirror lies inside receiver cylinder".into());
    }
    let mut result = mirror.clone();
    result.tower_id = tower_id.to_string();
    result.aim_point = [
        receiver.centre[0] + receiver.radius * dx / radial,
        receiver.centre[1] + receiver.radius * dy / radial,
        receiver.centre[2],
    ];
    Ok(result)
}

fn resolve_mobile_mirrors(plant: &MobilePlant, sun: Vec3) -> Result<Vec<HeliostatInput>, String> {
    if plant
        .config
        .get("tower_assignment_policy")
        .and_then(serde_json::Value::as_str)
        != Some("best_optical_at_time")
        || !plant.mirrors.iter().any(|m| m.tower_id == "auto")
    {
        return Ok(plant.mirrors.clone());
    }
    let receivers = receivers_from_config(&plant.config)?;
    let mut proxy = plant.mirrors.clone();
    for mirror in &mut proxy {
        if mirror.tower_id == "auto" {
            // Deterministic starting assignment; complete received-power
            // comparison including shadow/blocking follows below.
            let mut best: Option<(f64, HeliostatInput)> = None;
            for (id, receiver) in &receivers {
                let candidate = mirror_for_receiver(mirror, id, *receiver)?;
                let pose = PreparedField::pose_for(&candidate, sun).map_err(|e| e.to_string())?;
                let score =
                    dot(pose.normal, sun) * atmospheric_transmittance(pose.receiver_distance);
                if best.as_ref().is_none_or(|current| score > current.0) {
                    best = Some((score, candidate));
                }
            }
            *mirror = best.ok_or("no tower candidate")?.1;
        }
    }
    let field = PreparedField::new(proxy.clone(), sun).map_err(|e| e.to_string())?;
    let flexible: Vec<usize> = plant
        .mirrors
        .iter()
        .enumerate()
        .filter_map(|(i, m)| (m.tower_id == "auto").then_some(i))
        .collect();
    let choices: Result<Vec<(usize, HeliostatInput)>, String> = flexible
        .par_iter()
        .map(|&index| {
            let mut best: Option<(f64, HeliostatInput)> = None;
            for (id, receiver) in &receivers {
                let candidate = mirror_for_receiver(&plant.mirrors[index], id, *receiver)?;
                let geometry = field
                    .efficiency(index, true, Some(&candidate))
                    .map_err(|e| e.to_string())?;
                let pose = PreparedField::pose_for(&candidate, sun).map_err(|e| e.to_string())?;
                let atmosphere = atmospheric_transmittance(pose.receiver_distance);
                let intercept = cylinder_interception(
                    &candidate,
                    geometry.eta_cosine,
                    *receiver,
                    &plant.config,
                )?;
                // This is the tower-dependent part of received optical power.
                // DNI, mirror area, reflectivity and cleanliness are equal for
                // a mirror's tower candidates and do not affect the argmax.
                let score = geometry.eta_cosine * geometry.eta_joint * atmosphere * intercept;
                if best.as_ref().is_none_or(|current| score > current.0) {
                    best = Some((score, candidate));
                }
            }
            Ok((index, best.ok_or("no tower candidate")?.1))
        })
        .collect();
    for (index, mirror) in choices? {
        proxy[index] = mirror;
    }
    Ok(proxy)
}

impl MobileRuntime {
    pub fn from_gzip(bytes: &[u8]) -> Result<Self, String> {
        let bundle = read_mobile_bundle_gzip(bytes)?;
        let active = bundle
            .plants
            .iter()
            .position(|plant| plant.id == "gemasolar")
            .unwrap_or(0);
        Ok(Self { bundle, active })
    }

    pub fn select(&mut self, plant_id: &str) -> Result<serde_json::Value, String> {
        self.active = self
            .bundle
            .plants
            .iter()
            .position(|plant| plant.id == plant_id)
            .ok_or_else(|| format!("unknown plant: {plant_id}"))?;
        self.metadata()
    }

    fn unique_id(&self, name: &str) -> String {
        let stem: String = name
            .to_lowercase()
            .chars()
            .map(|c| if c.is_ascii_alphanumeric() { c } else { '-' })
            .collect();
        let stem = stem.trim_matches('-');
        let stem = if stem.is_empty() { "plant" } else { stem };
        let mut candidate = stem.to_string();
        let mut number = 2;
        while self.bundle.plants.iter().any(|plant| plant.id == candidate) {
            candidate = format!("{stem}-{number}");
            number += 1;
        }
        candidate
    }

    fn number(payload: &serde_json::Value, key: &str, default: f64) -> Result<f64, String> {
        let value = payload
            .get(key)
            .and_then(|value| value.as_f64().or_else(|| value.as_str()?.parse().ok()))
            .unwrap_or(default);
        if value.is_finite() {
            Ok(value)
        } else {
            Err(format!("{key} 必须是有限数字"))
        }
    }

    pub fn import_csv(&mut self, payload: &serde_json::Value) -> Result<serde_json::Value, String> {
        let name = payload
            .get("name")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .trim();
        if name.is_empty() {
            return Err("请填写电厂名称".into());
        }
        let csv_text = payload
            .get("csv")
            .and_then(serde_json::Value::as_str)
            .ok_or("CSV 内容缺失")?;
        let width = Self::number(payload, "mirror_width_m", 12.305)?;
        let height = Self::number(payload, "mirror_height_m", 9.752)?;
        let tower_height = Self::number(payload, "optical_height_m", 140.0)?;
        let receiver_radius = Self::number(payload, "receiver_radius_m", 4.0)?;
        let receiver_height = Self::number(payload, "receiver_height_m", 10.5)?;
        if width <= 0.0
            || height <= 0.0
            || tower_height <= 0.0
            || receiver_radius <= 0.0
            || receiver_height <= 0.0
        {
            return Err("镜面尺寸、塔高和接收器尺寸必须为正数".into());
        }
        let timezone = payload
            .get("timezone")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("UTC")
            .trim();
        let _: chrono_tz::Tz = timezone.parse().map_err(|_| "时区名称无效")?;
        let mut reader = csv::ReaderBuilder::new()
            .trim(csv::Trim::All)
            .from_reader(csv_text.as_bytes());
        let headers = reader.headers().map_err(|_| "CSV 无法读取")?.clone();
        let column = |name: &str| {
            headers
                .iter()
                .position(|h| h.trim().eq_ignore_ascii_case(name))
        };
        let x_col = column("x").ok_or("CSV 必须包含 x、y 列")?;
        let y_col = column("y").ok_or("CSV 必须包含 x、y 列")?;
        let parse = |record: &csv::StringRecord, key: &str, default: f64| -> Result<f64, String> {
            match column(key)
                .and_then(|i| record.get(i))
                .filter(|s| !s.trim().is_empty())
            {
                Some(value) => value
                    .parse::<f64>()
                    .ok()
                    .filter(|v| v.is_finite())
                    .ok_or_else(|| format!("{key} 含无效数值")),
                None => Ok(default),
            }
        };
        let mut mirrors = Vec::new();
        let mut ids = std::collections::HashSet::new();
        for (row, result) in reader.records().enumerate() {
            let record = result.map_err(|_| format!("CSV 第 {} 行无法读取", row + 2))?;
            let x = record
                .get(x_col)
                .ok_or("x 缺失")?
                .parse::<f64>()
                .map_err(|_| "x 含无效数值")?;
            let y = record
                .get(y_col)
                .ok_or("y 缺失")?
                .parse::<f64>()
                .map_err(|_| "y 含无效数值")?;
            if !x.is_finite() || !y.is_finite() {
                return Err("x、y 必须是有限数字".into());
            }
            let id = column("mirror_id")
                .and_then(|i| record.get(i))
                .filter(|s| !s.trim().is_empty())
                .map(str::to_string)
                .unwrap_or_else(|| format!("U{}", row + 1));
            if !ids.insert(id.clone()) {
                return Err("mirror_id 必须唯一".into());
            }
            let radius = x.hypot(y);
            let safe = radius.max(1e-12);
            mirrors.push(HeliostatInput {
                mirror_id: id,
                centre: [x, y, parse(&record, "z", 0.0)?],
                width: parse(&record, "width", width)?,
                height: parse(&record, "height", height)?,
                aim_point: [
                    parse(&record, "aim_x", receiver_radius * x / safe)?,
                    parse(&record, "aim_y", receiver_radius * y / safe)?,
                    parse(&record, "aim_z", tower_height)?,
                ],
                roll_deg: parse(&record, "roll_deg", 0.0)?,
                mount_type: column("mount_type")
                    .and_then(|i| record.get(i))
                    .filter(|s| !s.is_empty())
                    .unwrap_or("azimuth_elevation")
                    .to_string(),
                tower_id: column("tower_id")
                    .and_then(|i| record.get(i))
                    .filter(|s| !s.is_empty())
                    .unwrap_or("tower-1")
                    .to_string(),
            });
        }
        if mirrors.is_empty() || mirrors.len() > 100_000 {
            return Err("镜面数量须为 1–100000".into());
        }
        let active = &self.bundle.plants[self.active];
        let mut config = active.config.clone();
        let object = config.as_object_mut().ok_or("电厂参数无效")?;
        for (key, value) in [
            ("latitude", Self::number(payload, "latitude", 37.562)?),
            ("longitude", Self::number(payload, "longitude", -5.33)?),
            ("altitude_m", Self::number(payload, "altitude_m", 0.0)?),
            ("optical_height_m", tower_height),
            ("tower_height_m", tower_height),
            ("receiver_radius_m", receiver_radius),
            ("receiver_height_m", receiver_height),
            ("mirror_width_m", width),
            ("mirror_height_m", height),
            ("reflective_area_m2", width * height),
        ] {
            object.insert(key.into(), serde_json::json!(value));
        }
        object.insert("timezone".into(), serde_json::json!(timezone));
        object.insert("expected_mirrors".into(), serde_json::json!(mirrors.len()));
        object.insert("towers".into(), serde_json::json!([{"id":"tower-1","base":[0.0,0.0,0.0],"receiver":{"centre":[0.0,0.0,tower_height],"radius":receiver_radius,"height":receiver_height}}]));
        object.insert("tower_assignment_policy".into(), serde_json::json!("fixed"));
        let mut metadata = active.metadata.clone();
        if let Some(meta) = metadata.as_object_mut() {
            meta.insert(
                "default_mirror".into(),
                serde_json::json!(mirrors[0].mirror_id),
            );
            meta.insert("timezone".into(), serde_json::json!(timezone));
            meta.insert("tower_count".into(), serde_json::json!(1));
            meta.insert("towers".into(), object["towers"].clone());
            meta.insert("heliostat_summary".into(), serde_json::json!({"shape":"矩形平面镜","width_m":width,"height_m":height,"reflective_area_m2":width*height,"pedestal_height_m":null,"uniform":true}));
            meta.insert("site_location".into(), serde_json::json!({"latitude":object["latitude"],"longitude":object["longitude"],"accuracy_label":"用户输入","note":"坐标由用户提供，尚未核验。"}));
            meta.insert("plant_details".into(), serde_json::Value::Null);
        }
        let id = self.unique_id(name);
        self.bundle.plants.push(MobilePlant {
            id,
            name: name.into(),
            layout_status: "用户导入坐标".into(),
            reported_mirrors: Some(mirrors.len()),
            source: "设备本地 CSV".into(),
            note: "坐标与参数由用户负责核验。".into(),
            config,
            metadata,
            weather: active.weather.clone(),
            mirrors,
            builtin: false,
        });
        self.active = self.bundle.plants.len() - 1;
        self.metadata()
    }

    pub fn rearrange(&mut self, payload: &serde_json::Value) -> Result<serde_json::Value, String> {
        let source = self.bundle.plants[self.active].clone();
        let count = source.mirrors.len();
        if count < 6 {
            return Err("镜场重排至少需要 6 面镜".into());
        }
        let scheme = payload
            .get("scheme")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("campo");
        if !matches!(scheme, "campo" | "curved" | "free") {
            return Err("未知重排方式".into());
        }
        let width = source
            .config
            .get("mirror_width_m")
            .and_then(serde_json::Value::as_f64)
            .unwrap_or(source.mirrors[0].width);
        let height = source
            .config
            .get("mirror_height_m")
            .and_then(serde_json::Value::as_f64)
            .unwrap_or(source.mirrors[0].height);
        let tower_height = source
            .config
            .get("optical_height_m")
            .and_then(serde_json::Value::as_f64)
            .unwrap_or(140.0);
        let receiver_radius = source
            .config
            .get("receiver_radius_m")
            .and_then(serde_json::Value::as_f64)
            .unwrap_or(4.0);
        let spacing =
            Self::number(payload, "spacing_m", width.hypot(height) + 1.0)?.max(width.max(height));
        let inner = Self::number(payload, "inner_radius_m", tower_height * 0.55)?
            .max(receiver_radius + spacing);
        let golden = std::f64::consts::PI * (3.0 - 5.0_f64.sqrt());
        let north_fraction = Self::number(payload, "north_fraction", 0.6)?.clamp(0.05, 0.95);
        let mut mirrors = Vec::with_capacity(count);
        for i in 0..count {
            let angle = i as f64 * golden;
            let mut radius = inner + spacing * (i as f64).sqrt();
            if scheme == "campo" {
                radius = inner + spacing * ((i / 46) as f64);
            }
            let mut x = radius * angle.cos();
            let mut y = radius * angle.sin();
            if scheme == "curved" {
                y += Self::number(payload, "north_south_m", -25.0)?
                    + Self::number(payload, "ellipticity_m", 15.0)? * (2.0 * angle).cos();
            }
            if scheme == "free" {
                y *= if y >= 0.0 {
                    0.5 / north_fraction
                } else {
                    0.5 / (1.0 - north_fraction)
                };
                x *= 1.08;
            }
            let r = x.hypot(y).max(1e-12);
            mirrors.push(HeliostatInput {
                mirror_id: format!("R{:05}", i + 1),
                centre: [x, y, source.mirrors[i].centre[2]],
                width,
                height,
                aim_point: [
                    receiver_radius * x / r,
                    receiver_radius * y / r,
                    tower_height,
                ],
                roll_deg: 0.0,
                mount_type: "azimuth_elevation".into(),
                tower_id: "tower-1".into(),
            });
        }
        let label = match scheme {
            "campo" => "Campo",
            "curved" => "非圆曲线",
            _ => "自由排布",
        };
        let id = self.unique_id(&format!("{}-{scheme}", source.id));
        let mut plant = source;
        plant.id = id;
        plant.name = format!("{} · {}重排", plant.name, label);
        plant.layout_status = "设备本地参数化重排，非实测坐标".into();
        plant.note = "本布局由设备本地 Rust 内核生成，用于方法比较。".into();
        plant.reported_mirrors = Some(count);
        plant.mirrors = mirrors;
        plant.builtin = false;
        if let Some(meta) = plant.metadata.as_object_mut() {
            meta.insert(
                "default_mirror".into(),
                serde_json::json!(plant.mirrors[0].mirror_id),
            );
        }
        self.bundle.plants.push(plant);
        self.active = self.bundle.plants.len() - 1;
        self.metadata()
    }

    pub fn export_state(&self) -> serde_json::Value {
        serde_json::json!({"schema_version":1,"active_plant":self.bundle.plants[self.active].id,"plants":self.bundle.plants.iter().filter(|p|!p.builtin).collect::<Vec<_>>()})
    }

    pub fn import_state(
        &mut self,
        payload: &serde_json::Value,
    ) -> Result<serde_json::Value, String> {
        let plants: Vec<MobilePlant> = serde_json::from_value(
            payload
                .get("plants")
                .cloned()
                .unwrap_or_else(|| serde_json::json!([])),
        )
        .map_err(|e| format!("保存的镜场无效：{e}"))?;
        self.bundle.plants.retain(|p| p.builtin);
        for mut plant in plants {
            plant.builtin = false;
            if !plant.mirrors.is_empty() && !self.bundle.plants.iter().any(|p| p.id == plant.id) {
                self.bundle.plants.push(plant);
            }
        }
        if let Some(id) = payload
            .get("active_plant")
            .and_then(serde_json::Value::as_str)
            && let Some(index) = self.bundle.plants.iter().position(|p| p.id == id)
        {
            self.active = index;
        }
        self.metadata()
    }

    pub fn metadata(&self) -> Result<serde_json::Value, String> {
        use chrono::{DateTime, Duration, Utc};
        let plant = &self.bundle.plants[self.active];
        let mut value = plant.metadata.clone();
        if !value.is_object() {
            value = serde_json::json!({});
        }
        let object = value.as_object_mut().expect("object assigned above");
        let start = plant
            .weather
            .start_utc
            .parse::<DateTime<Utc>>()
            .map_err(|_| "invalid bundled weather start")?;
        let timestamps: Vec<String> = (0..plant.weather.dni_w_m2.len())
            .map(|hour| {
                (start + Duration::seconds(plant.weather.step_seconds * hour as i64)).to_rfc3339()
            })
            .collect();
        object.insert(
            "mirror_dimensions".into(),
            serde_json::json!(
                plant
                    .mirrors
                    .iter()
                    .map(|m| [m.width, m.height])
                    .collect::<Vec<_>>()
            ),
        );
        object.insert("active_plant".into(), serde_json::json!(plant.id));
        object.insert("plant_name".into(), serde_json::json!(plant.name));
        object.insert(
            "layout_status".into(),
            serde_json::json!(plant.layout_status),
        );
        object.insert(
            "reported_mirrors".into(),
            serde_json::json!(plant.reported_mirrors),
        );
        object.insert("source".into(), serde_json::json!(plant.source));
        object.insert("note".into(), serde_json::json!(plant.note));
        object.insert(
            "mirror_ids".into(),
            serde_json::json!(
                plant
                    .mirrors
                    .iter()
                    .map(|m| &m.mirror_id)
                    .collect::<Vec<_>>()
            ),
        );
        object.insert("timestamps".into(), serde_json::json!(timestamps));
        object.insert("efficiency_sample_count".into(), serde_json::json!(0));
        object.insert(
            "field_efficiencies_deferred".into(),
            serde_json::json!(true),
        );
        object.insert(
            "plants".into(),
            serde_json::json!(self.bundle.plants.iter().map(|item| serde_json::json!({
            "id": item.id, "name": item.name, "builtin": item.builtin, "catalog": item.builtin && item.id != "gemasolar"
        })).collect::<Vec<_>>()),
        );
        Ok(value)
    }

    pub fn frame(&self, timestamp: &str) -> Result<serde_json::Value, String> {
        use chrono::{DateTime, Utc};
        use solar_positioning::{Location, RefractionCorrection, SolarPositions, delta_t};
        let plant = &self.bundle.plants[self.active];
        let time = timestamp
            .parse::<DateTime<Utc>>()
            .map_err(|_| "time must be an ISO-8601 UTC timestamp")?;
        let start = plant
            .weather
            .start_utc
            .parse::<DateTime<Utc>>()
            .map_err(|_| "invalid bundled weather start")?;
        let elapsed = time.signed_duration_since(start).num_seconds();
        if elapsed < 0
            || plant.weather.step_seconds <= 0
            || elapsed % plant.weather.step_seconds != 0
        {
            return Err("time is outside the bundled hourly dataset".into());
        }
        let index = usize::try_from(elapsed / plant.weather.step_seconds)
            .map_err(|_| "invalid weather index")?;
        let dni = *plant
            .weather
            .dni_w_m2
            .get(index)
            .ok_or("time is outside the bundled hourly dataset")?;
        let latitude = plant
            .config
            .get("latitude")
            .and_then(serde_json::Value::as_f64)
            .ok_or("plant latitude missing")?;
        let longitude = plant
            .config
            .get("longitude")
            .and_then(serde_json::Value::as_f64)
            .ok_or("plant longitude missing")?;
        let altitude = plant
            .config
            .get("altitude_m")
            .and_then(serde_json::Value::as_f64)
            .unwrap_or(0.0);
        let temperature = plant.weather.temperature_series_c.get(index).copied();
        let pressure_hpa = 1013.25 * (1.0 - 2.25577e-5 * altitude).max(0.01).powf(5.25588);
        let refraction = RefractionCorrection::new(
            pressure_hpa,
            temperature.unwrap_or(plant.weather.temperature_c),
        )
        .map_err(|e| e.to_string())?;
        let dt = delta_t::estimate_from_date_like(time.date_naive()).map_err(|e| e.to_string())?;
        let position = SolarPositions::new()
            .at(
                &time,
                Location {
                    latitude,
                    longitude,
                },
                altitude,
                dt,
                Some(refraction),
            )
            .map_err(|e| e.to_string())?;
        let elevation = position.elevation_angle();
        let azimuth = position.azimuth().to_radians();
        let elevation_rad = elevation.to_radians();
        let sun = [
            elevation_rad.cos() * azimuth.sin(),
            elevation_rad.cos() * azimuth.cos(),
            elevation_rad.sin(),
        ];
        let timezone: chrono_tz::Tz = plant
            .config
            .get("timezone")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("UTC")
            .parse()
            .map_err(|_| "invalid bundled timezone")?;
        let local_time = time.with_timezone(&timezone).to_rfc3339();
        if elevation <= 0.0 {
            return Ok(serde_json::json!({
                "timestamp": time.to_rfc3339(), "local_time": local_time, "daylight": false,
                "dni": 0.0, "temperature": temperature, "temperature_source": plant.weather.temperature_source, "elevation": elevation,
                "sun": sun, "centres": plant.mirrors.iter().map(|m| m.centre).collect::<Vec<_>>(),
                "vertices": [], "eta_joint": null
            }));
        }
        let mirrors = resolve_mobile_mirrors(plant, sun)?;
        let field = PreparedField::new(mirrors, sun).map_err(|e| e.to_string())?;
        let vertices: Vec<[Vec3; 4]> = field.poses.iter().map(|pose| pose.vertices).collect();
        Ok(serde_json::json!({
            "timestamp": time.to_rfc3339(), "local_time": local_time, "daylight": true,
            "dni": dni, "temperature": temperature, "temperature_source": plant.weather.temperature_source, "elevation": elevation,
            "sun": sun, "vertices": vertices,
            "tower_ids": field.mirrors.iter().map(|m| &m.tower_id).collect::<Vec<_>>(),
            "eta_joint": vec![serde_json::Value::Null; field.mirrors.len()],
            "efficiency_sample_count": 0
        }))
    }

    pub fn target(&self, timestamp: &str, mirror_id: &str) -> Result<serde_json::Value, String> {
        let frame = self.frame(timestamp)?;
        if frame.get("daylight").and_then(serde_json::Value::as_bool) != Some(true) {
            return Ok(
                serde_json::json!({"timestamp":frame["timestamp"],"mirror_id":mirror_id,"daylight":false}),
            );
        }
        let plant = &self.bundle.plants[self.active];
        let index = plant
            .mirrors
            .iter()
            .position(|mirror| mirror.mirror_id == mirror_id)
            .ok_or_else(|| "unknown mirror ID".to_string())?;
        let sun_values = frame["sun"].as_array().ok_or("frame sun missing")?;
        let sun = [
            sun_values[0].as_f64().ok_or("invalid sun")?,
            sun_values[1].as_f64().ok_or("invalid sun")?,
            sun_values[2].as_f64().ok_or("invalid sun")?,
        ];
        let mirrors = resolve_mobile_mirrors(plant, sun)?;
        let field = PreparedField::new(mirrors, sun).map_err(|e| e.to_string())?;
        let mirror = &field.mirrors[index];
        let pose = &field.poses[index];
        let basis3 = projection_basis(pose.normal).map_err(|e| e.to_string())?;
        let plane = [basis3[0], basis3[1]];
        let target_polygon = polygon(pose.vertices.into_iter().map(|point| {
            let local = sub(point, mirror.centre);
            [dot(local, plane[0]), dot(local, plane[1])]
        }));
        let target_view = TargetView {
            index,
            mirror,
            pose,
        };
        let shadow = field
            .mode_diagnostic(&target_view, &target_polygon, plane, field.sun, false, true)
            .map_err(|e| e.to_string())?;
        let blocking = field
            .mode_diagnostic(
                &target_view,
                &target_polygon,
                plane,
                pose.reflected,
                true,
                true,
            )
            .map_err(|e| e.to_string())?;
        let target_geometry = MultiPolygon(vec![target_polygon.clone()]);
        let overlap = shadow.union_polygon.intersection(&blocking.union_polygon);
        let shadow_only = shadow.union_polygon.difference(&blocking.union_polygon);
        let blocking_only = blocking.union_polygon.difference(&shadow.union_polygon);
        let losses = if shadow.union_polygon.0.is_empty() {
            blocking.union_polygon.clone()
        } else if blocking.union_polygon.0.is_empty() {
            shadow.union_polygon.clone()
        } else {
            shadow.union_polygon.union(&blocking.union_polygon)
        };
        let visible = target_geometry.difference(&losses);
        let geometries = [
            ("target", &target_geometry),
            ("shadow", &shadow.union_polygon),
            ("blocking", &blocking.union_polygon),
            ("overlap", &overlap),
            ("shadow_only", &shadow_only),
            ("blocking_only", &blocking_only),
            ("visible", &visible),
        ];
        let mut views = serde_json::Map::new();
        for (name, direction) in [
            ("mirror", pose.normal),
            ("shadow", field.sun),
            ("blocking", pose.reflected),
        ] {
            let view3 = if name == "mirror" {
                basis3
            } else {
                projection_basis(direction).map_err(|e| e.to_string())?
            };
            let matrix = [
                [dot(view3[0], plane[0]), dot(view3[0], plane[1])],
                [dot(view3[1], plane[0]), dot(view3[1], plane[1])],
            ];
            let mut polygons = serde_json::Map::new();
            let mut areas = serde_json::Map::new();
            for (key, geometry) in geometries {
                let projected = transform_geometry(geometry, matrix);
                polygons.insert(key.into(), polygon_rings_json(&projected));
                areas.insert(key.into(), serde_json::json!(multipolygon_area(&projected)));
            }
            let contributor_json = |diagnostic: &ModeDiagnostic| {
                diagnostic.occluder_ids.iter().zip(&diagnostic.polygons).map(|(id, polygon)| {
                serde_json::json!({"mirror_id":id,"polygons":polygon_rings_json(&transform_geometry(polygon, matrix))})
            }).collect::<Vec<_>>()
            };
            views.insert(name.into(), serde_json::json!({
                "polygons":polygons, "areas":areas, "basis":[view3[0],view3[1]],
                "contributors":{"shadow":contributor_json(&shadow),"blocking":contributor_json(&blocking)}
            }));
        }
        let area = target_polygon.unsigned_area();
        let shadow_area = multipolygon_area(&shadow.union_polygon);
        let block_area = multipolygon_area(&blocking.union_polygon);
        let cosine = dot(pose.normal, field.sun).clamp(0.0, 1.0);
        let joint = (multipolygon_area(&visible) / area).clamp(0.0, 1.0);
        let receivers = receivers_from_config(&plant.config)?;
        let receiver = *receivers
            .get(&mirror.tower_id)
            .ok_or("target receiver missing")?;
        let atmosphere = atmospheric_transmittance(pose.receiver_distance);
        let intercept = cylinder_interception(mirror, cosine, receiver, &plant.config)?;
        let reflectivity = plant
            .config
            .get("mirror_reflectivity")
            .and_then(serde_json::Value::as_f64)
            .unwrap_or(1.0);
        let cleanliness = plant
            .config
            .get("mirror_cleanliness")
            .and_then(serde_json::Value::as_f64)
            .unwrap_or(1.0);

        Ok(serde_json::json!({
            "timestamp":frame["timestamp"], "mirror_id":mirror_id, "daylight":true,
            "tower_id":mirror.tower_id, "centre":mirror.centre, "normal":pose.normal,
            "reflected":pose.reflected, "aim":mirror.aim_point, "views":views,
            "modes":{
                "shadow":{"candidate_ids":shadow.candidate_ids,"occluder_ids":shadow.occluder_ids,"decisions":shadow.decisions},
                "blocking":{"candidate_ids":blocking.candidate_ids,"occluder_ids":blocking.occluder_ids,"decisions":blocking.decisions}
            },
            "efficiencies":{
                "eta_shadow":(1.0-shadow_area/area).clamp(0.0,1.0),
                "eta_blocking":(1.0-block_area/area).clamp(0.0,1.0),
                "eta_joint":(multipolygon_area(&visible)/area).clamp(0.0,1.0),
                "eta_cosine":cosine, "eta_atmosphere":atmosphere, "eta_intercept":intercept,
                "mirror_reflectivity":reflectivity, "mirror_cleanliness":cleanliness,
                "eta_optical":cosine * joint * atmosphere * intercept * reflectivity * cleanliness
            },
            "envelope_area_m2":area,
            "reflective_area_m2":plant.config.get("reflective_area_m2").and_then(serde_json::Value::as_f64).unwrap_or(area),
            "total_other_mirrors":plant.mirrors.len()-1
        }))
    }

    pub fn efficiencies(&self, timestamp: &str) -> Result<serde_json::Value, String> {
        let frame = self.frame(timestamp)?;
        if frame.get("daylight").and_then(serde_json::Value::as_bool) != Some(true) {
            return Err("sun is below the horizon".into());
        }
        let plant = &self.bundle.plants[self.active];
        let values = frame["sun"].as_array().ok_or("frame sun missing")?;
        let sun = [
            values[0].as_f64().ok_or("invalid sun")?,
            values[1].as_f64().ok_or("invalid sun")?,
            values[2].as_f64().ok_or("invalid sun")?,
        ];
        let mirrors = resolve_mobile_mirrors(plant, sun)?;
        let field = PreparedField::new(mirrors, sun).map_err(|e| e.to_string())?;
        let receivers = receivers_from_config(&plant.config)?;
        let reflectivity = plant
            .config
            .get("mirror_reflectivity")
            .and_then(serde_json::Value::as_f64)
            .unwrap_or(1.0);
        let cleanliness = plant
            .config
            .get("mirror_cleanliness")
            .and_then(serde_json::Value::as_f64)
            .unwrap_or(1.0);
        let rows: Result<Vec<_>, String> = (0..field.mirrors.len())
            .into_par_iter()
            .map(|index| {
                let geometry = field
                    .efficiency(index, true, None)
                    .map_err(|e| e.to_string())?;
                let mirror = &field.mirrors[index];
                let receiver = *receivers
                    .get(&mirror.tower_id)
                    .ok_or_else(|| format!("no receiver for tower {}", mirror.tower_id))?;
                let atmosphere = atmospheric_transmittance(field.poses[index].receiver_distance);
                let intercept =
                    cylinder_interception(mirror, geometry.eta_cosine, receiver, &plant.config)?;
                let optical = geometry.eta_cosine
                    * geometry.eta_joint
                    * reflectivity
                    * cleanliness
                    * atmosphere
                    * intercept;
                Ok((
                    geometry,
                    optical,
                    atmosphere,
                    intercept,
                    mirror.tower_id.clone(),
                ))
            })
            .collect();
        let rows = rows?;
        let optical: Vec<f64> = rows.iter().map(|row| row.1).collect();
        let factor = |f: fn(&(EfficiencyResult, f64, f64, f64, String)) -> f64| {
            rows.iter().map(f).collect::<Vec<_>>()
        };
        let minimum = optical.iter().copied().fold(f64::INFINITY, f64::min);
        let maximum = optical.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        let mean = optical.iter().sum::<f64>() / optical.len() as f64;
        Ok(
            serde_json::json!({"timestamp":frame["timestamp"],"metric":"eta_optical","label":"总光学效率","values":optical,"factors":{
            "eta_cosine":factor(|r|r.0.eta_cosine),"eta_shadow":factor(|r|r.0.eta_shadow),"eta_blocking":factor(|r|r.0.eta_blocking),"eta_joint":factor(|r|r.0.eta_joint),
            "mirror_reflectivity":vec![reflectivity;rows.len()],"mirror_cleanliness":vec![cleanliness;rows.len()],"eta_atmosphere":factor(|r|r.2),"eta_intercept":factor(|r|r.3)},
            "minimum":minimum,"maximum":maximum,"mean":mean,"mirror_count":rows.len(),"formula":"余弦 × 联合阴影遮挡 × 反射率 × 清洁度 × 沿程透过率 × 接收器截获率","tower_ids":rows.iter().map(|r|&r.4).collect::<Vec<_>>() }),
        )
    }

    pub fn request(
        &mut self,
        method: &str,
        path: &str,
        payload: &serde_json::Value,
    ) -> Result<serde_json::Value, String> {
        match (method, path) {
            ("GET", "health") => Ok(
                serde_json::json!({"status":"ok","mode":"offline","rust_core":true,"rust_core_version":env!("CARGO_PKG_VERSION")}),
            ),
            ("GET", "meta") => self.metadata(),
            ("GET", "frame") => self.frame(
                payload
                    .get("time")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or(""),
            ),
            ("GET", "target") => self.target(
                payload
                    .get("time")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or(""),
                payload
                    .get("mirror")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or(""),
            ),
            ("GET", "efficiencies") => self.efficiencies(
                payload
                    .get("time")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or(""),
            ),
            ("POST", "plants/select") => self.select(
                payload
                    .get("plant_id")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or(""),
            ),
            ("POST", "plants/import") => self.import_csv(payload),
            ("POST", "layout/rearrange") => self.rearrange(payload),
            ("GET", "state/export") => Ok(self.export_state()),
            ("POST", "state/import") => self.import_state(payload),
            _ => Err(format!(
                "offline endpoint is not implemented: {method} {path}"
            )),
        }
    }
}

pub fn mobile_bundle_summary(bundle: &MobileBundle) -> MobileBundleSummary {
    MobileBundleSummary {
        schema_version: bundle.schema_version,
        plant_count: bundle.plants.len(),
        mirror_count: bundle.plants.iter().map(|plant| plant.mirrors.len()).sum(),
        plants: bundle
            .plants
            .iter()
            .map(|plant| MobilePlantSummary {
                id: plant.id.clone(),
                name: plant.name.clone(),
                mirror_count: plant.mirrors.len(),
                reported_mirrors: plant.reported_mirrors.unwrap_or(plant.mirrors.len()),
            })
            .collect(),
    }
}

type Vec3 = [f64; 3];
type PointItem = GeomWithData<[f64; 2], usize>;

#[derive(Debug, Error)]
pub enum CoreError {
    #[error("{0} must contain finite coordinates")]
    NonFinite(&'static str),
    #[error("{0} must have nonzero finite length")]
    ZeroVector(&'static str),
    #[error("solar direction must be above the horizon")]
    SunBelowHorizon,
    #[error("mirror index {0} is out of range")]
    BadIndex(usize),
    #[error("mirror {0} has invalid dimensions or aim point")]
    InvalidMirror(String),
    #[error("target override for mirror {0} may only change aim point and tower id")]
    InvalidTargetOverride(String),
    #[error("target override count {actual} does not match target count {expected}")]
    TargetOverrideCount { expected: usize, actual: usize },
    #[error("target geometry is degenerate")]
    DegenerateTarget,
    #[error("receiver plane must lie beyond every target vertex")]
    InvalidReceiverPlane,
    #[error("JSON input is invalid: {0}")]
    Json(#[from] serde_json::Error),
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct HeliostatInput {
    pub mirror_id: String,
    pub centre: Vec3,
    pub width: f64,
    pub height: f64,
    pub aim_point: Vec3,
    #[serde(default)]
    pub roll_deg: f64,
    #[serde(default = "default_mount")]
    pub mount_type: String,
    #[serde(default = "default_tower")]
    pub tower_id: String,
}

fn default_mount() -> String {
    "projected_east".into()
}
fn default_tower() -> String {
    "tower-1".into()
}

#[derive(Clone, Debug, Deserialize)]
pub struct FieldRequest {
    pub mirrors: Vec<HeliostatInput>,
    pub sun: Vec3,
    #[serde(default)]
    pub target_indices: Option<Vec<usize>>,
    #[serde(default)]
    pub target_overrides: Option<Vec<HeliostatInput>>,
    #[serde(default = "default_true")]
    pub conservative_filter: bool,
}

fn default_true() -> bool {
    true
}

#[derive(Clone, Debug, Serialize)]
pub struct EfficiencyResult {
    pub mirror_id: String,
    pub eta_cosine: f64,
    pub eta_shadow: f64,
    pub eta_blocking: f64,
    pub eta_joint: f64,
    pub shadow_occluders: usize,
    pub blocking_occluders: usize,
}

#[derive(Clone)]
struct Pose {
    vertices: [Vec3; 4],
    radius: f64,
    reflected: Vec3,
    normal: Vec3,
    receiver_distance: f64,
}

struct TargetView<'a> {
    index: usize,
    mirror: &'a HeliostatInput,
    pose: &'a Pose,
}

struct ModeDiagnostic {
    candidate_ids: Vec<String>,
    occluder_ids: Vec<String>,
    polygons: Vec<MultiPolygon<f64>>,
    decisions: Vec<serde_json::Value>,
    union_polygon: MultiPolygon<f64>,
}

struct PreparedField {
    mirrors: Vec<HeliostatInput>,
    sun: Vec3,
    poses: Vec<Pose>,
    max_radius: f64,
    flat: bool,
    tree: RTree<PointItem>,
}

fn add(a: Vec3, b: Vec3) -> Vec3 {
    [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
}
fn sub(a: Vec3, b: Vec3) -> Vec3 {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}
fn scale(a: Vec3, k: f64) -> Vec3 {
    [a[0] * k, a[1] * k, a[2] * k]
}
fn dot(a: Vec3, b: Vec3) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}
fn cross(a: Vec3, b: Vec3) -> Vec3 {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}
fn norm(a: Vec3) -> f64 {
    dot(a, a).sqrt()
}

fn unit(a: Vec3, name: &'static str) -> Result<Vec3, CoreError> {
    if !a.iter().all(|value| value.is_finite()) {
        return Err(CoreError::NonFinite(name));
    }
    let length = norm(a);
    if length <= VECTOR_TOL {
        return Err(CoreError::ZeroVector(name));
    }
    Ok(scale(a, 1.0 / length))
}

fn reflected(mirror: &HeliostatInput) -> Result<Vec3, CoreError> {
    unit(sub(mirror.aim_point, mirror.centre), "reflection direction")
}

fn mirror_normal(sun: Vec3, reflected: Vec3) -> Result<Vec3, CoreError> {
    unit(add(sun, reflected), "normal bisector")
}

fn projection_basis(direction: Vec3) -> Result<[Vec3; 3], CoreError> {
    let w = unit(direction, "projection direction")?;
    let axes = [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]];
    let reference = axes
        .into_iter()
        .min_by(|a, b| dot(*a, w).abs().partial_cmp(&dot(*b, w).abs()).unwrap())
        .unwrap();
    let u = unit(cross(reference, w), "projection u axis")?;
    let v = cross(w, u);
    Ok([u, v, w])
}

fn mirror_vertices(mirror: &HeliostatInput, normal: Vec3) -> Result<[Vec3; 4], CoreError> {
    if mirror.mirror_id.trim().is_empty()
        || !mirror.width.is_finite()
        || mirror.width <= 0.
        || !mirror.height.is_finite()
        || mirror.height <= 0.
        || !mirror.roll_deg.is_finite()
        || !mirror
            .centre
            .iter()
            .chain(mirror.aim_point.iter())
            .all(|value| value.is_finite())
    {
        return Err(CoreError::InvalidMirror(mirror.mirror_id.clone()));
    }
    let mut reference = [1., 0., 0.];
    if normal[0].abs() > 1. - 1e-10 {
        reference = [0., 1., 0.];
    }
    let mut width_axis = unit(
        sub(reference, scale(normal, dot(reference, normal))),
        "mirror width axis",
    )?;
    if mirror.mount_type == "azimuth_elevation" {
        let horizontal = cross([0., 0., 1.], normal);
        if norm(horizontal) > 1e-10 {
            width_axis = unit(horizontal, "horizontal width axis")?;
        }
    } else if mirror.mount_type != "projected_east" {
        return Err(CoreError::InvalidMirror(mirror.mirror_id.clone()));
    }
    let height_axis = cross(normal, width_axis);
    let roll = mirror.roll_deg.rem_euclid(360.).to_radians();
    let u = add(
        scale(width_axis, roll.cos()),
        scale(height_axis, roll.sin()),
    );
    let v = add(
        scale(width_axis, -roll.sin()),
        scale(height_axis, roll.cos()),
    );
    let corner = |su: f64, sv: f64| {
        add(
            mirror.centre,
            add(
                scale(u, su * mirror.width / 2.),
                scale(v, sv * mirror.height / 2.),
            ),
        )
    };
    Ok([
        corner(-1., -1.),
        corner(1., -1.),
        corner(1., 1.),
        corner(-1., 1.),
    ])
}

fn clip_halfspace(vertices: &[Vec3], normal: Vec3, offset: f64) -> Vec<Vec3> {
    if vertices.is_empty() {
        return Vec::new();
    }
    let distances: Vec<f64> = vertices.iter().map(|p| dot(*p, normal) + offset).collect();
    if distances.iter().copied().fold(f64::NEG_INFINITY, f64::max) <= 0. {
        return Vec::new();
    }
    let mut clipped = Vec::with_capacity(vertices.len() + 2);
    for i in 0..vertices.len() {
        let previous = (i + vertices.len() - 1) % vertices.len();
        let (start, end) = (vertices[previous], vertices[i]);
        let (a, b) = (distances[previous], distances[i]);
        if (a >= 0.) != (b >= 0.) {
            clipped.push(add(start, scale(sub(end, start), a / (a - b))));
        }
        if b >= 0. {
            clipped.push(end);
        }
    }
    clipped
}

fn quantize(value: f64) -> f64 {
    (value / GRID).round() * GRID
}

fn polygon(points: impl IntoIterator<Item = [f64; 2]>) -> Polygon<f64> {
    let mut coords: Vec<Coord<f64>> = points
        .into_iter()
        .map(|p| Coord {
            x: quantize(p[0]),
            y: quantize(p[1]),
        })
        .collect();
    if let Some(first) = coords.first().copied()
        && coords.last().copied() != Some(first)
    {
        coords.push(first);
    }
    Polygon::new(LineString::new(coords), vec![])
}

fn multipolygon_area(value: &MultiPolygon<f64>) -> f64 {
    value.unsigned_area()
}

fn transform_geometry(value: &MultiPolygon<f64>, matrix: [[f64; 2]; 2]) -> MultiPolygon<f64> {
    use geo::MapCoords;
    value.map_coords(|coord| Coord {
        x: matrix[0][0] * coord.x + matrix[0][1] * coord.y,
        y: matrix[1][0] * coord.x + matrix[1][1] * coord.y,
    })
}

fn polygon_rings_json(value: &MultiPolygon<f64>) -> serde_json::Value {
    serde_json::Value::Array(value.0.iter().filter(|polygon| polygon.unsigned_area() > DEPTH_TOL * DEPTH_TOL).map(|polygon| {
        let ring = |line: &LineString<f64>| line.0.iter().map(|coord| serde_json::json!([coord.x, coord.y])).collect::<Vec<_>>();
        serde_json::json!({"outer":ring(polygon.exterior()),"holes":polygon.interiors().iter().map(ring).collect::<Vec<_>>()})
    }).collect())
}

impl PreparedField {
    fn pose_for(mirror: &HeliostatInput, sun: Vec3) -> Result<Pose, CoreError> {
        let reflected = reflected(mirror)?;
        let normal = mirror_normal(sun, reflected)?;
        let vertices = mirror_vertices(mirror, normal)?;
        Ok(Pose {
            vertices,
            radius: mirror.width.hypot(mirror.height) / 2.,
            reflected,
            normal,
            receiver_distance: norm(sub(mirror.aim_point, mirror.centre)),
        })
    }

    fn new(mirrors: Vec<HeliostatInput>, sun: Vec3) -> Result<Self, CoreError> {
        let sun = unit(sun, "sun")?;
        if sun[2] <= 0. {
            return Err(CoreError::SunBelowHorizon);
        }
        let mut poses = Vec::with_capacity(mirrors.len());
        let mut max_radius: f64 = 0.;
        let mut z_min = f64::INFINITY;
        let mut z_max = f64::NEG_INFINITY;
        for mirror in &mirrors {
            let pose = Self::pose_for(mirror, sun)?;
            max_radius = max_radius.max(pose.radius);
            z_min = z_min.min(mirror.centre[2]);
            z_max = z_max.max(mirror.centre[2]);
            poses.push(pose);
        }
        let tree = RTree::bulk_load(
            mirrors
                .iter()
                .enumerate()
                .map(|(index, mirror)| {
                    GeomWithData::new([mirror.centre[0], mirror.centre[1]], index)
                })
                .collect(),
        );
        Ok(Self {
            mirrors,
            sun,
            poses,
            max_radius,
            flat: z_max - z_min <= DEPTH_TOL,
            tree,
        })
    }

    fn candidates(
        &self,
        target: &TargetView<'_>,
        direction: Vec3,
        target_radius: f64,
        blocking: bool,
        conservative: bool,
    ) -> Vec<usize> {
        let origin = target.mirror.centre;
        let target_normal = target.pose.normal;
        let cosine = dot(target_normal, direction);
        let distance = target.pose.receiver_distance;
        let possible: Vec<usize> = if conservative && self.flat && direction[2] > VECTOR_TOL {
            let radius = (self.max_radius + target_radius + DEPTH_TOL) / direction[2];
            self.tree
                .locate_within_distance([origin[0], origin[1]], radius * radius)
                .map(|item| item.data)
                .collect()
        } else {
            (0..self.mirrors.len()).collect()
        };
        possible
            .into_iter()
            .filter(|&index| index != target.index)
            .filter(|&index| {
                if !conservative {
                    return true;
                }
                let delta = sub(self.mirrors[index].centre, origin);
                let transverse = sub(delta, scale(direction, dot(delta, direction)));
                if norm(transverse) > self.poses[index].radius + target_radius + DEPTH_TOL {
                    return false;
                }
                let local = self.poses[index].vertices.map(|point| sub(point, origin));
                if local
                    .iter()
                    .map(|p| dot(*p, target_normal))
                    .fold(f64::NEG_INFINITY, f64::max)
                    <= DEPTH_TOL * cosine
                {
                    return false;
                }
                !blocking
                    || local
                        .iter()
                        .map(|p| dot(*p, direction))
                        .fold(f64::INFINITY, f64::min)
                        < distance - DEPTH_TOL
            })
            .collect()
    }

    fn mode_diagnostic(
        &self,
        target_view: &TargetView<'_>,
        target_polygon: &Polygon<f64>,
        plane: [Vec3; 2],
        direction: Vec3,
        blocking: bool,
        conservative: bool,
    ) -> Result<ModeDiagnostic, CoreError> {
        let origin = target_view.mirror.centre;
        let pose = target_view.pose;
        let cosine = dot(pose.normal, direction);
        if cosine <= VECTOR_TOL {
            return Err(CoreError::DegenerateTarget);
        }
        if blocking
            && pose
                .vertices
                .iter()
                .map(|p| dot(sub(*p, origin), direction))
                .fold(f64::NEG_INFINITY, f64::max)
                >= pose.receiver_distance - DEPTH_TOL
        {
            return Err(CoreError::InvalidReceiverPlane);
        }
        let target_radius = target_view.mirror.width.hypot(target_view.mirror.height) / 2.;
        let candidates = self.candidates(
            target_view,
            direction,
            target_radius,
            blocking,
            conservative,
        );
        let candidate_ids = candidates
            .iter()
            .map(|&i| self.mirrors[i].mirror_id.clone())
            .collect();
        let mut merged = MultiPolygon(vec![]);
        let mut polygons = Vec::new();
        let mut occluder_ids = Vec::new();
        let mut decisions = Vec::new();
        for index in candidates {
            let id = self.mirrors[index].mirror_id.clone();
            let mut clipped = clip_halfspace(
                &self.poses[index].vertices.map(|point| sub(point, origin)),
                pose.normal,
                -DEPTH_TOL * cosine,
            );
            if blocking {
                clipped = clip_halfspace(
                    &clipped,
                    scale(direction, -1.),
                    pose.receiver_distance - DEPTH_TOL,
                );
            }
            if clipped.len() < 3 {
                decisions.push(serde_json::json!({"mirror_id":id,"reason":"depth_rejected"}));
                continue;
            }
            let projected = polygon(clipped.into_iter().map(|point| {
                let on_plane = sub(point, scale(direction, dot(point, pose.normal) / cosine));
                [dot(on_plane, plane[0]), dot(on_plane, plane[1])]
            }));
            if projected.unsigned_area() <= DEPTH_TOL * DEPTH_TOL {
                decisions.push(serde_json::json!({"mirror_id":id,"reason":"degenerate"}));
                continue;
            }
            let overlap = target_polygon.intersection(&projected);
            let area = multipolygon_area(&overlap);
            if area > DEPTH_TOL * DEPTH_TOL {
                merged = if merged.0.is_empty() {
                    overlap.clone()
                } else {
                    merged.union(&overlap)
                };
                polygons.push(overlap);
                occluder_ids.push(id.clone());
            }
            decisions.push(serde_json::json!({"mirror_id":id,"reason":if area > DEPTH_TOL * DEPTH_TOL {"overlap"} else {"no_overlap"},"overlap_area_m2":area}));
        }
        Ok(ModeDiagnostic {
            candidate_ids,
            occluder_ids,
            polygons,
            decisions,
            union_polygon: merged,
        })
    }

    fn mode_union(
        &self,
        target_view: &TargetView<'_>,
        target_polygon: &Polygon<f64>,
        plane: [Vec3; 2],
        direction: Vec3,
        blocking: bool,
        conservative: bool,
    ) -> Result<(MultiPolygon<f64>, usize), CoreError> {
        let diagnostic = self.mode_diagnostic(
            target_view,
            target_polygon,
            plane,
            direction,
            blocking,
            conservative,
        )?;
        let count = diagnostic.occluder_ids.len();
        Ok((diagnostic.union_polygon, count))
    }

    fn efficiency(
        &self,
        index: usize,
        conservative: bool,
        target_override: Option<&HeliostatInput>,
    ) -> Result<EfficiencyResult, CoreError> {
        if index >= self.mirrors.len() {
            return Err(CoreError::BadIndex(index));
        }
        let original = &self.mirrors[index];
        let override_pose;
        let (mirror, pose) = if let Some(mirror) = target_override {
            let same_geometry = mirror.mirror_id == original.mirror_id
                && mirror
                    .centre
                    .iter()
                    .zip(original.centre.iter())
                    .all(|(a, b)| (a - b).abs() <= DEPTH_TOL)
                && (mirror.width - original.width).abs() <= DEPTH_TOL
                && (mirror.height - original.height).abs() <= DEPTH_TOL
                && (mirror.roll_deg - original.roll_deg).abs() <= DEPTH_TOL
                && mirror.mount_type == original.mount_type;
            if !same_geometry {
                return Err(CoreError::InvalidTargetOverride(original.mirror_id.clone()));
            }
            override_pose = Self::pose_for(mirror, self.sun)?;
            (mirror, &override_pose)
        } else {
            (original, &self.poses[index])
        };
        let origin = mirror.centre;
        let basis = projection_basis(pose.normal)?;
        let plane = [basis[0], basis[1]];
        let target = polygon(pose.vertices.into_iter().map(|point| {
            let local = sub(point, origin);
            [dot(local, plane[0]), dot(local, plane[1])]
        }));
        let area = target.unsigned_area();
        if area <= DEPTH_TOL * DEPTH_TOL {
            return Err(CoreError::DegenerateTarget);
        }
        let target_view = TargetView {
            index,
            mirror,
            pose,
        };
        let (shadow, shadow_count) =
            self.mode_union(&target_view, &target, plane, self.sun, false, conservative)?;
        let (blocking, blocking_count) = self.mode_union(
            &target_view,
            &target,
            plane,
            pose.reflected,
            true,
            conservative,
        )?;
        let shadow_area = multipolygon_area(&shadow);
        let blocking_area = multipolygon_area(&blocking);
        let joint_area = if shadow.0.is_empty() {
            blocking_area
        } else if blocking.0.is_empty() {
            shadow_area
        } else {
            multipolygon_area(&shadow.union(&blocking))
        };
        Ok(EfficiencyResult {
            mirror_id: mirror.mirror_id.clone(),
            eta_cosine: dot(pose.normal, self.sun).clamp(0., 1.),
            eta_shadow: (1. - shadow_area / area).clamp(0., 1.),
            eta_blocking: (1. - blocking_area / area).clamp(0., 1.),
            eta_joint: (1. - joint_area / area).clamp(0., 1.),
            shadow_occluders: shadow_count,
            blocking_occluders: blocking_count,
        })
    }
}

pub fn field_efficiencies(request: FieldRequest) -> Result<Vec<EfficiencyResult>, CoreError> {
    let targets = request
        .target_indices
        .clone()
        .unwrap_or_else(|| (0..request.mirrors.len()).collect());
    if let Some(overrides) = &request.target_overrides
        && overrides.len() != targets.len()
    {
        return Err(CoreError::TargetOverrideCount {
            expected: targets.len(),
            actual: overrides.len(),
        });
    }
    let field = PreparedField::new(request.mirrors, request.sun)?;
    targets
        .into_par_iter()
        .enumerate()
        .map(|(position, index)| {
            field.efficiency(
                index,
                request.conservative_filter,
                request
                    .target_overrides
                    .as_ref()
                    .map(|overrides| &overrides[position]),
            )
        })
        .collect()
}

pub fn field_efficiencies_json(payload: &str) -> Result<String, CoreError> {
    let request: FieldRequest = serde_json::from_str(payload)?;
    Ok(serde_json::to_string(&field_efficiencies(request)?)?)
}

static MOBILE_RUNTIME: OnceLock<Mutex<Option<MobileRuntime>>> = OnceLock::new();

fn mobile_runtime() -> &'static Mutex<Option<MobileRuntime>> {
    MOBILE_RUNTIME.get_or_init(|| Mutex::new(None))
}

fn initialize_mobile_runtime(bytes: &[u8]) -> Result<serde_json::Value, String> {
    let runtime = MobileRuntime::from_gzip(bytes)?;
    let summary = mobile_bundle_summary(&runtime.bundle);
    *mobile_runtime()
        .lock()
        .map_err(|_| "mobile runtime lock poisoned")? = Some(runtime);
    serde_json::to_value(summary).map_err(|error| error.to_string())
}

#[derive(Deserialize)]
struct MobileApiRequest {
    method: String,
    path: String,
    #[serde(default)]
    payload: serde_json::Value,
}

fn mobile_request_json(input: &str) -> String {
    let result = serde_json::from_str::<MobileApiRequest>(input)
        .map_err(|error| error.to_string())
        .and_then(|request| {
            let mut guard = mobile_runtime()
                .lock()
                .map_err(|_| "mobile runtime lock poisoned".to_string())?;
            let runtime = guard
                .as_mut()
                .ok_or_else(|| "offline mobile runtime is not initialized".to_string())?;
            runtime.request(
                &request.method,
                request.path.trim_start_matches('/'),
                &request.payload,
            )
        });
    result
        .unwrap_or_else(|error| serde_json::json!({"error":error}))
        .to_string()
}

fn c_string(value: serde_json::Value) -> *mut c_char {
    CString::new(value.to_string())
        .map(CString::into_raw)
        .unwrap_or(std::ptr::null_mut())
}

/// Initialize the process-wide offline mobile runtime from a gzipped bundle.
///
/// # Safety
/// `data` must reference `length` readable bytes for the duration of this call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn heliostat_mobile_initialize_gzip(
    data: *const u8,
    length: usize,
) -> *mut c_char {
    if data.is_null() {
        return c_string(serde_json::json!({"error":"bundle pointer is null"}));
    }
    // SAFETY: caller guarantees a readable buffer of `length` bytes.
    let bytes = unsafe { std::slice::from_raw_parts(data, length) };
    c_string(
        initialize_mobile_runtime(bytes).unwrap_or_else(|error| serde_json::json!({"error":error})),
    )
}

/// Dispatch a JSON request to the initialized offline mobile runtime.
///
/// # Safety
/// `input` must point to a valid NUL-terminated UTF-8 string.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn heliostat_mobile_request_json(input: *const c_char) -> *mut c_char {
    if input.is_null() {
        return c_string(serde_json::json!({"error":"request pointer is null"}));
    }
    // SAFETY: caller guarantees a valid C string.
    let text = unsafe { CStr::from_ptr(input) }.to_string_lossy();
    CString::new(mobile_request_json(&text))
        .map(CString::into_raw)
        .unwrap_or(std::ptr::null_mut())
}

/// C ABI used by Swift, Kotlin/NDK and other native platform shells.
///
/// The returned UTF-8 string is either the result array or an object with an
/// `error` member. The caller must release it with `heliostat_free_string`.
///
/// # Safety
///
/// `input` must point to a valid NUL-terminated UTF-8 byte string and remain
/// valid for the duration of the call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn heliostat_compute_field_json(input: *const c_char) -> *mut c_char {
    let result = catch_unwind(AssertUnwindSafe(|| {
        if input.is_null() {
            return Err("input pointer is null".to_string());
        }
        // SAFETY: the caller contract requires a valid NUL-terminated string.
        let payload = unsafe { CStr::from_ptr(input) }
            .to_str()
            .map_err(|error| format!("input is not UTF-8: {error}"))?;
        field_efficiencies_json(payload).map_err(|error| error.to_string())
    }));
    let text = match result {
        Ok(Ok(value)) => value,
        Ok(Err(error)) => serde_json::json!({"error": error}).to_string(),
        Err(_) => serde_json::json!({"error": "Rust geometry core panicked"}).to_string(),
    };
    CString::new(text)
        .map(CString::into_raw)
        .unwrap_or(std::ptr::null_mut())
}

/// Release strings returned by `heliostat_compute_field_json`.
///
/// # Safety
///
/// `value` must be null or a pointer returned by
/// `heliostat_compute_field_json` that has not already been released.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn heliostat_free_string(value: *mut c_char) {
    if !value.is_null() {
        // SAFETY: the pointer must come from CString::into_raw above and be freed once.
        drop(unsafe { CString::from_raw(value) });
    }
}

#[cfg(feature = "python-bindings")]
mod python {
    use super::*;
    use pyo3::exceptions::PyValueError;
    use pyo3::prelude::*;

    #[pyfunction]
    fn compute_field_json(payload: &str) -> PyResult<String> {
        field_efficiencies_json(payload).map_err(|error| PyValueError::new_err(error.to_string()))
    }

    #[pymodule]
    fn _heliostat_rust(module: &Bound<'_, PyModule>) -> PyResult<()> {
        module.add_function(wrap_pyfunction!(compute_field_json, module)?)?;
        module.add("CORE_VERSION", env!("CARGO_PKG_VERSION"))?;
        Ok(())
    }
}

#[cfg(feature = "android-jni")]
mod android {
    use super::*;
    use jni::JNIEnv;
    use jni::objects::{JByteArray, JClass, JString};
    use jni::sys::jstring;

    fn java_string(env: &mut JNIEnv, text: String) -> jstring {
        env.new_string(text)
            .map(JString::into_raw)
            .unwrap_or(std::ptr::null_mut())
    }

    #[unsafe(no_mangle)]
    pub extern "system" fn Java_com_hustquick_heliostatviewer_NativeCore_initializeBundle(
        mut env: JNIEnv,
        _class: JClass,
        input: JByteArray,
    ) -> jstring {
        let result = env
            .convert_byte_array(&input)
            .map_err(|error| error.to_string())
            .and_then(|bytes| initialize_mobile_runtime(&bytes));
        java_string(
            &mut env,
            result
                .unwrap_or_else(|error| serde_json::json!({"error":error}))
                .to_string(),
        )
    }

    #[unsafe(no_mangle)]
    pub extern "system" fn Java_com_hustquick_heliostatviewer_NativeCore_requestJson(
        mut env: JNIEnv,
        _class: JClass,
        input: JString,
    ) -> jstring {
        let text = env
            .get_string(&input)
            .map(|value| value.to_string_lossy().into_owned())
            .map(|value| mobile_request_json(&value))
            .unwrap_or_else(|error| serde_json::json!({"error":error.to_string()}).to_string());
        java_string(&mut env, text)
    }

    #[unsafe(no_mangle)]
    pub extern "system" fn Java_com_hustquick_heliostatviewer_NativeCore_computeFieldJson(
        mut env: JNIEnv,
        _class: JClass,
        input: JString,
    ) -> jstring {
        let result = env
            .get_string(&input)
            .map(|value| value.to_string_lossy().into_owned())
            .map_err(|error| error.to_string())
            .and_then(|payload| {
                field_efficiencies_json(&payload).map_err(|error| error.to_string())
            });
        let text = result.unwrap_or_else(|error| serde_json::json!({"error": error}).to_string());
        env.new_string(text)
            .map(JString::into_raw)
            .unwrap_or(std::ptr::null_mut())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use approx::assert_abs_diff_eq;

    #[test]
    fn mobile_bundle_is_read_without_network_or_python() {
        use flate2::Compression;
        use flate2::write::GzEncoder;
        use std::io::Write;
        let json = r#"{"schema_version":1,"plants":[{"id":"test","name":"Test field","layout_status":"built-in","reported_mirrors":1,"source":"fixture","note":"fixture","config":{"latitude":1,"longitude":0,"altitude_m":0,"timezone":"UTC","year":2023,"mirror_reflectivity":0.92,"mirror_cleanliness":0.95,"receiver_quadrature_order":16,"towers":[{"id":"tower-1","receiver":{"centre":[5,0,20],"radius":1,"height":4}}]},"metadata":{"timezone":"UTC"},"weather":{"start_utc":"2023-03-21T12:00:00+00:00","step_seconds":3600,"dni_w_m2":[800.0],"temperature_c":12.0,"source":"fixture"},"mirrors":[{"mirror_id":"M1","centre":[0,0,0],"width":2,"height":2,"aim_point":[4,0,20],"tower_id":"tower-1"}]}]}"#;
        let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(json.as_bytes()).unwrap();
        let compressed = encoder.finish().unwrap();
        let bundle = read_mobile_bundle_gzip(&compressed).unwrap();
        let summary = mobile_bundle_summary(&bundle);
        assert_eq!(summary.schema_version, 1);
        assert_eq!(summary.plant_count, 1);
        assert_eq!(summary.mirror_count, 1);
        assert_eq!(summary.plants[0].id, "test");
        let mut runtime = MobileRuntime::from_gzip(&compressed).unwrap();
        let meta = runtime
            .request("GET", "meta", &serde_json::json!({}))
            .unwrap();
        assert_eq!(meta["active_plant"], "test");
        assert_eq!(meta["mirror_ids"][0], "M1");
        assert_eq!(meta["timestamps"].as_array().unwrap().len(), 1);
        assert_eq!(
            runtime
                .request("GET", "health", &serde_json::json!({}))
                .unwrap()["mode"],
            "offline"
        );
        let frame = runtime
            .request(
                "GET",
                "frame",
                &serde_json::json!({"time":"2023-03-21T12:00:00Z"}),
            )
            .unwrap();
        assert_eq!(frame["daylight"], true);
        assert_eq!(frame["dni"], 800.0);
        assert!((frame["elevation"].as_f64().unwrap() - 88.0401).abs() < 0.01);
        let sun = frame["sun"].as_array().unwrap();
        assert!((sun[2].as_f64().unwrap() - 0.999415).abs() < 1e-5);
        let target = runtime
            .request(
                "GET",
                "target",
                &serde_json::json!({"time":"2023-03-21T12:00:00Z","mirror":"M1"}),
            )
            .unwrap();
        assert_eq!(target["mirror_id"], "M1");
        assert!((target["efficiencies"]["eta_joint"].as_f64().unwrap() - 1.0).abs() < 1e-9);
        assert!(target["views"]["mirror"]["polygons"]["target"].is_array());
        let efficiencies = runtime
            .request(
                "GET",
                "efficiencies",
                &serde_json::json!({"time":"2023-03-21T12:00:00Z"}),
            )
            .unwrap();
        assert_eq!(efficiencies["mirror_count"], 1);
        let optical = efficiencies["values"][0].as_f64().unwrap();
        assert!(optical > 0.0 && optical < 1.0);
        assert_eq!(efficiencies["factors"]["mirror_reflectivity"][0], 0.92);

        let imported = runtime.request("POST", "plants/import", &serde_json::json!({
            "name":"Local field","csv":"mirror_id,x,y\nA,50,0\nB,45,20\nC,25,45\nD,-25,45\nE,-45,20\nF,-50,0\n",
            "latitude":"37.5","longitude":"-5.3","timezone":"UTC","optical_height_m":"100",
            "receiver_radius_m":"4","receiver_height_m":"8","mirror_width_m":"6","mirror_height_m":"4"
        })).unwrap();
        assert_eq!(imported["mirror_ids"].as_array().unwrap().len(), 6);
        assert_eq!(
            imported["plants"].as_array().unwrap().last().unwrap()["builtin"],
            false
        );
        let rearranged = runtime
            .request(
                "POST",
                "layout/rearrange",
                &serde_json::json!({"scheme":"free","spacing_m":"10"}),
            )
            .unwrap();
        assert_eq!(rearranged["mirror_ids"].as_array().unwrap().len(), 6);
        let state = runtime
            .request("GET", "state/export", &serde_json::json!({}))
            .unwrap();
        assert_eq!(state["plants"].as_array().unwrap().len(), 2);
        let mut restored = MobileRuntime::from_gzip(&compressed).unwrap();
        restored.request("POST", "state/import", &state).unwrap();
        assert_eq!(
            restored.metadata().unwrap()["active_plant"],
            state["active_plant"]
        );
    }

    #[test]
    fn receiver_quadrature_matches_python_reference() {
        let mirror = HeliostatInput {
            mirror_id: "M".into(),
            centre: [0., 0., 0.],
            width: 2.,
            height: 2.,
            aim_point: [4., 0., 20.],
            roll_deg: 0.,
            mount_type: "projected_east".into(),
            tower_id: "tower-1".into(),
        };
        let receiver = Receiver {
            centre: [5., 0., 20.],
            radius: 1.,
            height: 4.,
        };
        let config = serde_json::json!({"sunshape_mrad":2.51,"slope_error_mrad":2.6,"tracking_error_mrad":2.1,"receiver_quadrature_order":16});
        let result = cylinder_interception(&mirror, 0.8, receiver, &config).unwrap();
        assert!((result - 0.9614137).abs() < 1e-7);
    }

    #[test]
    fn mobile_bundle_rejects_unknown_schema() {
        use flate2::Compression;
        use flate2::write::GzEncoder;
        use std::io::Write;
        let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
        encoder
            .write_all(br#"{"schema_version":99,"plants":[]}"#)
            .unwrap();
        let error = read_mobile_bundle_gzip(&encoder.finish().unwrap()).unwrap_err();
        assert!(error.contains("unsupported mobile bundle schema"));
    }

    fn flat(id: &str, x: f64, z: f64) -> HeliostatInput {
        HeliostatInput {
            mirror_id: id.into(),
            centre: [x, 0., z],
            width: 2.,
            height: 2.,
            aim_point: [x, 0., 20.],
            roll_deg: 0.,
            mount_type: "projected_east".into(),
            tower_id: "tower-1".into(),
        }
    }

    #[test]
    fn duplicate_occluders_are_unioned_once() {
        let request = FieldRequest {
            mirrors: vec![flat("target", 0., 0.), flat("a", 1., 3.), flat("b", 1., 5.)],
            sun: [0., 0., 1.],
            target_indices: Some(vec![0]),
            target_overrides: None,
            conservative_filter: true,
        };
        let result = field_efficiencies(request).unwrap().remove(0);
        assert_abs_diff_eq!(result.eta_shadow, 0.5, epsilon = 1e-9);
        assert_abs_diff_eq!(result.eta_blocking, 0.5, epsilon = 1e-9);
        assert_abs_diff_eq!(result.eta_joint, 0.5, epsilon = 1e-9);
    }

    #[test]
    fn receiver_plane_rejects_beyond_mirror() {
        let mut target = flat("target", 0., 0.);
        target.aim_point = [0., 0., 10.];
        let request = FieldRequest {
            mirrors: vec![target, flat("beyond", 0., 12.)],
            sun: [0., 0., 1.],
            target_indices: Some(vec![0]),
            target_overrides: None,
            conservative_filter: true,
        };
        let result = field_efficiencies(request).unwrap().remove(0);
        assert_abs_diff_eq!(result.eta_shadow, 0., epsilon = 1e-9);
        assert_abs_diff_eq!(result.eta_blocking, 1., epsilon = 1e-9);
    }
}
