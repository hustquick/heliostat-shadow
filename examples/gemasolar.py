"""Solar-vector and clear-sky DNI example; no real field layout imported."""

import argparse

from solar import clear_sky_irradiance, sun_vector


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--time", default="2026-06-21 12:00:00")
    parser.add_argument("--timezone", default="Europe/Madrid")
    args = parser.parse_args()

    # Approximate site from the preceding discussion; survey data still TBD.
    result = sun_vector(37.562, -5.330, args.time, args.timezone)
    print("Gemasolar 近似场址示例：37.562°N, 5.330°W")
    print("默认海拔 0 m、气温 12°C；未导入真实镜场布局。")
    print(result.solar_pos[["elevation", "apparent_elevation", "azimuth"]].to_string())
    print("ENU，镜面 → 太阳 s:", result.sun_to_sky)
    print("ENU，太阳光传播 d_sun = -s:", result.ray_dir)
    print("太阳中心在理想地平线上方:", result.is_daylight)
    irradiance = clear_sky_irradiance(37.562, -5.330, args.time, args.timezone)
    print("晴空模型估算（非实测），单位 W/m²:")
    print(irradiance.to_string())
    if not result.is_daylight:
        print("夜间或地平线边界：后续应跳过镜场直射光计算。")


if __name__ == "__main__":
    main()
