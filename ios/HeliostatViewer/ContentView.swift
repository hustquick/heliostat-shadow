import SwiftUI
import WebKit

struct ContentView: View {
    var body: some View {
        OfflineViewerWebView()
            .ignoresSafeArea()
            .preferredColorScheme(.dark)
    }
}

private struct OfflineViewerWebView: UIViewRepresentable {
    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.setURLSchemeHandler(LocalAssetSchemeHandler(), forURLScheme: "heliostat")
        configuration.userContentController.addScriptMessageHandler(
            context.coordinator, contentWorld: .page, name: "heliostatNative")
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.isOpaque = false
        view.backgroundColor = UIColor(red: 8/255, green: 19/255, blue: 26/255, alpha: 1)
        view.scrollView.minimumZoomScale = 1
        view.scrollView.maximumZoomScale = 1
        view.allowsBackForwardNavigationGestures = true
        guard let initialized = NativeCore.initializeOfflineBundle(), !initialized.contains("\"error\"") else {
            view.loadHTMLString("<body style='background:#08131a;color:white;font:18px sans-serif;padding:32px'>离线数据或界面资源缺失。</body>", baseURL: nil)
            return view
        }
        view.load(URLRequest(url: URL(string: "heliostat://viewer/index.html")!))
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKScriptMessageHandlerWithReply {
        @MainActor
        func userContentController(_ userContentController: WKUserContentController,
                                   didReceive message: WKScriptMessage) async -> (Any?, String?) {
            if let body = message.body as? [String: Any], body["path"] as? String == "file/export",
               let payload = body["payload"] as? [String: Any], let text = payload["text"] as? String {
                let url = FileManager.default.temporaryDirectory.appendingPathComponent("heliostat-coordinates.csv")
                do { try text.write(to: url, atomically: true, encoding: .utf8) }
                catch { return (nil, error.localizedDescription) }
                let picker = UIDocumentPickerViewController(forExporting: [url], asCopy: true)
                let scene = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first
                var controller = scene?.windows.first(where: { $0.isKeyWindow })?.rootViewController
                while let presented = controller?.presentedViewController { controller = presented }
                controller?.present(picker, animated: true)
                return (["ok": true], nil)
            }
            guard let body = message.body as? [String: Any],
                  let method = body["method"] as? String,
                  let path = body["path"] as? String,
                  let result = NativeCore.request(method: method, path: path,
                                                  payload: body["payload"] ?? [:]),
                  let data = result.data(using: .utf8),
                  let object = try? JSONSerialization.jsonObject(with: data) else {
                return (nil, "本地镜场接口调用失败")
            }
            return (object, nil)
        }
    }
}

private final class LocalAssetSchemeHandler: NSObject, WKURLSchemeHandler {
    func webView(_ webView: WKWebView, start urlSchemeTask: WKURLSchemeTask) {
        guard let url = urlSchemeTask.request.url,
              url.host == "viewer" else {
            urlSchemeTask.didFailWithError(URLError(.badURL)); return
        }
        let relative = url.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard !relative.contains(".."),
              let root = Bundle.main.resourceURL?.appendingPathComponent("viewer", isDirectory: true),
              let data = try? Data(contentsOf: root.appendingPathComponent(relative)) else {
            urlSchemeTask.didFailWithError(URLError(.fileDoesNotExist)); return
        }
        let ext = (relative as NSString).pathExtension.lowercased()
        let mime = ["html":"text/html", "css":"text/css", "js":"text/javascript", "json":"application/json", "svg":"image/svg+xml"][ext] ?? "application/octet-stream"
        guard let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": mime, "Access-Control-Allow-Origin":"*"]) else {
            urlSchemeTask.didFailWithError(URLError(.badServerResponse)); return
        }
        urlSchemeTask.didReceive(response); urlSchemeTask.didReceive(data); urlSchemeTask.didFinish()
    }

    func webView(_ webView: WKWebView, stop urlSchemeTask: WKURLSchemeTask) {}
}
