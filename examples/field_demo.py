"""Run a six-mirror synthetic field or a user-provided ENU layout."""

import argparse
from pathlib import Path

import pandas as pd

from blocking import blocking_efficiency
from field import load_layout
from shadow import shadow_efficiency
from solar import clear_sky_irradiance, sun_vector


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", type=Path, help="Documented ENU CSV layout")
    parser.add_argument("--latitude", type=float, default=37.562)
    parser.add_argument("--longitude", type=float, default=-5.330)
    parser.add_argument("--time", default="2026-06-21 08:00:00")
    parser.add_argument("--timezone", default="Europe/Madrid")
    parser.add_argument("--altitude", type=float, default=0.0, help="Site altitude above sea level (m)")
    args = parser.parse_args()
    layout = args.layout or Path(__file__).with_name("synthetic_layout.csv")
    mirrors = load_layout(layout)
    print("布局:", layout)
    if args.layout is None:
        print("仅为 6 面镜合成算例，非 Gemasolar 真实镜场；原点和尺寸为人为定义。")
    print("坐标须共享 ENU 米制原点；场址海拔与 CSV 中的相对 z 坐标是不同参数。")
    sun = sun_vector(args.latitude, args.longitude, args.time, args.timezone, altitude=args.altitude)
    print(sun.solar_pos[["elevation", "azimuth"]].to_string())
    if not sun.is_daylight:
        print("太阳中心位于地平线以下，跳过直射光镜场效率计算。")
        return
    irradiance = clear_sky_irradiance(args.latitude, args.longitude, args.time, args.timezone, altitude=args.altitude)
    print(f"晴空 DNI 估算: {irradiance.dni.iloc[0]:.2f} W/m²（非实测）")
    rows = [dict(mirror_id=mirror.mirror_id,
                 eta_shadow=shadow_efficiency(mirror, mirrors, sun.sun_to_sky),
                 eta_blocking=blocking_efficiency(mirror, mirrors, sun.sun_to_sky))
            for mirror in mirrors]
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda value: f"{value:.6f}"))
    print("两列分别为未受阴影/遮挡的面积比例；不将两者乘积当作精确联合效率。")


if __name__ == "__main__":
    main()
