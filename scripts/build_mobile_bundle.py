#!/usr/bin/env python3
"""Compile built-in plant parameters and layouts into a deterministic mobile bundle."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
import pvlib

from viewer.workspace import ViewerWorkspace

SCHEMA_VERSION = 1


def mirror_payload(mirror):
    return {
        "mirror_id": mirror.mirror_id,
        "centre": [float(value) for value in mirror.centre],
        "width": float(mirror.width),
        "height": float(mirror.height),
        "aim_point": [float(value) for value in mirror.aim_point],
        "roll_deg": float(mirror.roll_deg),
        "mount_type": mirror.mount_type,
        "tower_id": mirror.tower_id,
    }


def build_bundle(output: Path, plant_ids: list[str] | None = None) -> dict:
    with tempfile.TemporaryDirectory(prefix="heliostat-mobile-") as user_root:
        workspace = ViewerWorkspace(ROOT, user_root=user_root)
        available = [key for key, value in workspace._plants.items() if value.get("builtin")]
        selected = plant_ids or available
        unknown = sorted(set(selected) - set(available))
        if unknown:
            raise ValueError(f"unknown built-in plants: {', '.join(unknown)}")
        plants = []
        for plant_id in selected:
            workspace.select(plant_id)
            model = workspace.active
            metadata = workspace.metadata()
            if model.clear_sky:
                location = pvlib.location.Location(
                    latitude=float(model.config["latitude"]),
                    longitude=float(model.config["longitude"]),
                    altitude=float(model.config.get("altitude_m", 0.0)),
                    tz="UTC",
                )
                weather_index = model.weather.index
                dni_values = location.get_clearsky(weather_index, model="ineichen")["dni"].fillna(0.0)
                weather_source = "pvlib Ineichen clear-sky, compiled at build time"
            else:
                weather_index = model.weather.index
                dni_values = pd.to_numeric(model.weather["dni_w_m2"], errors="raise")
                weather_source = "bundled historical DNI"
            metadata_base = dict(metadata)
            for key in ("mirror_ids", "timestamps", "plants", "active_plant"):
                metadata_base.pop(key, None)
            plants.append({
                "id": plant_id,
                "name": metadata["plant_name"],
                "layout_status": metadata["layout_status"],
                "reported_mirrors": metadata["reported_mirrors"],
                "source": metadata["source"],
                "note": metadata["note"],
                "config": model.config,
                "metadata": metadata_base,
                "weather": {
                    "start_utc": weather_index[0].isoformat(),
                    "step_seconds": 3600,
                    "dni_w_m2": [round(float(value), 3) for value in dni_values],
                    "temperature_c": 12.0,
                    "source": weather_source,
                },
                "mirrors": [mirror_payload(mirror) for mirror in model.mirrors],
            })
    bundle = {"schema_version": SCHEMA_VERSION, "plants": plants}
    encoded = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as zipped:
            zipped.write(encoded)
    return {"plants": len(plants), "mirrors": sum(len(p["mirrors"]) for p in plants),
            "compressed_bytes": output.stat().st_size, "uncompressed_bytes": len(encoded)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/mobile/plant_bundle.json.gz")
    parser.add_argument("--plant", action="append", dest="plants")
    args = parser.parse_args()
    print(json.dumps(build_bundle(args.output, args.plants), ensure_ascii=False))


if __name__ == "__main__":
    main()
