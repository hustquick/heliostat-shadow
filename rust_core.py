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
