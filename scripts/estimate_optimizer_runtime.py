#!/usr/bin/env python3
"""Measure one PS10 optimization round and estimate catalogue runtimes.

The reported durations are planning estimates, not service-level guarantees.
They assume the default four representative times, one candidate per mirror,
three whole-field validations per round, and the existing Rust geometry core.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import pandas as pd

from optimize_ps10_greedy import (ROOT, annual_representative_samples, delivered_power,
                                  grid_neighbours, legal_grid, ranked_moves, receiver_for)
from viewer.workspace import ViewerWorkspace


def human_duration(seconds: float) -> str:
    if seconds < 1:
        return f'{seconds:.2f} s'
    if seconds < 90:
        return f'{seconds:.0f} s'
    if seconds < 5400:
        return f'{seconds / 60:.1f} min'
    return f'{seconds / 3600:.1f} h'


def main() -> None:
    output = ROOT/'reports/optimizer_runtime_estimates'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='heliostat-runtime-') as cache:
        workspace = ViewerWorkspace(ROOT, user_root=Path(cache))
        workspace.select('ps10')
        model = workspace.active
        config, mirrors = model.config, list(model.mirrors)
        receiver = receiver_for(config)
        samples = annual_representative_samples(config)
        search_config = dict(config, receiver_quadrature_order=16)
        clearance = (mirrors[0].width ** 2 + mirrors[0].height ** 2) ** .5 + 1.
        grid = legal_grid(mirrors, clearance, 30.)

        # Warm the extension and time the Python screen independently.
        delivered_power(mirrors, samples[:1], search_config, receiver)
        started = time.perf_counter()
        ranked_moves(mirrors, samples, search_config, receiver, grid, clearance, 1)
        screening_seconds = time.perf_counter() - started
        started = time.perf_counter()
        delivered_power(mirrors, samples[:1], search_config, receiver)
        field_sample_seconds = time.perf_counter() - started

    catalog = json.loads((ROOT/'data/power_tower_catalog.json').read_text())
    reference_count = len(mirrors)
    samples_per_round = len(samples)
    validation_candidates = 3
    # The Rust field core uses a spatial tree, but denser fields still create
    # more candidate overlaps.  N^1.15 is a conservative interpolation from
    # the measured PS10 one-sample full-field evaluation.
    rows = []
    for item in catalog:
        count = int(item.get('reported_heliostats') or 2650)
        towers = len(item.get('model_towers', [])) or 1
        scale = (count / reference_count) ** 1.15
        estimated_screen = screening_seconds * (count / reference_count) ** 1.5
        estimated_field_sample = field_sample_seconds * scale
        per_round = estimated_screen + validation_candidates * samples_per_round * estimated_field_sample
        rows.append(dict(
            plant_id=item['id'], plant_name=item['name'], mirrors=count, towers=towers,
            optimization_scope=('single-tower ready' if towers == 1 else 'multi-tower assignment required'),
            estimated_screen_s=estimated_screen,
            estimated_full_field_sample_s=estimated_field_sample,
            estimated_round_s=per_round,
            estimated_30_moves_s=30 * per_round,
            estimated_60_moves_s=60 * per_round,
        ))
    frame = pd.DataFrame(rows)
    for column in ('estimated_screen_s', 'estimated_full_field_sample_s', 'estimated_round_s',
                   'estimated_30_moves_s', 'estimated_60_moves_s'):
        frame[column.replace('_s', '_text')] = frame[column].map(human_duration)
    frame.to_csv(output/'catalogue_estimates.csv', index=False)
    summary = dict(
        calibration_plant='ps10', calibration_mirrors=reference_count,
        representative_samples=samples_per_round,
        grid_candidates=len(grid), screening_seconds=screening_seconds,
        one_full_field_sample_seconds=field_sample_seconds,
        validation_candidates_per_round=validation_candidates,
        geometry_core='Rust via _heliostat_rust',
        scaling_model='screen N^1.5; full geometry N^1.15; planning estimate only')
    (output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    lines = [
        '# 镜场贪心优化时间估算', '',
        '基准在本机以 PS10 的 624 面参数化场测得；几何计算调用 Rust 内核。每轮假定四个代表时刻、'
        '每镜一个候选位置、排名前三个候选执行完整全场复核。时间是规划估计，实际会随镜场密度、'
        '接收器、候选格点、CPU 和时刻样本数变化。', '',
        f"- PS10 候选筛选：{human_duration(screening_seconds)}",
        f"- PS10 单个时刻完整场计算：{human_duration(field_sample_seconds)}",
        '- 目录中的双塔场需先加入逐镜跨塔分配优化，当前单塔优化器不会假装支持。', '',
        '| 电厂 | 镜数 | 30 次移动估计 | 60 次移动估计 | 适用性 |',
        '| --- | ---: | ---: | ---: | --- |',
    ]
    for row in rows:
        lines.append(f"| {row['plant_name']} | {row['mirrors']:,} | {human_duration(row['estimated_30_moves_s'])} | {human_duration(row['estimated_60_moves_s'])} | {row['optimization_scope']} |")
    (output/'RESULTS.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
