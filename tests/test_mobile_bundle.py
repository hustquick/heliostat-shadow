import gzip
import json
from pathlib import Path

from scripts.build_mobile_bundle import build_bundle


def test_mobile_bundle_is_self_contained_and_deterministic(tmp_path):
    first = tmp_path / "first.json.gz"
    second = tmp_path / "second.json.gz"
    summary = build_bundle(first, ["gemasolar", "ps10"])
    build_bundle(second, ["gemasolar", "ps10"])
    assert first.read_bytes() == second.read_bytes()
    payload = json.loads(gzip.decompress(first.read_bytes()))
    assert payload["schema_version"] == 1
    assert summary["plants"] == 2
    assert summary["mirrors"] == 2650 + 624
    assert {plant["id"] for plant in payload["plants"]} == {"gemasolar", "ps10"}
    for plant in payload["plants"]:
        assert plant["config"]["latitude"]
        assert len(plant["mirrors"]) > 0
        assert set(plant["mirrors"][0]) >= {
            "mirror_id", "centre", "width", "height", "aim_point", "tower_id"
        }
    ps10 = next(plant for plant in payload["plants"] if plant["id"] == "ps10")
    assert ps10["weather"]["temperature_source"] == "era5_land_reanalysis"
    assert len(ps10["weather"]["temperature_series_c"]) == len(ps10["weather"]["dni_w_m2"]) == 8760
    assert ps10["weather"]["temperature_series_c"][0] != 12.0


def test_mobile_builds_package_shared_bundle_and_viewer():
    root = Path(__file__).resolve().parents[1]
    android = (root / "scripts/build_android.sh").read_text()
    ios = (root / "scripts/build_ios.sh").read_text()
    project = (root / "ios/project.yml").read_text()
    for script in (android, ios):
        assert "scripts/build_mobile_bundle.py" in script
    assert 'cp -R "${ROOT}/viewer"' in android
    assert "../viewer" in project
    assert "../build/mobile" in project


def test_mobile_view_has_one_view_selector_and_offline_failure_message():
    root = Path(__file__).resolve().parents[1]
    html = (root / "viewer/index.html").read_text()
    css = (root / "viewer/style.css").read_text()
    app = (root / "viewer/app.js").read_text()
    assert 'id="viewMode"' in html
    assert '#overview, #focus { display: none; }' in css
    assert "离线计算内核未连接" in app
    assert "webglUnavailable" in app
