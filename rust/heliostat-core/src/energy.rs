//! Shared duration-weighted optical energy evaluator for all client bridges.
use super::*;

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct EnergySample {
    pub sun: [f64; 3],
    pub dni: f64,
    pub duration_hours: f64,
}

pub fn energy(plant: &MobilePlant, samples: &[EnergySample]) -> Result<serde_json::Value, String> {
    if samples.is_empty() || plant.mirrors.is_empty() {
        return Err("energy inputs must not be empty".into());
    }
    if plant
        .config
        .get("atmospheric_model")
        .and_then(serde_json::Value::as_str)
        .is_some_and(|m| m != "clear_air_40km")
    {
        return Err("unsupported atmospheric model".into());
    }
    let receivers = receivers_from_config(&plant.config)?;
    let factor = |key: &str| -> Result<f64, String> {
        let x = plant
            .config
            .get(key)
            .and_then(serde_json::Value::as_f64)
            .unwrap_or(1.0);
        if !x.is_finite() || !(0.0..=1.0).contains(&x) {
            return Err(format!("invalid {key}"));
        }
        Ok(x)
    };
    let reflection = factor("mirror_reflectivity")? * factor("mirror_cleanliness")?;
    let area_override = plant
        .config
        .get("reflective_area_m2")
        .and_then(serde_json::Value::as_f64);
    let mut incident = 0.0;
    let mut received = 0.0;
    let mut rows = Vec::new();
    for sample in samples {
        let norm = dot(sample.sun, sample.sun).sqrt();
        if !sample.dni.is_finite()
            || sample.dni < 0.0
            || !sample.duration_hours.is_finite()
            || sample.duration_hours <= 0.0
            || !norm.is_finite()
            || (norm - 1.0).abs() > 1e-6
        {
            return Err("invalid DNI, duration or unit sun vector".into());
        }
        if sample.sun[2] <= 0.0 || sample.dni == 0.0 {
            rows.push(serde_json::json!({"receiver_incident_wh":0.0,"incident_normal_wh":0.0}));
            continue;
        }
        let mirrors = resolve_mobile_mirrors(plant, sample.sun)?;
        let field = PreparedField::new(mirrors, sample.sun).map_err(|e| e.to_string())?;
        let powers: Result<Vec<(f64, f64)>, String> = (0..field.mirrors.len())
            .into_par_iter()
            .map(|i| {
                let m = &field.mirrors[i];
                let area = area_override.unwrap_or(m.width * m.height);
                if !area.is_finite() || area <= 0.0 || area > m.width * m.height * (1.0 + 1e-6) {
                    return Err("invalid reflective area".into());
                }
                let g = field.efficiency(i, true, None).map_err(|e| e.to_string())?;
                let receiver = *receivers.get(&m.tower_id).ok_or("unknown target tower")?;
                let eta = g.eta_cosine
                    * g.eta_joint
                    * reflection
                    * atmospheric_transmittance(field.poses[i].receiver_distance)
                    * cylinder_interception(m, g.eta_cosine, receiver, &plant.config)?;
                let normal = area * sample.dni * sample.duration_hours;
                Ok((normal * eta, normal))
            })
            .collect();
        let powers = powers?;
        let r = powers.iter().map(|p| p.0).sum::<f64>();
        let n = powers.iter().map(|p| p.1).sum::<f64>();
        received += r;
        incident += n;
        rows.push(serde_json::json!({"receiver_incident_wh":r,"incident_normal_wh":n}));
    }
    Ok(serde_json::json!({"receiver_incident_kwh":received/1000.0,
        "incident_normal_kwh":incident/1000.0,
        "energy_weighted_optical_efficiency":if incident>0.0 {received/incident} else {0.0},
        "samples":rows,"metric":"receiver_incident_optical_energy","unit":"kWh"}))
}

pub fn energy_json(payload: &str) -> Result<String, String> {
    let value: serde_json::Value = serde_json::from_str(payload).map_err(|e| e.to_string())?;
    let plant: MobilePlant =
        serde_json::from_value(value["plant"].clone()).map_err(|e| e.to_string())?;
    let samples: Vec<EnergySample> =
        serde_json::from_value(value["samples"].clone()).map_err(|e| e.to_string())?;
    serde_json::to_string(&energy(&plant, &samples)?).map_err(|e| e.to_string())
}

/// A screened move cannot approve itself: re-evaluate the complete field.
pub fn optimize_step(
    plant: &MobilePlant,
    payload: &serde_json::Value,
) -> Result<serde_json::Value, String> {
    let samples: Vec<EnergySample> =
        serde_json::from_value(payload["samples"].clone()).map_err(|e| e.to_string())?;
    let candidates = payload["candidates"]
        .as_array()
        .ok_or("candidates missing")?;
    if candidates.len() > 64 {
        return Err("at most 64 shortlisted candidates per step".into());
    }
    let threshold = payload["threshold_wh"]
        .as_f64()
        .ok_or("threshold_wh missing")?;
    if !threshold.is_finite() || threshold < 0.0 {
        return Err("invalid gain threshold".into());
    }
    let receivers = receivers_from_config(&plant.config)?;
    if receivers.len() != 1 {
        return Err("mirror relocation currently requires a single tower".into());
    }
    let baseline = energy(plant, &samples)?["receiver_incident_kwh"]
        .as_f64()
        .unwrap()
        * 1000.0;
    let clearance = plant
        .mirrors
        .iter()
        .map(|m| m.width.hypot(m.height))
        .fold(0.0, f64::max)
        + 1.0;
    let mut best: Option<(f64, usize, MobilePlant, f64)> = None;
    for (ordinal, candidate) in candidates.iter().enumerate() {
        let i = candidate["index"]
            .as_u64()
            .ok_or("candidate index missing")? as usize;
        if i >= plant.mirrors.len() {
            return Err("candidate index out of bounds".into());
        }
        let xy: [f64; 2] =
            serde_json::from_value(candidate["xy"].clone()).map_err(|e| e.to_string())?;
        if !xy.iter().all(|x| x.is_finite()) {
            return Err("invalid destination".into());
        }
        if plant
            .mirrors
            .iter()
            .enumerate()
            .any(|(j, m)| j != i && (m.centre[0] - xy[0]).hypot(m.centre[1] - xy[1]) < clearance)
        {
            continue;
        }
        let m = &plant.mirrors[i];
        let receiver = *receivers.get(&m.tower_id).ok_or("unknown target tower")?;
        if (xy[0] - receiver.centre[0]).hypot(xy[1] - receiver.centre[1]) <= receiver.radius {
            continue;
        }
        let mut trial = plant.clone();
        trial.mirrors[i].centre[0] = xy[0];
        trial.mirrors[i].centre[1] = xy[1];
        trial.mirrors[i] = mirror_for_receiver(&trial.mirrors[i], &m.tower_id, receiver)?;
        let power = energy(&trial, &samples)?["receiver_incident_kwh"]
            .as_f64()
            .unwrap()
            * 1000.0;
        let gain = power - baseline;
        if best.as_ref().is_none_or(|b| gain > b.0) {
            best = Some((gain, ordinal, trial, power));
        }
    }
    match best {
        Some((gain, ordinal, trial, power)) if gain > threshold => Ok(
            serde_json::json!({"accepted":true,"candidate_ordinal":ordinal,
            "gain_wh":gain,"baseline_wh":baseline,"final_wh":power,"mirrors":trial.mirrors}),
        ),
        _ => Ok(
            serde_json::json!({"accepted":false,"baseline_wh":baseline,"final_wh":baseline,
            "best_gain_wh":best.as_ref().map(|b|b.0),"reason":"shortlist_gain_below_threshold","convergence_claim":false}),
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> MobilePlant {
        serde_json::from_value(serde_json::json!({"id":"test","name":"test","layout_status":"test","reported_mirrors":1,"source":"fixture","note":"",
            "config":{"reflective_area_m2":3.0,"mirror_reflectivity":0.9,"mirror_cleanliness":0.95,"receiver_quadrature_order":16,
            "towers":[{"id":"tower-1","receiver":{"centre":[5,0,20],"radius":1,"height":4}}]},
            "mirrors":[{"mirror_id":"M1","centre":[0,0,0],"width":2,"height":2,"aim_point":[4,0,20],"tower_id":"tower-1"}]})).unwrap()
    }
    #[test]
    fn duration_weight_and_night_are_physical() {
        let plant = fixture();
        let mut sample = EnergySample {
            sun: [0.0, 0.0, 1.0],
            dni: 800.0,
            duration_hours: 1.0,
        };
        let once = energy(&plant, &[sample.clone()]).unwrap();
        sample.duration_hours = 3.0;
        let three = energy(&plant, &[sample.clone()]).unwrap();
        assert!(
            (three["receiver_incident_kwh"].as_f64().unwrap()
                - 3.0 * once["receiver_incident_kwh"].as_f64().unwrap())
            .abs()
                < 1e-12
        );
        assert_eq!(three["incident_normal_kwh"], 7.2);
        sample.sun = [0.0, 0.0, -1.0];
        assert_eq!(
            energy(&plant, &[sample]).unwrap()["receiver_incident_kwh"],
            0.0
        );
    }
    #[test]
    fn reject_invalid_energy_inputs() {
        for sample in [
            EnergySample {
                sun: [0.0, 0.0, 1.0],
                dni: -1.0,
                duration_hours: 1.0,
            },
            EnergySample {
                sun: [0.0, 0.0, 1.0],
                dni: 800.0,
                duration_hours: 0.0,
            },
            EnergySample {
                sun: [0.0, 0.0, 2.0],
                dni: 800.0,
                duration_hours: 1.0,
            },
        ] {
            assert!(energy(&fixture(), &[sample]).is_err());
        }
    }
}
