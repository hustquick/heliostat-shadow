"""Optional Python bridge to the shared Rust shadow/blocking kernel."""

import json
import os

try:
    import _heliostat_rust
except ImportError:  # Pure-Python development remains a supported fallback.
    _heliostat_rust = None


def available() -> bool:
    return _heliostat_rust is not None and os.environ.get("HELIOSTAT_RUST_CORE", "1") != "0"


def version() -> str | None:
    return _heliostat_rust.CORE_VERSION if available() else None


def _mirror_payload(mirror):
    return {
        "mirror_id": mirror.mirror_id,
        "centre": mirror.centre,
        "width": mirror.width,
        "height": mirror.height,
        "aim_point": mirror.aim_point,
        "roll_deg": mirror.roll_deg,
        "mount_type": mirror.mount_type,
        "tower_id": mirror.tower_id,
    }


def field_efficiencies(mirrors, sun, *, target_indices=None, target_overrides=None,
                       conservative_filter=True):
    """Compute the field geometry in one Rust call, preserving mirror order."""
    if not available():
        return None
    payload = {
        "mirrors": [_mirror_payload(mirror) for mirror in mirrors],
        "sun": list(map(float, sun)),
        "target_indices": target_indices,
        "target_overrides": (None if target_overrides is None else
                             [_mirror_payload(mirror) for mirror in target_overrides]),
        "conservative_filter": bool(conservative_filter),
    }
    result = json.loads(_heliostat_rust.compute_field_json(json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"))))
    if target_indices is None and [row["mirror_id"] for row in result] != [
            mirror.mirror_id for mirror in mirrors]:
        raise RuntimeError("Rust core returned mirrors in an unexpected order")
    return result


def resolve_towers(mirrors, sun, config):
    """Shared serial full-field-gain assignment used on all four platforms."""
    if not available() or not hasattr(_heliostat_rust, "resolve_towers_json"):
        raise RuntimeError("双塔全场收益策略需要更新后的 Rust 内核，请重新构建安装包")
    plant = dict(id="assignment",name="assignment",layout_status="fixed",reported_mirrors=len(mirrors),source="model",note="",config=config,metadata={},weather=dict(start_utc="2023-01-01T00:00:00Z",step_seconds=3600,dni_w_m2=[],temperature_c=12.,source=""),mirrors=[_mirror_payload(m) for m in mirrors])
    return json.loads(_heliostat_rust.resolve_towers_json(json.dumps(dict(plant=plant,sun=list(map(float,sun))))))


def optical_energy(mirrors, samples, config):
    """Same energy kernel as Android/iOS; return None for the reference fallback."""
    if not available() or not hasattr(_heliostat_rust, 'evaluate_energy_json'):
        return None
    plant = dict(id='energy', name='energy', layout_status='model', reported_mirrors=len(mirrors),
                 source='model', note='', config=config, metadata={}, mirrors=[_mirror_payload(m) for m in mirrors])
    inputs = [dict(sun=list(map(float, s.sun)), dni=float(s.dni), duration_hours=float(s.duration_hours)) for s in samples]
    return json.loads(_heliostat_rust.evaluate_energy_json(json.dumps(dict(plant=plant, samples=inputs))))


def optimize_energy_step(mirrors, samples, config, candidates, threshold_wh):
    if not available() or not hasattr(_heliostat_rust, 'optimize_energy_step_json'):
        return None
    plant = dict(id='energy', name='energy', layout_status='model', reported_mirrors=len(mirrors),
                 source='model', note='', config=config, metadata={}, mirrors=[_mirror_payload(m) for m in mirrors])
    inputs = [dict(sun=list(map(float,s.sun)),dni=float(s.dni),duration_hours=float(s.duration_hours)) for s in samples]
    moves = [dict(index=int(i),xy=list(map(float,xy))) for i,xy,*_ in candidates]
    return json.loads(_heliostat_rust.optimize_energy_step_json(json.dumps(dict(plant=plant,samples=inputs,
        candidates=moves,threshold_wh=float(threshold_wh)))))


def analyze_instant(mirrors, config, payload):
    if not available() or not hasattr(_heliostat_rust, 'analyze_instant_json'):
        raise ValueError('指定时间/实时分析需要新版共享 Rust 内核，请重新构建应用')
    plant = dict(id=payload.get('plant_id', 'analysis'), name='analysis', layout_status='model',
                 reported_mirrors=len(mirrors), source='model', note='', config=config, metadata={},
                 mirrors=[_mirror_payload(m) for m in mirrors])
    return json.loads(_heliostat_rust.analyze_instant_json(json.dumps(dict(plant=plant, payload=payload))))
