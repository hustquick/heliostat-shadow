"""Compare reconstructed Gemasolar coordinates with literature Campo layouts."""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from datasets import read_pvgis_dni
from field import load_layout
from layouts import campo_radial_stagger_candidates, select_by_score
from optics import atmospheric_transmittance
from receiver import CylindricalReceiver
from simulation import PreparedField
from solar import sun_vector

ROOT = Path(__file__).resolve().parents[1]


def _markdown_table(frame):
    values = frame.copy()
    for column in values.select_dtypes(include="number"):
        values[column] = values[column].map(lambda value: f"{value:.5f}")
    rows = [[str(value) for value in row] for row in values.itertuples(index=False, name=None)]
    widths = [max(len(str(column)), *(len(row[index]) for row in rows))
              for index, column in enumerate(values.columns)]
    render = lambda row: "| " + " | ".join(value.ljust(widths[index])
        for index, value in enumerate(row)) + " |"
    return "\n".join([render(list(values.columns)),
        render(["-" * width for width in widths]), *(render(row) for row in rows)])


def _proxy_scores(mirrors, config):
    """Documented HFLCAL-like pre-trim proxy: annual cosine × atmosphere."""
    centres = np.array([m.centre for m in mirrors])
    aims = np.array([m.aim_point for m in mirrors])
    reflected = aims - centres
    distance = np.linalg.norm(reflected, axis=1)
    reflected /= distance[:, None]
    scores = np.zeros(len(mirrors))
    samples = 0
    for month, day in ((3, 21), (6, 21), (9, 21), (12, 21)):
        base = pd.Timestamp(year=2023, month=month, day=day, hour=12,
                            tz=config["timezone"])
        grid = pd.date_range(base - pd.Timedelta(hours=3), base + pd.Timedelta(hours=5), freq="10min")
        elevations = [sun_vector(config["latitude"], config["longitude"], timestamp,
                                 altitude=config["altitude_m"]).solar_pos.elevation.iloc[0]
                      for timestamp in grid]
        solar_noon = grid[int(np.argmax(elevations))]
        for offset in (-4, -2, 0, 2, 4):
            timestamp = solar_noon + pd.Timedelta(hours=offset)
            sun = sun_vector(config["latitude"], config["longitude"], timestamp,
                             altitude=config["altitude_m"])
            if not sun.is_daylight:
                continue
            normals = reflected + sun.sun_to_sky
            normals /= np.linalg.norm(normals, axis=1)[:, None]
            scores += np.maximum(0, normals @ sun.sun_to_sky)
            samples += 1
    return scores / samples * atmospheric_transmittance(distance)


def _campo(config, increments):
    candidates = campo_radial_stagger_candidates(
        mirror_width_m=config["mirror_width_m"], mirror_height_m=config["mirror_height_m"],
        tower_height_m=config["optical_height_m"], receiver_radius_m=config["receiver_radius_m"],
        radial_increments=increments, first_row_count=46, candidate_count=3864,
        separation_m=0,
    )
    return select_by_score(candidates, _proxy_scores(candidates, config), config["expected_mirrors"])


def _layout_stats(name, mirrors):
    xy = np.array([m.centre[:2] for m in mirrors])
    radius = np.linalg.norm(xy, axis=1)
    return dict(layout=name, mirror_count=len(mirrors), radius_min_m=radius.min(),
                radius_median_m=np.median(radius), radius_max_m=radius.max(),
                north_fraction=float((xy[:, 1] > 0).mean()),
                convex_bbox_area_ha=float(np.ptp(xy[:, 0]) * np.ptp(xy[:, 1]) / 1e4))


def _evaluate(name, mirrors, samples, config, receiver):
    rows = []
    for timestamp, weather in samples.iterrows():
        sun = sun_vector(config["latitude"], config["longitude"], timestamp,
                         altitude=config["altitude_m"], temperature=weather.temperature_c)
        prepared = PreparedField.from_mirrors(mirrors, sun.sun_to_sky)
        result = prepared.evaluate(
            weather.dni_w_m2, reflective_area_m2=config["reflective_area_m2"],
            atmospheric_model=config["atmospheric_model"],
            mirror_reflectivity=config["mirror_reflectivity"],
            mirror_cleanliness=config["mirror_cleanliness"], receiver=receiver,
            receiver_absorptivity=config["receiver_absorptivity"],
            receiver_thermal_efficiency=config["receiver_thermal_efficiency"],
            sunshape_mrad=config["sunshape_mrad"], slope_error_mrad=config["slope_error_mrad"],
            tracking_error_mrad=config["tracking_error_mrad"],
            receiver_quadrature_order=config["receiver_quadrature_order"],
        )
        normal = result.incident_normal_power_w.sum()
        cosine = result.incident_cosine_power_w.sum()
        rows.append(dict(
            layout=name, time_local=timestamp.tz_convert(config["timezone"]).isoformat(),
            dni_w_m2=weather.dni_w_m2,
            eta_cosine=float(cosine / normal),
            eta_joint=float(result.geometric_usable_power_w.sum() / cosine),
            eta_optical=float(result.receiver_incident_power_w.sum() / normal),
            receiver_incident_mw=float(result.receiver_incident_power_w.sum() / 1e6),
        ))
    return rows


def main():
    config = json.loads((ROOT / "data/gemasolar_config.json").read_text())
    output = ROOT / "reports/layout_comparison"
    output.mkdir(parents=True, exist_ok=True)
    actual = load_layout(ROOT / "data/processed/gemasolar_layout.csv")
    layouts = {
        "Gemasolar重建坐标": actual,
        "Campo-2013旧参数": _campo(config, (np.cos(np.pi / 6), 1.4, 2.0)),
        "Campo-2016改进参数": _campo(config, (np.cos(np.pi / 6), 1.0, 2.4)),
    }
    radiation, _ = read_pvgis_dni(
        ROOT / f'data/raw/radiation/pvgis_sarah3_{config["year"]}.json', config["year"])
    local_dates = radiation.index.tz_convert(config["timezone"]).strftime("%Y-%m-%d")
    samples = []
    for date in config["selected_local_dates"]:
        day = radiation.loc[local_dates == date]
        samples.append(day.loc[[day.dni_w_m2.idxmax()]])
    samples = pd.concat(samples)
    receiver = CylindricalReceiver((0, 0, config["optical_height_m"]),
                                   config["receiver_radius_m"], config["receiver_height_m"])
    stats = pd.DataFrame([_layout_stats(name, mirrors) for name, mirrors in layouts.items()])
    detail = pd.DataFrame(row for name, mirrors in layouts.items()
                          for row in _evaluate(name, mirrors, samples, config, receiver))
    summary = detail.groupby("layout", sort=False).apply(
        lambda g: pd.Series(dict(
            sample_count=len(g), mean_cosine_efficiency=g.eta_cosine.mean(),
            mean_joint_efficiency=g.eta_joint.mean(),
            mean_optical_efficiency=g.eta_optical.mean(),
            mean_receiver_incident_mw=g.receiver_incident_mw.mean(),
        )), include_groups=False).reset_index().merge(stats, on="layout")
    stats.to_csv(output / "layout_geometry.csv", index=False)
    detail.to_csv(output / "seasonal_design_points.csv", index=False)
    summary.to_csv(output / "summary.csv", index=False)

    plot_names = ["Reconstructed Gemasolar", "Campo 2013 parameters", "Campo 2016 parameters"]
    fig, axes = plt.subplots(1, 3, figsize=(15, 5), constrained_layout=True)
    for ax, plot_name, (_, mirrors) in zip(axes, plot_names, layouts.items()):
        xy = np.array([m.centre[:2] for m in mirrors])
        ax.scatter(xy[:, 0], xy[:, 1], s=1.2, color="#186f83")
        ax.scatter([0], [0], marker="^", s=40, color="#d04730")
        ax.set(title=plot_name, xlabel="East / m", ylabel="North / m", aspect="equal")
    fig.savefig(output / "layouts.png", dpi=180)
    plt.close(fig)

    lines = [
        "# Gemasolar 排布方式文献复现与同口径比较", "",
        "本阶段复现 Collado–Guallar Campo 三分区径向交错方法。它先生成 3,864 面候选镜，",
        "再按光学表现裁剪为 2,650 面。本实现严格采用文献给出的 DM、Nhel1=46、",
        "Dr1=cos(30°)、分区镜数翻倍和半节距交错规则。由于 HFLCAL 的原始逐镜裁剪程序未公开，",
        "候选裁剪使用明确记录的 20 个季节/时刻的平均余弦效率×大气透射率代理；因此这是",
        "“文献排布规则复现 + 本项目统一裁剪与光学评价”，不是作者 Campo 数值结果的逐点复制。", "",
        "## 方案", "",
        "- Gemasolar 重建坐标：公开地图提取的 2,650 面镜，用作实际排布参考。",
        "- Campo-2013 旧参数：Dr2=1.4、Dr3=2.0。",
        "- Campo-2016 改进参数：Dr2=1.0、Dr3=2.4；属于文献扫描并推荐的更密 zone 2 / 更疏 zone 3 组合。", "",
        "## 同口径结果", "",
        _markdown_table(summary), "",
        "比较采用 2023 年春分、夏至、秋分、冬至各自历史 DNI 最大的一个小时。所有方案共用",
        "140 m 瞄准高度、同一镜面/接收器参数和完整损失链。四点平均只用于快速、可复核的阶段比较，",
        "不代表文献的 TMY 年效率，也不代表全年最优结论。", "",
        "## 文献", "",
        "1. Collado & Guallar (2012), *Campo: Generation of regular heliostat fields*, DOI: 10.1016/j.renene.2012.03.011.",
        "2. Collado & Guallar (2013), *A review of optimized design layouts for solar power tower plants with campo code*, DOI: 10.1016/j.rser.2012.11.019.",
        "3. Collado & Guallar (2016), *Two-stages optimised design of the collector field of solar power tower plants*, DOI: 10.1016/j.solener.2016.06.065.",
    ]
    (output / "RESULTS.md").write_text("\n".join(lines) + "\n")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
