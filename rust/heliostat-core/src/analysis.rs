//! Arbitrary-instant analysis shared by desktop Python and both mobile bridges.
use super::*;
use chrono::{DateTime, Utc};

pub fn instant(
    plant: &MobilePlant,
    environment: &MobilePlant,
    payload: &serde_json::Value,
) -> Result<serde_json::Value, String> {
    let timestamp = payload["time"].as_str().ok_or("请选择带时区的分析时间")?;
    let time = timestamp
        .parse::<DateTime<Utc>>()
        .map_err(|_| "分析时间必须包含时区")?;
    if !(1900..=2100).contains(&chrono::Datelike::year(&time)) {
        return Err("分析年份须在 1900–2100 年之间".into());
    }
    let number = |name: &str, low: f64, high: f64| -> Result<f64, String> {
        let value = payload[name]
            .as_f64()
            .ok_or_else(|| format!("分析参数缺失：{name}"))?;
        if !value.is_finite() || !(low..=high).contains(&value) {
            return Err(format!("分析参数超出范围：{name}"));
        }
        Ok(value)
    };
    let dni = number("dni", 0.0, 2000.0)?;
    let temperature = number("temperature_c", -90.0, 70.0)?;
    let pressure = number("pressure_hpa", 100.0, 1100.0)?;
    for key in ["mirror_reflectivity", "mirror_cleanliness"] {
        if let Some(value) = plant.config.get(key) {
            let v = value.as_f64().ok_or("反射率或清洁度不是有效数值")?;
            if !v.is_finite() || !(0.0..=1.0).contains(&v) {
                return Err("反射率或清洁度须在 0–1 之间".into());
            }
        }
    }
    if plant.mirrors.is_empty() {
        return Err("当前镜场没有定日镜".into());
    }
    let mirror = payload["mirror"]
        .as_str()
        .unwrap_or(&plant.mirrors[0].mirror_id);
    if !plant.mirrors.iter().any(|m| m.mirror_id == mirror) {
        return Err("目标镜编号不存在".into());
    }
    let mut selected = plant.clone();
    for key in ["latitude", "longitude", "altitude_m", "timezone"] {
        if let Some(value) = environment.config.get(key) {
            selected.config[key] = value.clone();
        }
    }
    selected.config["tower_assignment_strategy"] = serde_json::json!("independent");
    selected.config["analysis_pressure_hpa"] = serde_json::json!(pressure);
    selected.weather = MobileWeather {
        start_utc: time.to_rfc3339(),
        step_seconds: 3600,
        dni_w_m2: vec![dni],
        temperature_c: temperature,
        temperature_series_c: vec![temperature],
        temperature_source: payload["weather_source"]
            .as_str()
            .unwrap_or("manual")
            .into(),
        source: payload["weather_source"]
            .as_str()
            .unwrap_or("manual")
            .into(),
    };
    let runtime = MobileRuntime {
        bundle: MobileBundle {
            schema_version: 1,
            plants: vec![selected],
        },
        active: 0,
    };
    let frame = runtime.frame(&time.to_rfc3339())?;
    let daylight = frame["daylight"].as_bool().unwrap_or(false);
    if payload["target_only"].as_bool() == Some(true) {
        return Ok(
            serde_json::json!({"timestamp":frame["timestamp"],"plant_id":plant.id,"target":runtime.target(&time.to_rfc3339(),mirror)?}),
        );
    }
    let count = plant.mirrors.len();
    let efficiencies = if daylight {
        runtime.efficiencies(&time.to_rfc3339())?
    } else {
        serde_json::json!({"timestamp":frame["timestamp"],"metric":"eta_optical","label":"总光学效率", "values":vec![0.0;count], "factors":{}, "minimum":0.0,"maximum":0.0,"mean":0.0,"mirror_count":count,
            "tower_ids":plant.mirrors.iter().map(|m| &m.tower_id).collect::<Vec<_>>(),"formula":"太阳在地平线以下，接收光学功率为零"})
    };
    let values = efficiencies["values"].as_array().ok_or("效率结果无效")?;
    let area_override = plant.config["reflective_area_m2"].as_f64();
    let mut area = 0.0;
    let mut weighted = 0.0;
    for (m, eta) in plant.mirrors.iter().zip(values) {
        let a = area_override.unwrap_or(m.width * m.height);
        if !a.is_finite() || a <= 0.0 || a > m.width * m.height * (1.0 + 1e-6) {
            return Err("有效反射面积无效".into());
        }
        area += a;
        weighted += a * eta.as_f64().ok_or("效率结果无效")?;
    }
    let mean_factor = |name: &str| -> serde_json::Value {
        if !daylight {
            return serde_json::Value::Null;
        }
        let entries = efficiencies["factors"][name].as_array();
        match entries {
            Some(v) => {
                serde_json::json!(v.iter().filter_map(|x| x.as_f64()).sum::<f64>() / count as f64)
            }
            None => serde_json::Value::Null,
        }
    };
    let factors: serde_json::Map<String, serde_json::Value> = [
        "eta_cosine",
        "eta_shadow",
        "eta_blocking",
        "eta_joint",
        "mirror_reflectivity",
        "mirror_cleanliness",
        "eta_atmosphere",
        "eta_intercept",
    ]
    .into_iter()
    .map(|k| (k.into(), mean_factor(k)))
    .collect();
    Ok(
        serde_json::json!({"timestamp":frame["timestamp"],"local_time":frame["local_time"],"plant_id":plant.id,
        "daylight":daylight,"weather":{"dni":dni,"temperature_c":temperature,"pressure_hpa":pressure,"source":payload["weather_source"],"time":payload["weather_time"]},
        "mean":weighted/area,"arithmetic_mean":efficiencies["mean"],"minimum":efficiencies["minimum"],"maximum":efficiencies["maximum"],"mirror_count":count,
        "reflective_area_m2":area,"receiver_incident_power_w":if daylight {weighted*dni}else{0.0},"factors":factors,
        "frame":frame,"target":runtime.target(&time.to_rfc3339(),mirror)?,"efficiencies":efficiencies,
        "metric":"tracking_optical_efficiency","formula":"余弦 × 联合阴影遮挡 × 反射率 × 清洁度 × 沿程透过率 × 接收器截获率"}),
    )
}

pub fn instant_json(input: &str) -> Result<String, String> {
    let value: serde_json::Value = serde_json::from_str(input).map_err(|e| e.to_string())?;
    let plant: MobilePlant =
        serde_json::from_value(value["plant"].clone()).map_err(|e| e.to_string())?;
    let result = instant(&plant, &plant, &value["payload"])?;
    serde_json::to_string(&result).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn plant() -> MobilePlant {
        serde_json::from_str::<MobileBundle>(r#"{"schema_version":1,"plants":[{"id":"test","name":"Test field","layout_status":"built-in","reported_mirrors":1,"source":"fixture","note":"fixture","config":{"latitude":1,"longitude":0,"altitude_m":0,"timezone":"UTC","year":2023,"mirror_reflectivity":0.92,"mirror_cleanliness":0.95,"receiver_quadrature_order":16,"towers":[{"id":"tower-1","receiver":{"centre":[5,0,20],"radius":1,"height":4}}]},"metadata":{"timezone":"UTC"},"weather":{"start_utc":"2023-03-21T12:00:00+00:00","step_seconds":3600,"dni_w_m2":[800.0],"temperature_c":12.0,"source":"fixture"},"mirrors":[{"mirror_id":"M1","centre":[0,0,0],"width":2,"height":2,"aim_point":[4,0,20],"tower_id":"tower-1"}]}]}"#).unwrap().plants.remove(0)
    }
    fn payload() -> serde_json::Value {
        serde_json::json!({"time":"2026-03-21T12:23:45Z","dni":800.0,"temperature_c":20.0,"pressure_hpa":1000.0,"mirror":"M1"})
    }
    #[test]
    fn arbitrary_instant_is_non_mutating_and_physical() {
        let p = plant();
        let before = serde_json::to_string(&p).unwrap();
        let r = instant(&p, &p, &payload()).unwrap();
        assert_eq!(r["timestamp"], "2026-03-21T12:23:45+00:00");
        assert_eq!(r["daylight"], true);
        let eta = r["mean"].as_f64().unwrap();
        assert!(eta > 0.0 && eta <= 1.0);
        assert!(
            (r["receiver_incident_power_w"].as_f64().unwrap() - eta * 4.0 * 800.0).abs() < 1e-8
        );
        assert_eq!(r["factors"]["mirror_reflectivity"], 0.92);
        assert_eq!(serde_json::to_string(&p).unwrap(), before);
        let mut night = payload();
        night["time"] = serde_json::json!("2026-03-21T00:23:45Z");
        let r = instant(&p, &p, &night).unwrap();
        assert_eq!(r["mean"], 0.0);
        assert_eq!(r["receiver_incident_power_w"], 0.0);
    }
    #[test]
    fn rejects_bad_weather_dates_and_mirrors() {
        let p = plant();
        for (key, value) in [
            ("dni", serde_json::json!(null)),
            ("dni", serde_json::json!(-1)),
            ("pressure_hpa", serde_json::json!(1500)),
            ("temperature_c", serde_json::json!(100)),
            ("time", serde_json::json!("2026-03-21T12:00")),
            ("time", serde_json::json!("2101-03-21T12:00:00Z")),
            ("mirror", serde_json::json!("missing")),
        ] {
            let mut v = payload();
            v[key] = value;
            assert!(instant(&p, &p, &v).is_err());
        }
    }
    #[test]
    fn native_route_and_json_bridge_agree() {
        let p = plant();
        let v = payload();
        let expected = instant(&p, &p, &v).unwrap();
        let mut rt = MobileRuntime {
            bundle: MobileBundle {
                schema_version: 1,
                plants: vec![p.clone()],
            },
            active: 0,
        };
        let routed = rt.request("POST", "analysis/instant", &v).unwrap();
        assert_eq!(routed, expected);
        let encoded =
            instant_json(&serde_json::json!({"plant":p,"payload":v}).to_string()).unwrap();
        let decoded = serde_json::from_str::<serde_json::Value>(&encoded).unwrap();
        assert_eq!(decoded["timestamp"], expected["timestamp"]);
        for key in ["mean", "minimum", "maximum", "receiver_incident_power_w"] {
            assert!(
                (decoded[key].as_f64().unwrap() - expected[key].as_f64().unwrap()).abs() < 1e-10
            );
        }
    }
    #[test]
    fn bound_environment_and_target_only_are_supported() {
        let mut p = plant();
        let mut source = p.clone();
        source.config["longitude"] = serde_json::json!(180.0);
        p.config["source_plant_id"] = serde_json::json!("source");
        source.id = "source".into();
        let mut rt = MobileRuntime {
            bundle: MobileBundle {
                schema_version: 1,
                plants: vec![p, source],
            },
            active: 0,
        };
        assert_eq!(
            rt.request("POST", "analysis/instant", &payload()).unwrap()["daylight"],
            false
        );
        let mut v = payload();
        v["target_only"] = serde_json::json!(true);
        let result = rt.request("POST", "analysis/instant", &v).unwrap();
        assert!(result.get("efficiencies").is_none());
        assert!(result.get("target").is_some());
    }
}
