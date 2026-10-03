"""Load explicitly documented ENU layouts; no coordinate conversion is inferred."""

import csv
from pathlib import Path

from heliostat import Heliostat

COLUMNS = ("mirror_id", "x", "y", "z", "width", "height", "aim_x", "aim_y", "aim_z", "roll_deg")


def load_layout(path: str | Path) -> list[Heliostat]:
    """Read UTF-8/BOM CSV containing COLUMNS (additional columns are allowed).

    Coordinates/sizes are metres; roll is degrees. A source/readme must supply
    origin, elevation datum, axes and provenance. Geographic degrees are NOT
    converted or auto-detected. Empty data, duplicate IDs, missing/nonnumeric
    values, invalid dimensions and coincident centre/aim points raise ValueError.
    """
    mirrors = []
    identifiers = set()
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValueError("Layout CSV is empty")
        if len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("Layout CSV contains duplicate column names")
        missing = set(COLUMNS) - set(reader.fieldnames)
        if missing:
            raise ValueError(f"Missing layout columns: {', '.join(sorted(missing))}")
        for row in reader:
            try:
                if None in row:
                    raise ValueError("Row has more fields than the header")
                values = {key: float(row[key]) for key in COLUMNS if key != "mirror_id"}
                mirror = Heliostat(
                    mirror_id=row["mirror_id"],
                    centre=tuple(values[key] for key in ("x", "y", "z")),
                    width=values["width"], height=values["height"],
                    aim_point=tuple(values[key] for key in ("aim_x", "aim_y", "aim_z")),
                    roll_deg=values["roll_deg"],
                    mount_type=row.get("mount_type", "projected_east"),
                    tower_id=row.get("tower_id", "tower-1"),
                )
                if mirror.mirror_id in identifiers:
                    raise ValueError(f"Duplicate mirror_id: {mirror.mirror_id}")
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid layout at CSV line {reader.line_num}: {exc}") from exc
            identifiers.add(mirror.mirror_id)
            mirrors.append(mirror)
    if not mirrors:
        raise ValueError("Layout CSV contains no mirrors")
    return mirrors
