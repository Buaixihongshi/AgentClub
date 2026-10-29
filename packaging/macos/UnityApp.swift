// Native shell for the packaged app: starts the bundled backend on a free
// loopback port and hosts the frontend in a WKWebView window.
import Cocoa
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuItemValidation, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    private static let projectKey = "ProjectDirectory"
    private var window: NSWindow!
    private var webView: WKWebView!
    private var backend: Process?
    private var port = 0
    private var quitting = false
    private var projectItem: NSMenuItem!

    private var resources: URL { Bundle.main.resourceURL! }
    private var logURL: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/Unity/backend.log")
    }

    /// The Git repository Workflow tasks branch from; nil keeps the backend in its private workspace.
    private var projectDirectory: URL? {
        guard let path = UserDefaults.standard.string(forKey: Self.projectKey) else { return nil }
        var isDirectory: ObjCBool = false
        return FileManager.default.fileExists(atPath: path, isDirectory: &isDirectory) && isDirectory.boolValue
            ? URL(fileURLWithPath: path, isDirectory: true) : nil
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1360, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Unity"
        window.minSize = NSSize(width: 900, height: 600)
        window.contentView = webView
        // Kept alive after ⌘W so a quit confirmation can still attach to it.
        window.isReleasedWhenClosed = false
        window.setFrameAutosaveName("UnityMainWindow")
        window.center()
        window.makeKeyAndOrderFront(nil)
        showStatus("正在启动 Unity…")
        startBackend()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    // MARK: Backend lifecycle

    private func freePort() -> Int {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        defer { close(fd) }
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        addr.sin_port = 0
        var len = socklen_t(MemoryLayout<sockaddr_in>.size)
        _ = withUnsafeMutablePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.bind(fd, $0, len) }
        }
        _ = withUnsafeMutablePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.getsockname(fd, $0, &len) }
        }
        return Int(UInt16(bigEndian: addr.sin_port))
    }

    private func startBackend() {
        port = freePort()
        try? FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil, attributes: [.posixPermissions: 0o600])
        }
        let log = try? FileHandle(forWritingTo: logURL)
        log?.seekToEndOfFile()

        let process = Process()
        process.executableURL = resources.appendingPathComponent("python/bin/python3")
        process.arguments = ["-m", "backend.app.desktop", "--port", String(port)]
        process.currentDirectoryURL = resources.appendingPathComponent("app")
        var env = ProcessInfo.processInfo.environment
        // Never write bytecode into the signed bundle (the launcher sets this again for children).
        env["PYTHONPYCACHEPREFIX"] = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Caches/Unity/pycache").path
        // The launcher makes this the backend's cwd, i.e. the Workflow project repository.
        if let project = projectDirectory { env["UNITY_WORKSPACE"] = project.path }
        process.environment = env
        process.standardOutput = log
        process.standardError = log
        process.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                // A backend replaced by a project switch is expected to exit.
                guard let self, !self.quitting, proc === self.backend else { return }
                self.showStatus("后端已退出（代码 \(proc.terminationStatus)）。日志：~/Library/Logs/Unity/backend.log")
            }
        }
        do {
            try process.run()
            backend = process
            waitForHealth(deadline: Date().addingTimeInterval(90))
        } catch {
            showStatus("无法启动后端：\(error.localizedDescription)")
        }
    }

    private var origin: URL { URL(string: "http://127.0.0.1:\(port)")! }

    private func waitForHealth(deadline: Date) {
        var request = URLRequest(url: origin.appendingPathComponent("api/health"))
        request.timeoutInterval = 2
        URLSession.shared.dataTask(with: request) { [weak self] _, response, _ in
            DispatchQueue.main.async {
                guard let self, self.backend?.isRunning == true else { return }
                if (response as? HTTPURLResponse)?.statusCode == 200 {
                    self.webView.load(URLRequest(url: self.origin))
                } else if Date() > deadline {
                    self.showStatus("后端启动超时。日志：~/Library/Logs/Unity/backend.log")
                } else {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { self.waitForHealth(deadline: deadline) }
                }
            }
        }.resume()
    }

    /// Sums the activity counters in /api/health; nil when they cannot be read.
    private func fetchActiveWork(_ completion: @escaping (Int?) -> Void) {
        var request = URLRequest(url: origin.appendingPathComponent("api/health"))
        request.timeoutInterval = 3
        URLSession.shared.dataTask(with: request) { data, _, _ in
            var total: Int?
            if let data, let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let runtime = json["runtime"] as? [String: Any],
               let activity = runtime["activity"] as? [String: Any] {
                total = activity.values.compactMap { ($0 as? NSNumber)?.intValue }.reduce(0, +)
            }
            DispatchQueue.main.async { completion(total) }
        }.resume()
    }

    // Asynchronous so the confirmation works for menu, Dock and Apple Event quits alike.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard !quitting, let backend, backend.isRunning else { return .terminateNow }
        fetchActiveWork { [weak self] active in
            guard let self else { return }
            guard let active, active > 0 else { return self.stopBackendThenQuit() }
            let alert = NSAlert()
            alert.messageText = "还有 \(active) 项任务未结束"
            alert.informativeText = "包括正在运行或等待你回复的讨论。退出会中断正在进行的模型调用。"
            alert.addButton(withTitle: "取消")
            alert.addButton(withTitle: "仍然退出")
            self.window.makeKeyAndOrderFront(nil)
            alert.beginSheetModal(for: self.window) { response in
                if response == .alertFirstButtonReturn {
                    NSApp.reply(toApplicationShouldTerminate: false)
                } else {
                    self.stopBackendThenQuit()
                }
            }
        }
        return .terminateLater
    }

    /// Blocking: graceful SIGTERM first, SIGKILL after 10 seconds.
    private static func stop(_ backend: Process?) {
        guard let backend, backend.isRunning else { return }
        backend.terminate()
        let deadline = Date().addingTimeInterval(10)
        while backend.isRunning && Date() < deadline { usleep(100_000) }
        if backend.isRunning { kill(backend.processIdentifier, SIGKILL) }
    }

    private func stopBackendThenQuit() {
        quitting = true
        DispatchQueue.global().async { [backend] in
            Self.stop(backend)
            DispatchQueue.main.async { NSApp.reply(toApplicationShouldTerminate: true) }
        }
    }

    private func restartBackend() {
        let old = backend
        backend = nil
        showStatus("正在切换项目仓库…")
        DispatchQueue.global().async {
            Self.stop(old)
            DispatchQueue.main.async { self.startBackend() }
        }
    }

    // MARK: Project repository

    private static func gitTopLevel(_ url: URL) -> URL? {
        let git = Process()
        git.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        git.arguments = ["-C", url.path, "rev-parse", "--show-toplevel"]
        let output = Pipe()
        git.standardOutput = output
        git.standardError = FileHandle.nullDevice
        guard (try? git.run()) != nil else { return nil }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        git.waitUntilExit()
        guard git.terminationStatus == 0,
              let path = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
              !path.isEmpty else { return nil }
        return URL(fileURLWithPath: path, isDirectory: true)
    }

    @objc private func chooseProject() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.prompt = "选择"
        panel.message = "选择 Workflow 任务使用的 Git 仓库；每个任务会在其中建立独立的分支和工作树。"
        panel.directoryURL = projectDirectory
        panel.beginSheetModal(for: window) { [weak self] response in
            guard let self, response == .OK, let url = panel.url else { return }
            // Let the panel's sheet finish closing before showing another one.
            DispatchQueue.main.async {
                guard let repository = Self.gitTopLevel(url) else {
                    let alert = NSAlert()
                    alert.messageText = "所选文件夹不是 Git 仓库"
                    alert.informativeText = "Workflow 需要在 Git 仓库中为任务创建分支和工作树。"
                    alert.beginSheetModal(for: self.window)
                    return
                }
                self.switchProject(to: repository)
            }
        }
    }

    @objc private func resetProject() { switchProject(to: nil) }

    private func switchProject(to url: URL?) {
        guard url?.path != projectDirectory?.path else { return }
        let apply = {
            if let url {
                UserDefaults.standard.set(url.path, forKey: Self.projectKey)
            } else {
                UserDefaults.standard.removeObject(forKey: Self.projectKey)
            }
            self.refreshProjectItem()
            self.restartBackend()
        }
        fetchActiveWork { [weak self] active in
            guard let self else { return }
            guard let active, active > 0 else { return apply() }
            let alert = NSAlert()
            alert.messageText = "还有 \(active) 项任务未结束"
            alert.informativeText = "切换仓库会重启后端，中断正在进行的模型调用。"
            alert.addButton(withTitle: "取消")
            alert.addButton(withTitle: "仍然切换")
            alert.beginSheetModal(for: self.window) { if $0 == .alertSecondButtonReturn { apply() } }
        }
    }

    private func refreshProjectItem() {
        let project = projectDirectory
        projectItem.title = "项目仓库：" + (project?.lastPathComponent ?? "未选择")
        projectItem.toolTip = project?.path ?? "未选择时使用数据目录中的 workspace，Workflow 任务无法创建工作区。"
    }

    func validateMenuItem(_ menuItem: NSMenuItem) -> Bool {
        menuItem.action == #selector(resetProject) ? projectDirectory != nil : true
    }

    private func showStatus(_ text: String) {
        let escaped = text.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
        webView.loadHTMLString("""
        <html><body style="font:15px -apple-system;display:flex;height:100vh;margin:0;align-items:center;
        justify-content:center;color:#555;background:#fafafa">\(escaped)</body></html>
        """, baseURL: nil)
    }

    // MARK: Navigation, popups and downloads

    private func isApp(_ url: URL?) -> Bool { url?.host == "127.0.0.1" && url?.port == port }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if action.shouldPerformDownload { return decisionHandler(.download) }
        if let url = action.request.url, ["http", "https"].contains(url.scheme ?? ""), !isApp(url) {
            NSWorkspace.shared.open(url)
            return decisionHandler(.cancel)
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        decisionHandler(response.canShowMIMEType ? .allow : .download)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggestedFilename
        panel.directoryURL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
        panel.beginSheetModal(for: window) { completionHandler($0 == .OK ? panel.url : nil) }
    }

    // target=_blank links (logs, previews) open in the default browser.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "确定")
        alert.addButton(withTitle: "取消")
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn) }
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        let alert = NSAlert()
        alert.messageText = prompt
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 320, height: 24))
        field.stringValue = defaultText ?? ""
        alert.accessoryView = field
        alert.addButton(withTitle: "确定")
        alert.addButton(withTitle: "取消")
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn ? field.stringValue : nil) }
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.beginSheetModal(for: window) { completionHandler($0 == .OK ? panel.urls : nil) }
    }

    // MARK: Menu (WKWebView needs an Edit menu for ⌘C/⌘V/⌘A)

    @objc private func reload() { _ = isApp(webView.url) ? webView.reload() : webView.load(URLRequest(url: origin)) }
    @objc private func openLogs() { NSWorkspace.shared.activateFileViewerSelecting([logURL]) }
    @objc private func openData() {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        NSWorkspace.shared.open(support.appendingPathComponent("Unity"))
    }

    private func buildMenu() {
        let main = NSMenu()
        func submenu(_ title: String, _ items: [NSMenuItem]) {
            let holder = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            let menu = NSMenu(title: title)
            items.forEach(menu.addItem)
            holder.submenu = menu
            main.addItem(holder)
        }
        func item(_ title: String, _ action: Selector?, _ key: String = "", _ mods: NSEvent.ModifierFlags = .command) -> NSMenuItem {
            let entry = NSMenuItem(title: title, action: action, keyEquivalent: key)
            entry.keyEquivalentModifierMask = mods
            return entry
        }
        projectItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
        refreshProjectItem()
        submenu("Unity", [
            item("关于 Unity", #selector(NSApplication.orderFrontStandardAboutPanel(_:))),
            .separator(),
            projectItem,
            item("选择项目仓库…", #selector(chooseProject), "o", [.command, .shift]),
            item("使用默认工作目录", #selector(resetProject)),
            .separator(),
            item("打开数据目录", #selector(openData)),
            item("显示后端日志", #selector(openLogs)),
            .separator(),
            item("隐藏 Unity", #selector(NSApplication.hide(_:)), "h"),
            item("退出 Unity", #selector(NSApplication.terminate(_:)), "q"),
        ])
        submenu("编辑", [
            item("撤销", Selector(("undo:")), "z"),
            item("重做", Selector(("redo:")), "z", [.command, .shift]),
            .separator(),
            item("剪切", #selector(NSText.cut(_:)), "x"),
            item("复制", #selector(NSText.copy(_:)), "c"),
            item("粘贴", #selector(NSText.paste(_:)), "v"),
            item("全选", #selector(NSText.selectAll(_:)), "a"),
        ])
        submenu("显示", [item("重新载入", #selector(reload), "r")])
        submenu("窗口", [
            item("最小化", #selector(NSWindow.performMiniaturize(_:)), "m"),
            item("关闭", #selector(NSWindow.performClose(_:)), "w"),
        ])
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
