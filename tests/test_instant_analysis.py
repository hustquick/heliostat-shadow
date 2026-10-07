"""Run the actual shared Rust library through desktop and mobile C APIs."""
import ctypes
import gzip
import json
import pytest
import importlib
_heliostat_rust = pytest.importorskip("_heliostat_rust")
from viewer.workspace import ViewerWorkspace
from viewer.model import ROOT
from rust_core import analyze_instant, _mirror_payload


def test_instant_desktop_mobile_parity_and_preserved_data(tmp_path):
    workspace = ViewerWorkspace(ROOT, tmp_path)
    model = workspace.active
    mirrors = model.mirrors[:3]
    payload = dict(time='2026-10-07T12:34:56Z', dni=800., temperature_c=21., pressure_hpa=1000.,
                   mirror=mirrors[0].mirror_id, plant_id=workspace.active_id)
    before_config = json.dumps(model.config, sort_keys=True)
    before_files = {str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    desktop = analyze_instant(mirrors, model.config, payload)
    assert desktop['daylight']
    assert 0 < desktop['mean'] <= 1
    assert desktop['receiver_incident_power_w'] == pytest.approx(desktop['mean']*desktop['reflective_area_m2']*800)
    try:
        extension = importlib.import_module('_heliostat_rust._heliostat_rust')
    except ModuleNotFoundError:
        extension = _heliostat_rust
    lib = ctypes.CDLL(extension.__file__)
    lib.heliostat_mobile_initialize_gzip.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
    lib.heliostat_mobile_initialize_gzip.restype=ctypes.c_void_p
    lib.heliostat_mobile_request_json.argtypes=[ctypes.c_char_p]
    lib.heliostat_mobile_request_json.restype=ctypes.c_void_p
    lib.heliostat_free_string.argtypes=[ctypes.c_void_p]
    def decoded(pointer):
        assert pointer
        try: return json.loads(ctypes.string_at(pointer))
        finally: lib.heliostat_free_string(pointer)
    plant=dict(id=workspace.active_id,name='test',layout_status='model',reported_mirrors=3,source='test',note='',config=model.config,metadata={},mirrors=[_mirror_payload(m) for m in mirrors])
    compressed=gzip.compress(json.dumps(dict(schema_version=1,plants=[plant])).encode())
    initialized=decoded(lib.heliostat_mobile_initialize_gzip(ctypes.create_string_buffer(compressed),len(compressed)))
    assert 'error' not in initialized
    mobile=decoded(lib.heliostat_mobile_request_json(json.dumps(dict(method='POST',path='analysis/instant',payload=payload)).encode()))
    assert mobile == desktop
    assert json.dumps(model.config,sort_keys=True) == before_config
    assert {str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before_files
    night=analyze_instant(mirrors,model.config,dict(payload,time='2026-10-07T00:00:00Z'))
    assert night['mean'] == night['receiver_incident_power_w'] == 0
    for key,value in [('dni',None),('dni',-1),('pressure_hpa',2000),('mirror','missing')]:
        with pytest.raises(ValueError): analyze_instant(mirrors,model.config,dict(payload,**{key:value}))
