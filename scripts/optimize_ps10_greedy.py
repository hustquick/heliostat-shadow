#!/usr/bin/env python3
"""Greedy local field-improvement experiment for catalogue power-tower plants.

This is a *screened* annual representative-time search. Every mirror is
considered in every round. A fast full-plane screen proposes legal vacant
locations. The leading moves are accepted only after a complete field
shadow/blocking and receiver calculation. A final full-field evaluation is
written for the initial and final layouts.

Catalogue fields are parameterised reconstructions unless the selected layout
has an explicit coordinate survey. The result must therefore be treated as a
layout-method experiment, not a commissioned-field modification design.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from heliostat import Heliostat, mirror_normal
from optics import atmospheric_transmittance
from receiver import CylindricalReceiver
from simulation import PreparedField
from solar import clear_sky_irradiance, sun_vector
from tower import towers_from_config
from viewer.workspace import ViewerWorkspace

ROOT = Path(__file__).resolve().parents[1]


from scripts.annual_energy import Sample, weather_samples, evaluate_energy


def annual_representative_samples(config: dict) -> list[Sample]:
    """One daylight sample on each seasonal reference day; equal weights."""
    samples: list[Sample] = []
    for month, day in ((3, 21), (6, 21), (9, 21), (12, 21)):
        for hour in (12,):
            timestamp = pd.Timestamp(year=config['year'], month=month, day=day,
                                     hour=hour, tz=config['timezone'])
            solar = sun_vector(config['latitude'], config['longitude'], timestamp,
                               altitude=config['altitude_m'])
            dni = float(clear_sky_irradiance(
                config['latitude'], config['longitude'], timestamp,
                altitude=config['altitude_m'])['dni'].iloc[0])
            if solar.is_daylight and dni > 1.0:
                samples.append(Sample(timestamp, solar.sun_to_sky, dni))
    if not samples:
        raise RuntimeError('No daylight representative samples were generated')
    return samples


def receiver_for(config: dict) -> CylindricalReceiver:
    towers = towers_from_config(config)
    if len(towers) != 1:
        raise ValueError('This greedy mover currently optimizes one receiver at a time; '
                         'use a single-tower field or split a multi-tower field by assigned tower.')
    return towers[0].receiver


def retarget(mirror: Heliostat, centre_xy: np.ndarray, receiver: CylindricalReceiver) -> Heliostat:
    """Move a mirror centre and retain a rim aim point on the same receiver."""
    dx, dy = centre_xy - np.asarray(receiver.centre[:2])
    radial = float(np.hypot(dx, dy))
    if radial <= receiver.radius:
        raise ValueError('Candidate lies inside the receiver cylinder')
    aim = (receiver.centre[0] + receiver.radius * dx / radial,
           receiver.centre[1] + receiver.radius * dy / radial,
           receiver.centre[2])
    return replace(mirror, centre=(float(centre_xy[0]), float(centre_xy[1]), mirror.centre[2]),
                   aim_point=aim)


def fast_screen_scores(points: np.ndarray, mirror: Heliostat, samples: list[Sample],
                       receiver: CylindricalReceiver) -> np.ndarray:
    """Vectorized full-plane screen using only cosine and path transmission.

    Shadow/blocking and receiver interception are deliberately excluded here:
    they are recalculated exactly for the shortlisted locations.  This avoids
    running a receiver quadrature for every grid point of every mirror.
    """
    z = float(mirror.centre[2])
    centres = np.column_stack((points, np.full(len(points), z)))
    radial = points - np.asarray(receiver.centre[:2])
    radial_length = np.linalg.norm(radial, axis=1)
    aim = np.column_stack((np.asarray(receiver.centre[:2]) + receiver.radius * radial / radial_length[:, None],
                           np.full(len(points), receiver.centre[2])))
    reflected = aim - centres
    distance = np.linalg.norm(reflected, axis=1)
    reflected /= distance[:, None]
    total = np.zeros(len(points))
    for sample in samples:
        normal = sample.sun[None, :] + reflected
        normal /= np.linalg.norm(normal, axis=1)[:, None]
        cosine = np.clip(normal @ sample.sun, 0., None)
        total += sample.duration_hours * sample.dni * cosine * atmospheric_transmittance(distance)
    return total


def delivered_power(mirrors: list[Heliostat], samples: list[Sample], config: dict,
                    receiver: CylindricalReceiver) -> tuple[float, float]:
    """Return duration-weighted received Wh and energy-weighted optical efficiency.

    Legacy seasonal samples have unit weights, preserving the old numeric score.
    """
    from rust_core import optical_energy
    shared = optical_energy(mirrors, samples, config)
    if shared is not None:
        return shared['receiver_incident_kwh']*1000, shared['energy_weighted_optical_efficiency']
    delivered = normal = 0.0
    for sample in samples:
        field = PreparedField.from_mirrors(mirrors, sample.sun)
        result = field.evaluate(
            sample.dni, reflective_area_m2=config['reflective_area_m2'],
            atmospheric_model=config['atmospheric_model'],
            mirror_reflectivity=config['mirror_reflectivity'],
            mirror_cleanliness=config['mirror_cleanliness'], receiver=receiver,
            receiver_absorptivity=config['receiver_absorptivity'],
            receiver_thermal_efficiency=config['receiver_thermal_efficiency'],
            sunshape_mrad=config['sunshape_mrad'], slope_error_mrad=config['slope_error_mrad'],
            tracking_error_mrad=config['tracking_error_mrad'],
            receiver_quadrature_order=config['receiver_quadrature_order'])
        delivered += sample.duration_hours * float(result.receiver_incident_power_w.sum())
        normal += sample.duration_hours * float(result.incident_normal_power_w.sum())
    return delivered, delivered / normal if normal else 0.0


def legal_grid(mirrors: list[Heliostat], clearance: float, step: float) -> np.ndarray:
    xy = np.array([mirror.centre[:2] for mirror in mirrors])
    radii = np.linalg.norm(xy, axis=1)
    inner = max(0.0, float(np.quantile(radii, .01)) - step)
    outer = float(np.quantile(radii, .99)) + step
    extent = outer + step
    axis = np.arange(-extent, extent + step * .5, step)
    xx, yy = np.meshgrid(axis, axis)
    candidates = np.column_stack((xx.ravel(), yy.ravel()))
    radius = np.linalg.norm(candidates, axis=1)
    return candidates[(radius >= inner) & (radius <= outer)]


def grid_neighbours(mirrors: list[Heliostat], grid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Nearest and second-nearest mirror for every shared grid point."""
    xy = np.array([mirror.centre[:2] for mirror in mirrors])
    if len(mirrors) < 2:
        return np.full((len(grid),2),np.inf), np.zeros((len(grid),2),dtype=int)
    distances, indices = cKDTree(xy).query(grid, k=2)
    return np.asarray(distances), np.asarray(indices)


def screened_candidates(index: int, mirrors: list[Heliostat], grid: np.ndarray,
                        grid_distances: np.ndarray, grid_indices: np.ndarray,
                        clearance: float, count: int, samples: list[Sample], config: dict,
                        receiver: CylindricalReceiver, region: str) -> list[np.ndarray]:
    """Return only legal high-value full-plane destinations for one mirror."""
    xy = np.array([mirror.centre[:2] for mirror in mirrors])
    others = np.delete(xy, index, axis=0)
    # Only a point whose nearest mirror is the moved one changes its clearance
    # test; then its precomputed second-nearest distance becomes relevant.
    nearest_without_mover = np.where(grid_indices[:, 0] == index,
                                     grid_distances[:, 1], grid_distances[:, 0])
    legal = grid[nearest_without_mover >= clearance]
    # Include local offsets so the screen does not miss sub-grid improvements.
    current = xy[index]
    offsets = np.array([[0, 0], [12, 0], [-12, 0], [0, 12], [0, -12],
                        [9, 9], [9, -9], [-9, 9], [-9, -9]], dtype=float)
    local = current + offsets
    local = local[cKDTree(others).query(local, k=1)[0] >= clearance]
    legal = legal[np.linalg.norm(legal-np.asarray(receiver.centre[:2]),axis=1) > receiver.radius]
    local = local[np.linalg.norm(local-np.asarray(receiver.centre[:2]),axis=1) > receiver.radius]
    pool = legal if region == 'global' else local
    mirror = mirrors[index]
    scores = fast_screen_scores(pool, mirror, samples, receiver)
    order = np.argsort(scores)[::-1]
    chosen: list[np.ndarray] = []
    for row in order:
        point = pool[row]
        if np.linalg.norm(point - current) < 1e-6:
            continue
        if all(np.linalg.norm(point - existing) > 1e-6 for existing in chosen):
            chosen.append(point)
        if len(chosen) == count:
            break
    return chosen


def ranked_moves(mirrors: list[Heliostat], samples: list[Sample], config: dict,
                 receiver: CylindricalReceiver, grid: np.ndarray, clearance: float,
                 candidate_count: int, region: str) -> list[tuple[int, np.ndarray, float]]:
    """Rank every mover using a direct-power proxy before whole-field checks.

    The screening quantity deliberately has no shadow/blocking term. It is
    only a way to choose a small number of moves for the complete field
    calculation below; it can never itself approve a relocation.
    """
    options: list[tuple[int, np.ndarray, float]] = []
    distances, indices = grid_neighbours(mirrors, grid)
    for index in range(len(mirrors)):
        candidates = screened_candidates(index, mirrors, grid, distances, indices, clearance,
                                         candidate_count, samples, config, receiver, region)
        if not candidates:
            continue
        current_and_candidates = np.vstack((np.asarray(mirrors[index].centre[:2]), candidates))
        scores = fast_screen_scores(current_and_candidates, mirrors[index], samples, receiver)
        for destination, score in zip(candidates, scores[1:]):
            options.append((index, destination, float(score - scores[0])))
    return sorted(options, key=lambda option: option[2], reverse=True)


def save_layout(path: Path, mirrors: list[Heliostat]) -> None:
    pd.DataFrame([dict(mirror_id=mirror.mirror_id, x=mirror.centre[0], y=mirror.centre[1],
                       z=mirror.centre[2], width=mirror.width, height=mirror.height,
                       aim_x=mirror.aim_point[0], aim_y=mirror.aim_point[1], aim_z=mirror.aim_point[2],
                       roll_deg=mirror.roll_deg, mount_type=mirror.mount_type,
                       tower_id=mirror.tower_id) for mirror in mirrors]).to_csv(path, index=False)


def save_comparison_plot(path: Path, initial: list[Heliostat], final: list[Heliostat], plant_id: str) -> None:
    import matplotlib.pyplot as plt

    before = np.asarray([mirror.centre[:2] for mirror in initial])
    after = np.asarray([mirror.centre[:2] for mirror in final])
    fig, axes = plt.subplots(1, 2, figsize=(12, 6), sharex=True, sharey=True, layout='constrained')
    for axis, points, title in zip(axes, (before, after), ('Initial reconstructed field', 'After greedy local improvement')):
        axis.scatter(points[:, 0], points[:, 1], s=5, c='#1674d1', alpha=.75, linewidths=0)
        axis.scatter([0], [0], marker='^', c='#ef9b30', s=80, label='塔')
        axis.set_aspect('equal')
        axis.set_xlabel('East x (m)')
        axis.set_ylabel('North y (m)')
        axis.set_title(title)
        axis.grid(alpha=.18)
    fig.suptitle(f'{plant_id}: greedy search for maximum representative net gain')
    fig.savefig(path, dpi=180)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plant', default='ps10', help='Catalogue plant ID, for example ps10 or crescent-dunes.')
    parser.add_argument('--max-moves', type=int, default=60)
    parser.add_argument('--relative-gain-limit', type=float, default=1e-5,
                        help='Stop when validated global gain is below this fraction of representative field power.')
    parser.add_argument('--grid-step-m', type=float, default=30.)
    parser.add_argument('--candidates-per-mirror', type=int, default=1)
    parser.add_argument('--candidate-region', choices=('global', 'local'), default='global',
                        help='Search the whole legal field or only 9 local displacement directions.')
    parser.add_argument('--full-validation-candidates', type=int, default=3,
                        help='Top proxy candidates re-evaluated using the whole field before acceptance.')
    parser.add_argument('--objective', choices=('seasonal', 'annual-stratified', 'annual-hourly'), default='seasonal')
    parser.add_argument('--annual-evaluation', action='store_true', help='Evaluate both layouts over a complete hourly year; reject annual regression.')
    parser.add_argument('--output', type=Path,
                        help='Results directory; defaults to reports/<plant>_greedy_optimization.')
    args = parser.parse_args()
    if args.max_moves < 0 or args.relative_gain_limit < 0 or not np.isfinite(args.relative_gain_limit) or args.grid_step_m <= 0 or not np.isfinite(args.grid_step_m) or args.candidates_per_mirror < 1 or not 1 <= args.full_validation_candidates <= 64:
        parser.error('Invalid optimizer budget, threshold or grid settings')
    output = args.output or ROOT/'reports'/f'{args.plant}_greedy_optimization'
    output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix='heliostat-ps10-') as cache:
        workspace = ViewerWorkspace(ROOT, user_root=Path(cache))
        workspace.select(args.plant)
        model = workspace.active
        config, mirrors = model.config, list(model.mirrors)
        plant_name = workspace.metadata()['plant_name']
        initial = list(mirrors)
        receiver = receiver_for(config)
        if args.objective == 'seasonal':
            samples = annual_representative_samples(config)
            objective_provenance = dict(method='four_seasonal_noon_samples', is_annual_energy=False)
        else:
            samples, objective_provenance = weather_samples(model, stratified=args.objective == 'annual-stratified')
        search_config = dict(config)
        clearance = float(max(np.hypot(m.width, m.height) for m in mirrors) + 1.0)
        grid = legal_grid(mirrors, clearance, args.grid_step_m)
        baseline_search_power, _ = delivered_power(mirrors, samples, search_config, receiver)
        current_power = baseline_search_power
        threshold = baseline_search_power * args.relative_gain_limit
        history = []
        stop_reason = 'move_budget_exhausted'
        for step in range(1, args.max_moves + 1):
            print(f"Round {step}/{args.max_moves}: screening {len(mirrors)} mirrors over {len(samples)} samples", flush=True)
            options = ranked_moves(mirrors, samples, search_config, receiver, grid, clearance,
                                   args.candidates_per_mirror, args.candidate_region)
            winner = None
            shortlisted = options[:args.full_validation_candidates]
            from rust_core import optimize_energy_step
            decision = optimize_energy_step(mirrors, samples, search_config, shortlisted, threshold)
            if decision is not None:
                if decision['accepted']:
                    index, destination, local_gain_w = shortlisted[decision['candidate_ordinal']]
                    trial = list(mirrors)
                    trial[index] = retarget(trial[index], destination, receiver)
                    winner = (index, destination, local_gain_w, decision['gain_wh'], trial, decision['final_wh'])
            else:
                for index, destination, local_gain_w in shortlisted:
                    trial = list(mirrors)
                    trial[index] = retarget(trial[index], destination, receiver)
                    trial_power, _ = delivered_power(trial, samples, search_config, receiver)
                    exact_gain = trial_power - current_power
                    if winner is None or exact_gain > winner[3]:
                        winner = (index, destination, local_gain_w, exact_gain, trial, trial_power)
            if winner is None or winner[3] <= threshold:
                stop_reason = 'shortlist_gain_below_threshold'
                history.append(dict(step=step, accepted=False, stop_reason='no_global_gain_above_limit',
                                    best_proxy_gain=0. if not options else options[0][2],
                                    best_global_gain=0. if winner is None else winner[3]))
                break
            index, destination, local_gain_w, exact_gain, trial, trial_power = winner
            previous = mirrors[index]
            mirrors, current_power = trial, trial_power
            history.append(dict(step=step, accepted=True, mirror_id=previous.mirror_id,
                                from_x_m=previous.centre[0], from_y_m=previous.centre[1],
                                to_x_m=float(destination[0]), to_y_m=float(destination[1]),
                                proxy_gain=local_gain_w, exact_global_gain=exact_gain))
        annual_report = None
        if args.annual_evaluation or args.objective == 'annual-hourly':
            print("Starting independent full hourly-year validation", flush=True)
            hourly, provenance = weather_samples(model)
            before = evaluate_energy(initial, hourly, config, receiver)
            after = evaluate_energy(mirrors, hourly, config, receiver)
            accepted = after['receiver_incident_kwh'] >= before['receiver_incident_kwh']
            annual_report = dict(provenance=provenance, baseline=before, candidate=after, accepted=accepted)
            if not accepted:
                mirrors = initial
        baseline_power, baseline_eta = delivered_power(initial, samples, config, receiver)
        final_power, final_eta = delivered_power(mirrors, samples, config, receiver)
    if annual_report is not None:
        (output/'annual_energy.json').write_text(json.dumps(annual_report, ensure_ascii=False, indent=2)+'\n')
    pd.DataFrame(history).to_csv(output/'move_history.csv', index=False)
    save_layout(output/'ps10_greedy_layout.csv', mirrors)
    save_comparison_plot(output/'layout_comparison.png', initial, mirrors, args.plant)
    summary = dict(
        plant_id=args.plant, plant_name=plant_name,
        baseline=f'{plant_name}: catalogue parameterised reconstruction; not an as-built coordinate survey',
        stop_reason=stop_reason,
        convergence_claim=False,
        objective=args.objective, objective_provenance=objective_provenance,
        annual_validation_accepted=None if annual_report is None else annual_report["accepted"],
        objective_unit="Wh" if args.objective != "seasonal" else "representative W sum",
        sample_duration_hours=[s.duration_hours for s in samples],
        representative_samples=[sample.timestamp.isoformat() for sample in samples],
        mirrors=len(mirrors), grid_candidates=len(grid), clearance_m=clearance,
        search_accepted_moves=sum(row.get('accepted', False) for row in history),
        accepted_moves=0 if annual_report is not None and not annual_report['accepted'] else sum(row.get('accepted', False) for row in history),
        baseline_objective=baseline_power, final_objective=final_power,
        baseline_receiver_incident_w=baseline_power if args.objective == 'seasonal' else None,
        final_receiver_incident_w=final_power if args.objective == 'seasonal' else None,
        baseline_receiver_incident_kwh=baseline_power/1000 if args.objective != 'seasonal' else None,
        final_receiver_incident_kwh=final_power/1000 if args.objective != 'seasonal' else None,
        relative_receiver_power_gain=(final_power / baseline_power - 1.) if baseline_power else 0.,
        baseline_optical_efficiency=baseline_eta, final_optical_efficiency=final_eta,
        relative_gain_limit=args.relative_gain_limit,
        absolute_gain_threshold=threshold,
        absolute_gain_threshold_w=threshold if args.objective == 'seasonal' else None,
        full_validation_candidates=args.full_validation_candidates,
        candidate_region=args.candidate_region,
        search_receiver_quadrature_order=search_config['receiver_quadrature_order'],
        final_receiver_quadrature_order=config['receiver_quadrature_order'])
    (output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    (output/'RESULTS.md').write_text(
        f'# {plant_name} 镜场优化\n\n'
        f'评价目标：{args.objective}；单位：{summary["objective_unit"]}。\n\n'
        '每轮筛选全部镜面，候选移动经过完整全场复核；每次接受与最终评价使用相同接收器积分阶数。\n'
        '季节样本仅为筛选指标；分层年评价是近似；完整逐时复核才属于本气象年的年能量计算。\n'
        f'逐时全年复核是否通过：{summary["annual_validation_accepted"]}（None 表示未执行）。\n'
        f'最终目标相对收益：{summary["relative_receiver_power_gain"]*100:.4f}%。\n'
        '坐标来源仍为模型/研究重建；接收器入射能量不等于发电量。\n')
    print(json.dumps({k:v for k,v in summary.items() if k not in ("representative_samples", "sample_duration_hours")}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
