"""Exercise the actual Swift save path with JSON null and failed native replies."""
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'darwin' or not shutil.which('swiftc'), reason='Swift Foundation requires macOS')
def test_ios_rearrangement_saves_json_null_without_property_list_crash(tmp_path):
    root = Path(__file__).resolve().parents[1]
    harness = tmp_path / 'StateCheck.swift'
    harness.write_text(r'''
import Foundation
var failed = false
func heliostat_compute_field_json(_ p: UnsafePointer<CChar>) -> UnsafeMutablePointer<CChar>? { nil }
func heliostat_mobile_initialize_gzip(_ p: UnsafePointer<UInt8>, _ n: UInt) -> UnsafeMutablePointer<CChar>? { nil }
func heliostat_free_string(_ p: UnsafeMutablePointer<CChar>) { free(p) }
func heliostat_mobile_request_json(_ p: UnsafePointer<CChar>) -> UnsafeMutablePointer<CChar>? {
    let request = try! JSONSerialization.jsonObject(with: Data(String(cString:p).utf8)) as! [String:Any]
    if request["path"] as? String == "state/export" {
        let mirrors = (0..<27135).map { ["mirror_id":"R\($0)", "centre":[0,0,0]] as [String:Any] }
        let state:[String:Any] = ["schema_version":1,"active_plant":"campo","plants":[["metadata":["source":NSNull()],"mirrors":mirrors]]]
        return strdup(String(data:try! JSONSerialization.data(withJSONObject:state),encoding:.utf8)!)
    }
    return strdup(failed ? "{\"error\":\"invalid parameters\"}" : "{\"ok\":true}")
}
@main struct StateCheck {
    @MainActor static func main() throws {
        let key = "offlineMirrorFieldStateV1", defaults = UserDefaults.standard
        let previous = defaults.object(forKey:key)
        defer { if let previous { defaults.set(previous,forKey:key) } else { defaults.removeObject(forKey:key) } }
        _ = NativeCore.request(method:"POST",path:"layout/rearrange",payload:["scheme":"campo"])
        guard let saved = defaults.data(forKey:key) else { fatalError("State must be stored as JSON bytes") }
        let state = try JSONSerialization.jsonObject(with:saved) as! [String:Any]
        let plant = (state["plants"] as! [[String:Any]])[0]
        assert((plant["metadata"] as! [String:Any])["source"] is NSNull)
        assert((plant["mirrors"] as! [[String:Any]]).count == 27135)
        failed = true
        _ = NativeCore.request(method:"POST",path:"layout/rearrange",payload:[:])
        assert(defaults.data(forKey:key) == saved, "Failed operations must preserve saved state")
        print("JSON null and 27135 mirror records saved; failed request preserves state")
    }
}
''')
    executable = tmp_path / 'state-check'
    subprocess.run(['swiftc', '-parse-as-library', str(root/'ios/HeliostatViewer/NativeCore.swift'),
                    str(harness), '-o', str(executable)], check=True, capture_output=True, text=True)
    result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
    assert '27135 mirror records saved' in result.stdout
