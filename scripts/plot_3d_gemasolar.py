"""Create a source-backed 3D Gemasolar field diagnostic snapshot."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np
import pandas as pd

from field import load_layout
from heliostat import mirror_normal, mirror_vertices, reflection_direction
from solar import sun_vector

ROOT = Path(__file__).resolve().parents[1]


def _cylinder(ax, centre, radius, height, *, color="#777777", alpha=.25):
    theta = np.linspace(0, 2 * np.pi, 48)
    z = np.linspace(centre[2] - height / 2, centre[2] + height / 2, 2)
    theta, z = np.meshgrid(theta, z)
    ax.plot_surface(centre[0] + radius * np.cos(theta),
                    centre[1] + radius * np.sin(theta), z,
                    color=color, alpha=alpha, linewidth=0, shade=False)


def _equal_axes(ax, points):
    points = np.asarray(points, dtype=float)
    lo, hi = points.min(axis=0), points.max(axis=0)
    centre = (lo + hi) / 2
    span = max(float((hi - lo).max()), 1.)
    ax.set_xlim(centre[0] - span / 2, centre[0] + span / 2)
    ax.set_ylim(centre[1] - span / 2, centre[1] + span / 2)
    ax.set_zlim(min(-span * .03, lo[2] - 1), max(hi[2] + 1, 145.))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timestamp", help="UTC ISO timestamp; default is peak calculated hour")
    parser.add_argument("--mirror-id", help="highlight a target mirror; default is lowest eta_joint")
    parser.add_argument("--output", type=Path,
                        help="output PNG; default reports/gemasolar_2023/field_3d_snapshot.png")
    args = parser.parse_args()

    output_dir = ROOT / "reports" / "gemasolar_2023"
    output = args.output or output_dir / "field_3d_snapshot.png"
    config = json.loads((ROOT / "data" / "gemasolar_config.json").read_text())
    mirrors = load_layout(ROOT / "data" / "processed" / "gemasolar_layout.csv")
    hourly = pd.read_csv(output_dir / "hourly_summary.csv")
    calculated = hourly[hourly.status == "calculated"]
    if calculated.empty:
        raise ValueError("No calculated daylight rows are available")
    timestamp = args.timestamp or calculated.loc[calculated.geometric_usable_mw.idxmax(), "time_utc"]
    with gzip.open(output_dir / "per_mirror.csv.gz", "rt", encoding="utf-8") as stream:
        detail = pd.read_csv(stream)
    snapshot = detail[detail.time_utc == timestamp].copy()
    if len(snapshot) != len(mirrors):
        raise ValueError(f"Snapshot {timestamp} has {len(snapshot)} rows, expected {len(mirrors)}")
    by_id = snapshot.set_index("mirror_id")
    target_id = args.mirror_id or str(by_id.eta_joint.idxmin())
    if target_id not in by_id.index:
        raise ValueError(f"Unknown mirror_id: {target_id}")
    if calculated[calculated.time_utc == timestamp].empty:
        raise ValueError(f"Timestamp {timestamp} is not a calculated row")

    ts = pd.Timestamp(timestamp)
    sun = sun_vector(config["latitude"], config["longitude"], ts,
                     altitude=config["altitude_m"])
    sun_dir = np.asarray(sun.sun_to_sky, dtype=float)
    vertices, normals = [], []
    for mirror in mirrors:
        normal = mirror_normal(sun_dir, reflection_direction(mirror))
        normals.append(normal)
        vertices.append(mirror_vertices(mirror, normal))
    vertices, normals = np.asarray(vertices), np.asarray(normals)
    eta = np.asarray([by_id.loc[m.mirror_id, "eta_joint"] for m in mirrors])
    centres = np.asarray([m.centre for m in mirrors])
    aims = np.asarray([m.aim_point for m in mirrors])
    receiver = np.array([0., 0., config["optical_height_m"]])
    index = next(i for i, m in enumerate(mirrors) if m.mirror_id == target_id)
    target = mirrors[index]
    target_centre = np.asarray(target.centre)
    reflected = reflection_direction(target)
    target_normal = normals[index]

    fig = plt.figure(figsize=(12, 9))
    ax = fig.add_subplot(111, projection="3d")
    cmap, colour_norm = plt.get_cmap("YlOrBr"), plt.Normalize(0, 1)
    ax.add_collection3d(Poly3DCollection(vertices, facecolors=cmap(colour_norm(eta)),
                                          edgecolors="#555555", linewidths=.08, alpha=.85))
    ax.add_collection3d(Poly3DCollection([vertices[index]], facecolors=[(1, 1, 1, 0)],
                                          edgecolors="#D62728", linewidths=2.2))
    ax.scatter(centres[:, 0], centres[:, 1], centres[:, 2], s=1, c=eta,
               cmap=cmap, norm=colour_norm, alpha=.18, depthshade=False)
    ax.scatter(aims[:, 0], aims[:, 1], aims[:, 2], c="#333333", s=4,
               marker=".", alpha=.45, depthshade=False, label="Aim points")
    ax.scatter(*receiver, c="#333333", s=24, marker="+", depthshade=False)
    _cylinder(ax, receiver, config["receiver_radius_m"], config["receiver_height_m"])
    ax.plot([0, 0], [0, 0], [0, config["optical_height_m"]], color="#555555",
            lw=2, alpha=.45, label="Tower / receiver axis")
    span = max(np.ptp(centres[:, 0]), np.ptp(centres[:, 1]))
    ray_length = max(float(np.linalg.norm(receiver - target_centre)), span * .12)
    ax.quiver(*target_centre, *(-sun_dir), length=ray_length * .12, color="#1F77B4",
              linewidth=2, arrow_length_ratio=.12, label="Sunlight propagation -s")
    ax.quiver(*target_centre, *reflected, length=ray_length, color="#D62728",
              linewidth=2, arrow_length_ratio=.06, label="Target reflection r_i")
    ax.quiver(*target_centre, *target_normal, length=ray_length * .16, color="#2CA02C",
              linewidth=2, arrow_length_ratio=.15, label="Target normal n_i")
    ax.scatter(*target_centre, c="#D62728", s=30, depthshade=False)
    ax.text(*target_centre, f"  {target_id}", color="#D62728", fontsize=8)
    all_points = np.vstack([centres, vertices.reshape(-1, 3), receiver[None, :]])
    _equal_axes(ax, all_points)
    ax.set(xlabel="East x (m)", ylabel="North y (m)", zlabel="Up z (m)")
    local = ts.tz_convert(config["timezone"])
    ax.set_title(f"Gemasolar 3D field diagnostic | {local.isoformat()}\n"
                 f"colour = eta_joint; target {target_id}; eta_joint={by_id.loc[target_id, 'eta_joint']:.4f}")
    ax.legend(loc="upper left", fontsize=8)
    fig.colorbar(plt.cm.ScalarMappable(norm=colour_norm, cmap=cmap), ax=ax,
                 pad=.08, shrink=.65, label="Joint usable fraction")
    fig.text(.02, .012, "Public map-reconstructed layout; ENU axes; geometry and optical errors are model scenarios.",
             fontsize=9)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=170, bbox_inches="tight")
    plt.close(fig)
    print(f"Created {output} for {timestamp}; target={target_id}; eta_joint={by_id.loc[target_id, 'eta_joint']:.6f}")


if __name__ == "__main__":
    main()
