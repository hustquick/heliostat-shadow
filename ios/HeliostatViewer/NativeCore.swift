import Foundation

@MainActor enum NativeCore {
    private static let stateKey = "offlineMirrorFieldStateV1"
    private static var initialized = false
    static func compute(_ payload: String) -> String? {
        payload.withCString { input in
            guard let output = heliostat_compute_field_json(input) else { return nil }
            defer { heliostat_free_string(output) }
            return String(cString: output)
        }
    }

    static func initializeOfflineBundle() -> String? {
        guard let url = Bundle.main.url(forResource: "plant_bundle", withExtension: "json.gz", subdirectory: "mobile"),
              let data = try? Data(contentsOf: url) else { return nil }
        let result: String? = data.withUnsafeBytes { (buffer: UnsafeRawBufferPointer) -> String? in
            guard let base = buffer.bindMemory(to: UInt8.self).baseAddress,
                  let output = heliostat_mobile_initialize_gzip(base, UInt(buffer.count)) else { return nil }
            defer { heliostat_free_string(output) }
            return String(cString: output)
        }
        guard let result, let resultData = result.data(using: .utf8),
              let summary = try? JSONSerialization.jsonObject(with: resultData) as? [String: Any],
              summary["error"] == nil else { return result }
        initialized = true
        if let saved = UserDefaults.standard.object(forKey: stateKey),
           let data = try? JSONSerialization.data(withJSONObject: saved),
           let object = try? JSONSerialization.jsonObject(with: data) {
            _ = request(method: "POST", path: "state/import", payload: object)
        }
        return result
    }

    static func request(method: String, path: String, payload: Any = [:]) -> String? {
        if !initialized { _ = initializeOfflineBundle() }
        guard JSONSerialization.isValidJSONObject(payload),
              let data = try? JSONSerialization.data(withJSONObject: [
                "method": method, "path": path, "payload": payload
              ]), let input = String(data: data, encoding: .utf8) else { return nil }
        let result: String? = input.withCString { pointer in
            guard let output = heliostat_mobile_request_json(pointer) else { return nil }
            defer { heliostat_free_string(output) }
            return String(cString: output)
        }
        if method == "POST", ["plants/select", "plants/import", "layout/rearrange"].contains(path),
           let state = rawRequest(method: "GET", path: "state/export", payload: [:]),
           let stateData = state.data(using: .utf8),
           let object = try? JSONSerialization.jsonObject(with: stateData) {
            UserDefaults.standard.set(object, forKey: stateKey)
        }
        return result
    }

    private static func rawRequest(method: String, path: String, payload: Any) -> String? {
        guard let data = try? JSONSerialization.data(withJSONObject: ["method": method, "path": path, "payload": payload]),
              let input = String(data: data, encoding: .utf8) else { return nil }
        return input.withCString { pointer in
            guard let output = heliostat_mobile_request_json(pointer) else { return nil }
            defer { heliostat_free_string(output) }
            return String(cString: output)
        }
    }

    static func selfTest() -> Bool {
        let payload = """
        {"mirrors":[{"mirror_id":"self-test","centre":[0,0,0],"width":2,"height":2,
        "aim_point":[0,0,20],"roll_deg":0,"mount_type":"projected_east","tower_id":"tower-1"}],
        "sun":[0,0,1],"target_indices":[0],"conservative_filter":true}
        """
        guard let result = compute(payload), let data = result.data(using: .utf8),
              let rows = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]],
              let joint = rows.first?["eta_joint"] as? Double else { return false }
        return abs(joint - 1) < 1e-12
    }
}
