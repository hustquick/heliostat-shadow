import AppKit
import Foundation
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKScriptMessageHandler {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var server: Process?
    private var portFile: URL?
    private var shuttingDown = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        createMenu()
        createWindow()
        startServer()
    }

    private func createMenu() {
        let menu = NSMenu()
        let applicationItem = NSMenuItem()
        menu.addItem(applicationItem)
        let applicationMenu = NSMenu(title: "塔式镜场设计与优化")
        applicationItem.submenu = applicationMenu
        let quit = NSMenuItem(title: "退出塔式镜场设计与优化", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        quit.keyEquivalentModifierMask = [.command]
        quit.target = NSApp
        applicationMenu.addItem(quit)
        let editItem = NSMenuItem(title: "编辑", action: nil, keyEquivalent: "")
        let editMenu = NSMenu(title: "编辑")
        for (title, action, key) in [("剪切", "cut:", "x"), ("复制", "copy:", "c"), ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")] {
            editMenu.addItem(NSMenuItem(title: title, action: Selector(action), keyEquivalent: key))
        }
        editItem.submenu = editMenu
        menu.addItem(editItem)
        for (title, entries) in desktopMenus {
            let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            let submenu = NSMenu(title: title)
            for (label, command, key) in entries {
                let action = NSMenuItem(title: label, action: #selector(desktopAction(_:)), keyEquivalent: key)
                action.target = self
                action.representedObject = command
                submenu.addItem(action)
            }
            item.submenu = submenu
            menu.addItem(item)
        }
        NSApp.mainMenu = menu
    }

    private let desktopMenus: [(String, [(String, String, String)])] = [
        ("文件", [("导入镜面坐标…", "import", "o"), ("导出全场坐标…", "export", "s")]),
        ("镜场", [("聚光启停参数…", "operation", ""), ("镜场重排…", "rearrange", ""), ("调整镜面朝向…", "orientation", ""), ("恢复太阳跟踪", "reset", "")]),
        ("视图", [("全场视角", "overview", "1"), ("聚焦目标", "focus", "2"), ("等轴测视图", "iso", "3"), ("俯视 E–N", "top", "4"), ("切换效率着色", "efficiency", ""), ("切换目标塔着色", "tower", ""), ("切换仅目标与候选镜", "neighbors", "")]),
        ("图案", [("添加文字…", "text", ""), ("生成当前文字图案", "textApply", ""), ("添加图案图片…", "image", ""), ("清除图案并恢复跟踪", "reset", "")]),
        ("时间", [("播放 / 暂停", "play", ""), ("上一小时", "prev", ""), ("下一小时", "next", "")]),
        ("帮助", [("操作说明", "help", "")])
    ]

    @objc private func desktopAction(_ sender: NSMenuItem) {
        guard let command = sender.representedObject as? String, let webView = webView else { return }
        webView.evaluateJavaScript("window.heliostatDesktopCommand?.('\(command)')")
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: String], let text = body["text"] else { return }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "heliostat-coordinates.csv"
        panel.beginSheetModal(for: window) { result in
            guard result == .OK, let url = panel.url else { return }
            do { try text.write(to: url, atomically: true, encoding: .utf8) }
            catch { let alert = NSAlert(); alert.messageText = "导出失败"; alert.informativeText = error.localizedDescription; alert.runModal() }
        }
    }

    private func createWindow() {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.userContentController.add(self, name: "heliostatExport")
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.setValue(false, forKey: "drawsBackground")
        webView.loadHTMLString("""
        <!doctype html><meta charset="utf-8">
        <style>
        body { margin:0; height:100vh; display:grid; place-items:center;
               background:#08131a; color:#d9edf4; font:16px -apple-system; }
        .box { text-align:center } .spinner { width:28px; height:28px; margin:0 auto 18px;
               border:3px solid #264957; border-top-color:#4fc3dc; border-radius:50%;
               animation:spin 0.8s linear infinite } @keyframes spin { to { transform:rotate(360deg) } }
        </style><div class="box"><div class="spinner"></div>正在启动塔式镜场设计与优化…</div>
        """, baseURL: nil)

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1360, height: 860),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "塔式镜场设计与优化"
        window.minSize = NSSize(width: 980, height: 650)
        window.contentView = webView
        window.delegate = self
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func startServer() {
        guard let resources = Bundle.main.resourceURL else {
            showFailure("应用资源目录不存在。")
            return
        }
        let executable = resources.appendingPathComponent("server/heliostat-viewer-server")
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            showFailure("内置计算服务不存在或不可执行。")
            return
        }

        let token = UUID().uuidString
        let port = FileManager.default.temporaryDirectory
            .appendingPathComponent("heliostat-viewer-\(token).port")
        portFile = port

        let logs = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs", isDirectory: true)
        try? FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        let logURL = logs.appendingPathComponent("heliostat-shadow-viewer.log")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        let logHandle = try? FileHandle(forWritingTo: logURL)

        let process = Process()
        process.executableURL = executable
        process.arguments = ["--port", "0", "--port-file", port.path]
        process.currentDirectoryURL = resources.appendingPathComponent("server", isDirectory: true)
        process.standardOutput = logHandle
        process.standardError = logHandle
        process.terminationHandler = { [weak self] task in
            logHandle?.closeFile()
            guard let self, !self.shuttingDown else { return }
            DispatchQueue.main.async {
                self.showFailure("计算服务意外停止（状态 \(task.terminationStatus)）。日志位于 ~/Library/Logs/heliostat-shadow-viewer.log。")
            }
        }
        do {
            try process.run()
            server = process
            waitForPortFile(port)
        } catch {
            logHandle?.closeFile()
            showFailure("无法启动内置计算服务：\(error.localizedDescription)")
        }
    }

    private func waitForPortFile(_ file: URL) {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            for _ in 0..<300 {
                guard let self, self.server?.isRunning == true else { return }
                if let text = try? String(contentsOf: file, encoding: .utf8),
                   let port = Int(text.trimmingCharacters(in: .whitespacesAndNewlines)),
                   port > 0 {
                    DispatchQueue.main.async {
                        self.webView.load(URLRequest(url: URL(string: "http://127.0.0.1:\(port)/")!))
                    }
                    return
                }
                Thread.sleep(forTimeInterval: 0.1)
            }
            DispatchQueue.main.async { [weak self] in
                self?.showFailure("计算服务启动超时。日志位于 ~/Library/Logs/heliostat-shadow-viewer.log。")
            }
        }
    }

    private func showFailure(_ message: String) {
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = "塔式镜场设计与优化无法启动"
        alert.informativeText = message
        alert.addButton(withTitle: "退出")
        alert.runModal()
        NSApp.terminate(nil)
    }

    func windowWillClose(_ notification: Notification) {
        NSApp.terminate(nil)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        shuttingDown = true
        if let process = server, process.isRunning {
            process.terminate()
            process.waitUntilExit()
        }
        if let portFile { try? FileManager.default.removeItem(at: portFile) }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
