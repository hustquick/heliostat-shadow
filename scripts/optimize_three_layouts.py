"""Optimize and compare three 2,650-heliostat layout families."""

import json
from itertools import product
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from datasets import read_pvgis_dni
from field import load_layout
from layouts import (campo_radial_stagger_candidates, deform_radial_curves,
                     select_by_score, select_by_score_with_spacing_and_north_fraction,
                     value_field_spiral_candidates)
from optics import atmospheric_transmittance
from receiver import CylindricalReceiver
from simulation import PreparedField
from solar import sun_vector

ROOT = Path(__file__).resolve().parents[1]


def representative_suns(config):
    result = []
    for month, day in ((3, 21), (6, 21), (9, 21), (12, 21)):
        base = pd.Timestamp(year=config["year"], month=month, day=day, hour=12,
                            tz=config["timezone"])
        grid = pd.date_range(base - pd.Timedelta(hours=3), base + pd.Timedelta(hours=5), freq="10min")
        elevations = [sun_vector(config["latitude"], config["longitude"], t,
                                 altitude=config["altitude_m"]).solar_pos.elevation.iloc[0]
                      for t in grid]
        noon = grid[int(np.argmax(elevations))]
        for offset in (-4, -2, 0, 2, 4):
            value = sun_vector(config["latitude"], config["longitude"],
                               noon + pd.Timedelta(hours=offset), altitude=config["altitude_m"])
            if value.is_daylight:
                result.append(value.sun_to_sky)
    return result


def optical_proxy(mirrors, suns):
    centres = np.array([m.centre for m in mirrors])
    aims = np.array([m.aim_point for m in mirrors])
    reflected = aims - centres
    distance = np.linalg.norm(reflected, axis=1)
    reflected /= distance[:, None]
    score = np.zeros(len(mirrors))
    for sun in suns:
        normals = reflected + sun
        normals /= np.linalg.norm(normals, axis=1)[:, None]
        score += np.maximum(0, normals @ sun)
    return score / len(suns) * atmospheric_transmittance(distance)


def minimum_spacing(mirrors):
    xy = np.array([m.centre[:2] for m in mirrors])
    return float(cKDTree(xy).query(xy, k=2)[0][:, 1].min())


def choose(candidates, suns, count):
    scores = optical_proxy(candidates, suns)
    selected = select_by_score(candidates, scores, count)
    selected_scores = optical_proxy(selected, suns)
    return selected, float(selected_scores.sum())


def optimize_campo(config, suns):
    records = []
    for dr2, dr3 in product((0.9, 1.0, 1.1, 1.2), (1.8, 2.0, 2.2, 2.4, 2.6)):
        candidates = campo_radial_stagger_candidates(
            mirror_width_m=config["mirror_width_m"], mirror_height_m=config["mirror_height_m"],
            tower_height_m=config["optical_height_m"], receiver_radius_m=config["receiver_radius_m"],
            radial_increments=(np.cos(np.pi / 6), dr2, dr3), candidate_count=3864)
        field, objective = choose(candidates, suns, config["expected_mirrors"])
        records.append((objective, dict(dr2=dr2, dr3=dr3), field, minimum_spacing(field)))
    return max(records, key=lambda item: item[0]), records


def optimize_curved(config, suns):
    base = campo_radial_stagger_candidates(
        mirror_width_m=config["mirror_width_m"], mirror_height_m=config["mirror_height_m"],
        tower_height_m=config["optical_height_m"], receiver_radius_m=config["receiver_radius_m"],
        radial_increments=(np.cos(np.pi / 6), 0.9, 1.8), candidate_count=3864,
        separation_m=3.0)
    diagonal = np.hypot(config["mirror_width_m"], config["mirror_height_m"])
    records = []
    for north_south, ellipticity in product((-50., -25., 0., 25., 50.),
                                             (-35., -15., 0., 15., 35.)):
        candidates = deform_radial_curves(
            base, north_south_m=north_south, ellipticity_m=ellipticity,
            tower_height_m=config["optical_height_m"], receiver_radius_m=config["receiver_radius_m"])
        field, objective = choose(candidates, suns, config["expected_mirrors"])
        spacing = minimum_spacing(field)
        if spacing + 1e-9 >= diagonal:
            records.append((objective, dict(north_south_offset_m=north_south,
                                            ellipticity_offset_m=ellipticity, security_distance_m=3.0),
                            field, spacing))
    if not records:
        raise RuntimeError("No feasible non-circular curve layout")
    return max(records, key=lambda item: item[0]), records


def optimize_value_field(config, suns):
    records = []
    for spacing, boundary, north_south, ellipticity, selected_north_fraction in product(
            (16.0, 18.0, 20.0, 22.0), (950, 1100),
            (0.0, 0.08), (-0.05, 0.0, 0.05), (0.6, 0.7, 0.8)):
        candidates = value_field_spiral_candidates(
            candidate_count=12000, inner_radius_m=80, boundary_radius_m=boundary,
            north_south=north_south, ellipticity=ellipticity,
            mirror_width_m=config["mirror_width_m"], mirror_height_m=config["mirror_height_m"],
            tower_height_m=config["optical_height_m"], receiver_radius_m=config["receiver_radius_m"])
        scores = optical_proxy(candidates, suns)
        try:
            field = select_by_score_with_spacing_and_north_fraction(
                candidates, scores, config["expected_mirrors"], spacing,
                selected_north_fraction)
        except ValueError:
            continue
        objective = float(optical_proxy(field, suns).sum())
        records.append((objective, dict(spacing_m=spacing, boundary_radius_m=boundary,
                                        north_south=north_south, ellipticity=ellipticity,
                                        selected_north_fraction=selected_north_fraction),
                        field, minimum_spacing(field)))
    return max(records, key=lambda item: item[0]), records


def exact_samples(config):
    radiation, _ = read_pvgis_dni(
        ROOT / f'data/raw/radiation/pvgis_sarah3_{config["year"]}.json', config["year"])
    local_dates = radiation.index.tz_convert(config["timezone"]).strftime("%Y-%m-%d")
    return pd.concat([day.loc[[day.dni_w_m2.idxmax()]] for date in config["selected_local_dates"]
                      if not (day := radiation.loc[local_dates == date]).empty])


def exact_evaluate(name, mirrors, samples, config, receiver):
    rows = []
    for timestamp, weather in samples.iterrows():
        sun = sun_vector(config["latitude"], config["longitude"], timestamp,
                         altitude=config["altitude_m"], temperature=weather.temperature_c)
        result = PreparedField.from_mirrors(mirrors, sun.sun_to_sky).evaluate(
            weather.dni_w_m2, reflective_area_m2=config["reflective_area_m2"],
            atmospheric_model=config["atmospheric_model"], mirror_reflectivity=config["mirror_reflectivity"],
            mirror_cleanliness=config["mirror_cleanliness"], receiver=receiver,
            receiver_absorptivity=config["receiver_absorptivity"],
            receiver_thermal_efficiency=config["receiver_thermal_efficiency"],
            sunshape_mrad=config["sunshape_mrad"], slope_error_mrad=config["slope_error_mrad"],
            tracking_error_mrad=config["tracking_error_mrad"],
            receiver_quadrature_order=config["receiver_quadrature_order"])
        normal = result.incident_normal_power_w.sum()
        cosine = result.incident_cosine_power_w.sum()
        rows.append(dict(layout=name, time_local=timestamp.tz_convert(config["timezone"]).isoformat(),
                         dni_w_m2=weather.dni_w_m2, eta_cosine=cosine / normal,
                         eta_joint=result.geometric_usable_power_w.sum() / cosine,
                         eta_optical=result.receiver_incident_power_w.sum() / normal,
                         receiver_incident_mw=result.receiver_incident_power_w.sum() / 1e6))
    return rows


def save_layout(path, mirrors):
    pd.DataFrame([dict(mirror_id=m.mirror_id, x=m.centre[0], y=m.centre[1], z=m.centre[2],
                       width=m.width, height=m.height, aim_x=m.aim_point[0], aim_y=m.aim_point[1],
                       aim_z=m.aim_point[2], roll_deg=m.roll_deg, mount_type=m.mount_type)
                      for m in mirrors]).to_csv(path, index=False)


def markdown_table(frame):
    copy = frame.copy()
    for column in copy.select_dtypes(include="number"):
        copy[column] = copy[column].map(lambda value: f"{value:.5f}")
    return "| " + " | ".join(copy.columns) + " |\n| " + " | ".join("---" for _ in copy.columns) + " |\n" + "\n".join(
        "| " + " | ".join(map(str, row)) + " |" for row in copy.itertuples(index=False, name=None))


def main():
    config = json.loads((ROOT / "data/gemasolar_config.json").read_text())
    output = ROOT / "reports/three_layout_optimization"
    output.mkdir(parents=True, exist_ok=True)
    suns = representative_suns(config)
    optimizers = [("Campo优化圆形交错", optimize_campo),
                  ("非圆曲线交错", optimize_curved),
                  ("价值场自由排布", optimize_value_field)]
    best = {}
    search_rows = []
    samples = exact_samples(config)
    receiver = CylindricalReceiver((0, 0, config["optical_height_m"]),
                                   config["receiver_radius_m"], config["receiver_height_m"])
    for name, optimizer in optimizers:
        _, records = optimizer(config, suns)
        ranked = sorted(records, key=lambda item: item[0], reverse=True)
        if name == "Campo优化圆形交错":
            shortlist = ranked[:5]
        elif name == "非圆曲线交错":
            available_offsets = sorted({item[1]["north_south_offset_m"] for item in records})
            shortlist = [max((item for item in records
                              if item[1]["north_south_offset_m"] == value),
                             key=lambda item: item[0]) for value in available_offsets]
        else:
            shortlist = [max((item for item in records
                              if item[1]["selected_north_fraction"] == fraction
                              and item[1]["spacing_m"] == spacing), key=lambda item: item[0])
                         for fraction in (0.6, 0.7, 0.8)
                         for spacing in (16.0, 18.0, 20.0, 22.0)]
        exact_scores = {}
        for item in shortlist:
            key = json.dumps(item[1], sort_keys=True)
            detail = pd.DataFrame(exact_evaluate(name, item[2], samples, config, receiver))
            exact_scores[key] = float(detail.receiver_incident_mw.mean())
        winner = max(shortlist, key=lambda item: exact_scores[json.dumps(item[1], sort_keys=True)])
        best[name] = winner
        for objective, parameters, _, spacing in records:
            key = json.dumps(parameters, sort_keys=True)
            search_rows.append(dict(layout=name, objective_proxy=objective,
                                    exact_receiver_incident_mw=exact_scores.get(key),
                                    shortlisted=key in exact_scores,
                                    minimum_spacing_m=spacing, parameters=json.dumps(parameters)))
        save_layout(output / ({"Campo优化圆形交错": "campo.csv", "非圆曲线交错": "curved.csv",
                               "价值场自由排布": "value_field.csv"}[name]), winner[2])

    reference = load_layout(ROOT / "data/processed/gemasolar_layout.csv")
    fields = {"Gemasolar重建参考": reference, **{name: value[2] for name, value in best.items()}}
    details = pd.DataFrame(row for name, field in fields.items()
                           for row in exact_evaluate(name, field, samples, config, receiver))
    summaries = []
    for name, group in details.groupby("layout", sort=False):
        field = fields[name]
        xy = np.array([m.centre[:2] for m in field])
        radius = np.linalg.norm(xy, axis=1)
        summaries.append(dict(layout=name, mean_cosine_efficiency=group.eta_cosine.mean(),
                              mean_joint_efficiency=group.eta_joint.mean(),
                              mean_optical_efficiency=group.eta_optical.mean(),
                              mean_receiver_incident_mw=group.receiver_incident_mw.mean(),
                              minimum_spacing_m=minimum_spacing(field), radius_max_m=radius.max(),
                              north_fraction=(xy[:, 1] > 0).mean(),
                              bbox_area_ha=np.ptp(xy[:, 0]) * np.ptp(xy[:, 1]) / 1e4))
    summary = pd.DataFrame(summaries)
    pd.DataFrame(search_rows).to_csv(output / "parameter_search.csv", index=False)
    details.to_csv(output / "seasonal_design_points.csv", index=False)
    summary.to_csv(output / "summary.csv", index=False)
    (output / "best_parameters.json").write_text(json.dumps(
        {name: dict(proxy_objective=value[0], parameters=value[1],
                    minimum_spacing_m=value[3]) for name, value in best.items()},
        ensure_ascii=False, indent=2) + "\n")

    fig, axes = plt.subplots(2, 2, figsize=(12, 11), constrained_layout=True)
    english = ["Reconstructed reference", "Optimized Campo", "Non-circular curves", "Value-field free layout"]
    for ax, title, (_, field) in zip(axes.flat, english, fields.items()):
        xy = np.array([m.centre[:2] for m in field])
        ax.scatter(xy[:, 0], xy[:, 1], s=1.2, color="#176f82")
        ax.scatter([0], [0], marker="^", s=35, color="#d44c32")
        ax.set(title=title, xlabel="East / m", ylabel="North / m", aspect="equal")
    fig.savefig(output / "optimized_layouts.png", dpi=180)
    plt.close(fig)

    reference_row = summary.loc[summary.layout == "Gemasolar重建参考"].iloc[0]
    campo_row = summary.loc[summary.layout == "Campo优化圆形交错"].iloc[0]
    curved_row = summary.loc[summary.layout == "非圆曲线交错"].iloc[0]
    value_row = summary.loc[summary.layout == "价值场自由排布"].iloc[0]
    campo_power_delta = 100 * (campo_row.mean_receiver_incident_mw /
                               reference_row.mean_receiver_incident_mw - 1)
    campo_area_delta = 100 * (campo_row.bbox_area_ha / reference_row.bbox_area_ha - 1)
    curved_power_delta = 100 * (curved_row.mean_receiver_incident_mw /
                                reference_row.mean_receiver_incident_mw - 1)
    value_power_delta = 100 * (value_row.mean_receiver_incident_mw /
                               reference_row.mean_receiver_incident_mw - 1)

    report = ["# 三类定日镜场排布的创建、比较与参数优化", "",
              "三种方案均固定为 2,650 面同尺寸定日镜。参数搜索以四季、每日五个太阳时刻的",
              "平均余弦效率×大气透射率作为第一阶段快速代理目标；再从各类方案选取覆盖不同",
              "密度、曲线和方位容量的候选，用四季历史 DNI 峰值时刻执行精确多边形阴影/遮挡和完整接收器",
              "光学链。最终参数按精确平均接收器入射功率选择，避免快速代理偏爱过密镜场。", "", "## 最优参数", "",
              "```json", json.dumps({name: value[1] for name, value in best.items()},
                                      ensure_ascii=False, indent=2), "```", "",
              "## 精确复算结果", "", markdown_table(summary), "",
              "## 当前搜索域内的结论", "",
              f"Campo 最优场的接收器入射功率相对重建参考场变化 {campo_power_delta:+.2f}%，",
              f"同时矩形包络面积变化 {campo_area_delta:+.2f}%。非圆曲线场采用真实非零形变，",
              f"其入射功率相对参考场变化 {curved_power_delta:+.2f}%；这说明非圆边界本身不保证更高效率，",
              "曲线参数、镜距和逐镜取舍仍需联合优化。", "",
              f"价值场自由排布的平均余弦效率为 {value_row.mean_cosine_efficiency:.5f}，但阴影/遮挡",
              f"联合效率仅为 {value_row.mean_joint_efficiency:.5f}，最终入射功率相对参考场变化",
              f"{value_power_delta:+.2f}%。单镜价值代理没有描述镜间耦合，不能单独作为自由排布的最终目标。", "",
              "Gemasolar 重建坐标只作为现实参考，没有参与三种生成方法的参数搜索。当前结果是",
              "四个代表时刻比较，不是全年 TMY 优化；下一阶段应对候选参数前沿进行全年逐时复算。"]
    (output / "RESULTS.md").write_text("\n".join(report) + "\n")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
