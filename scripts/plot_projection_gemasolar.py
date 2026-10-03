"""Plot the shared 2D shadow/blocking projection for one target mirror."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Polygon as MatplotlibPolygon
import numpy as np
import pandas as pd
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon

from field import load_layout
from simulation import PreparedField
from solar import sun_vector

ROOT = Path(__file__).resolve().parents[1]


def _parts(geometry):
    if geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, (MultiPolygon, GeometryCollection)):
        return [part for child in geometry.geoms for part in _parts(child)]
    return []


def _add_geometry(ax, geometry, *, facecolor, edgecolor, alpha, linestyle="-"):
    for polygon in _parts(geometry):
        xy = np.asarray(polygon.exterior.coords)
        ax.add_patch(MatplotlibPolygon(xy, closed=True, facecolor=facecolor,
                                       edgecolor=edgecolor, alpha=alpha,
                                       linewidth=1, linestyle=linestyle))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timestamp", help="UTC ISO timestamp; default is peak calculated hour")
    parser.add_argument("--mirror-id", help="target mirror; default is lowest eta_joint")
    parser.add_argument("--output", type=Path,
                        help="output PNG; default reports/gemasolar_2023/projection_diagnostic.png")
    args = parser.parse_args()

    output_dir = ROOT / "reports" / "gemasolar_2023"
    output = args.output or output_dir / "projection_diagnostic.png"
    config = json.loads((ROOT / "data" / "gemasolar_config.json").read_text())
    mirrors = load_layout(ROOT / "data" / "processed" / "gemasolar_layout.csv")
    hourly = pd.read_csv(output_dir / "hourly_summary.csv")
    calculated = hourly[hourly.status == "calculated"]
    timestamp = args.timestamp or calculated.loc[calculated.geometric_usable_mw.idxmax(), "time_utc"]
    with gzip.open(output_dir / "per_mirror.csv.gz", "rt", encoding="utf-8") as stream:
        detail = pd.read_csv(stream)
    snapshot = detail[detail.time_utc == timestamp].set_index("mirror_id")
    if len(snapshot) != len(mirrors):
        raise ValueError(f"Snapshot {timestamp} is incomplete")
    target_id = args.mirror_id or str(snapshot.eta_joint.idxmin())
    ids = [mirror.mirror_id for mirror in mirrors]
    if target_id not in ids:
        raise ValueError(f"Unknown mirror_id: {target_id}")
    target_index = ids.index(target_id)
    weather = calculated[calculated.time_utc == timestamp]
    if weather.empty:
        raise ValueError(f"Timestamp {timestamp} is not a calculated row")
    ts = pd.Timestamp(timestamp)
    historical = pd.read_csv(ROOT / "data/processed/gemasolar_dni_2023.csv")
    sample = historical.loc[pd.to_datetime(historical.time_utc, utc=True) == ts].iloc[0]
    sun = sun_vector(config["latitude"], config["longitude"], ts,
                     altitude=config["altitude_m"], temperature=float(sample.temperature_c))
    field = PreparedField.from_mirrors(mirrors, sun.sun_to_sky)
    diagnostic = field.target_projection_diagnostics(target_index)
    target = diagnostic["target_polygon"]
    shadow = diagnostic["shadow"]
    blocking = diagnostic["blocking"]
    visible = target.difference(shadow["union_polygon"].union(blocking["union_polygon"]))

    fig, axes = plt.subplots(1, 2, figsize=(13, 6), sharex=True, sharey=True,
                             layout="constrained")
    for ax, mode, title, color in ((axes[0], shadow, "Shadow footprint on target mirror", "#2878B5"),
                                   (axes[1], blocking, "Blocking footprint on target mirror", "#D17A22")):
        _add_geometry(ax, target, facecolor="#F4F1DE", edgecolor="#222222", alpha=.7,
                      linestyle="--")
        for polygon in mode["polygons"]:
            _add_geometry(ax, polygon, facecolor=color, edgecolor=color, alpha=.32)
        _add_geometry(ax, mode["union_polygon"], facecolor="none", edgecolor=color,
                      alpha=1., linestyle="-")
        _add_geometry(ax, visible, facecolor="#43A047", edgecolor="#43A047", alpha=.35)
        ax.set_aspect("equal")
        ax.grid(alpha=.2)
        ax.set_title(f"{title}\n{len(mode['candidate_ids'])} candidates; "
                     f"{len(mode['occluder_ids'])} positive-overlap occluders")
        ax.set_xlabel("Target-plane u (m)")
        ax.set_ylabel("Target-plane v (m)")
        min_x, min_y, max_x, max_y = target.bounds
        pad = .08 * max(max_x - min_x, max_y - min_y, 1.)
        ax.set_xlim(min_x - pad, max_x + pad)
        ax.set_ylim(min_y - pad, max_y + pad)
    handles = [Patch(facecolor="#F4F1DE", edgecolor="#222222", label="Target mirror"),
               Patch(facecolor="#43A047", alpha=.35, label="Joint visible area"),
               Patch(facecolor="#2878B5", alpha=.32, label="Shadow candidates"),
               Patch(facecolor="#D17A22", alpha=.32, label="Blocking candidates")]
    axes[0].legend(handles=handles, loc="upper right", fontsize=8)
    local = ts.tz_convert(config["timezone"])
    fig.suptitle(f"Shared target-plane diagnostic | {local.isoformat()} | target {target_id}\n"
                 f"eta_shadow={snapshot.loc[target_id, 'eta_shadow']:.4f}; "
                 f"eta_blocking={snapshot.loc[target_id, 'eta_blocking']:.4f}; "
                 f"eta_joint={snapshot.loc[target_id, 'eta_joint']:.4f}", fontsize=12)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=170, bbox_inches="tight")
    plt.close(fig)
    print(f"Created {output} for {timestamp}; target={target_id}; "
          f"shadow_occluders={len(shadow['occluder_ids'])}; blocking_occluders={len(blocking['occluder_ids'])}")


if __name__ == "__main__":
    main()
