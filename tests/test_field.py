"""Layout parsing, physical input validation and useful failure locations."""

import pytest

from field import load_layout

HEADER = "mirror_id,x,y,z,width,height,aim_x,aim_y,aim_z,roll_deg\n"
ROW = "001,0,0,2,4,3,0,0,100,0\n"


def test_load_bom_and_preserve_string_ids(tmp_path):
    path = tmp_path / "layout.csv"
    path.write_text(HEADER + ROW, encoding="utf-8-sig")
    mirrors = load_layout(path)
    assert len(mirrors) == 1
    assert mirrors[0].mirror_id == "001"
    assert mirrors[0].centre == (0, 0, 2)
    assert mirrors[0].width == 4


def test_optional_tower_id_is_loaded_and_legacy_rows_use_primary_tower(tmp_path):
    legacy = tmp_path / "legacy.csv"
    legacy.write_text(HEADER + ROW)
    assert load_layout(legacy)[0].tower_id == 'tower-1'
    multi = tmp_path / "multi.csv"
    multi.write_text(HEADER.strip() + ",tower_id\n" + ROW.strip() + ",east\n")
    assert load_layout(multi)[0].tower_id == 'east'


@pytest.mark.parametrize("content,match", [
    ("", "empty"), (HEADER, "no mirrors"), ("mirror_id,x\na,0\n", "Missing"),
    (HEADER + ROW + ROW, "line 3.*Duplicate"),
    (HEADER + ROW.replace(",4,3,", ",-4,3,"), "line 2.*width"),
    (HEADER + ROW.replace(",4,3,", ",nan,3,"), "line 2.*width"),
    (HEADER + ROW.replace(",0,0,100,", ",0,0,2,"), "line 2.*aim"),
    (HEADER + ROW.replace(",4,3,", ",oops,3,"), "line 2"),
    (HEADER + "001,0,0\n", "line 2"),
    (HEADER + ROW.strip() + ",extra\n", "line 2"),
    (HEADER.strip() + ",x\n", "duplicate column"),
])
def test_invalid_layout(tmp_path, content, match):
    path = tmp_path / "layout.csv"
    path.write_text(content)
    with pytest.raises(ValueError, match=match):
        load_layout(path)
