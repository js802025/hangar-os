import AppKit

/// Hangar for Mac, spike: a ✦ in the menu bar listing the daemon's spaces.
/// Pick one and it opens full screen, on a macOS Space of its own. Agents run
/// on Apple's container; a space can also have a Mac computer (a macOS VM).
@MainActor
final class App: NSObject, NSApplicationDelegate, NSMenuDelegate {
    var item: NSStatusItem!
    var daemon: Daemon?
    var windows: [String: SpaceWindow] = [:]
    var spaces: [Daemon.Space] = []
    var problem: String?
    var down = false
    var starting = false

    func applicationDidFinishLaunching(_ note: Notification) {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "✦"
        let menu = NSMenu()
        menu.delegate = self
        item.menu = menu
        Task {
            await refresh()
            if down { startDaemon() }          // start Hangar with the app
            // `HangarMac --open NAME` pops a space straight out (handy for testing).
            let args = CommandLine.arguments
            if let i = args.firstIndex(of: "--open"), i + 1 < args.count { open(args[i + 1]) }
        }
    }

    func refresh() async {
        if daemon == nil { daemon = try? Daemon.local() }
        guard let daemon else {
            down = true
            problem = starting ? "Starting Hangar…" : "Hangar isn't running"
            rebuild()
            return
        }
        guard await daemon.alive() else {
            down = true
            problem = starting ? "Starting Hangar…" : "Hangar isn't running"
            rebuild()
            return
        }
        down = false
        starting = false
        do {
            spaces = try await daemon.spaces()
            problem = nil
        } catch {
            problem = "Hangar answered oddly (\(error))"
        }
        rebuild()
    }

    func startDaemon() {
        do {
            try Runtime.startDaemon()
            starting = true
            Task {
                for _ in 0..<30 {
                    try? await Task.sleep(for: .seconds(1))
                    await refresh()
                    if !down { return }
                }
                starting = false
                problem = "Hangar didn't start (see ~/.local/state/hangar/daemon-mac.log)"
                rebuild()
            }
        } catch {
            problem = "\(error.localizedDescription)"
        }
        rebuild()
    }

    func menuWillOpen(_ menu: NSMenu) {
        Task { await refresh() }
    }

    private func add(_ menu: NSMenu, _ title: String, _ action: Selector?, _ obj: Any? = nil) {
        let m = NSMenuItem(title: title, action: action, keyEquivalent: "")
        m.target = self
        m.representedObject = obj
        if action == nil { m.isEnabled = false }
        menu.addItem(m)
    }

    func rebuild() {
        guard let menu = item.menu else { return }
        menu.removeAllItems()
        if let problem {
            add(menu, problem, nil)
            if down && !starting { add(menu, "Start Hangar", #selector(startPicked)) }
        } else if spaces.isEmpty {
            add(menu, "No spaces yet. Ask the pilot, or `hangar new`.", nil)
        } else {
            add(menu, "Spaces", nil)
            for s in spaces {
                let mark = windows[s.name] != nil ? "  ●" : ""
                let kind = s.isMac ? "  (Mac)" : ""
                let m = NSMenuItem(title: "✦ \(s.name)\(kind)\(mark)", action: #selector(pick(_:)), keyEquivalent: "")
                m.representedObject = s.name
                m.target = self
                m.toolTip = [s.status, s.model, s.task].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
                m.isEnabled = s.status == "running"
                menu.addItem(m)
                if s.isMac, let vm = s.vm, s.status == "running" {
                    let t = NSMenuItem(title: "    Take over the Mac", action: #selector(takeOver(_:)), keyEquivalent: "")
                    t.representedObject = vm
                    t.target = self
                    menu.addItem(t)
                }
            }
        }
        menu.addItem(.separator())
        add(menu, "Set Up a Mac Computer…", #selector(setUpMac))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit Hangar", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
    }

    @objc func startPicked() { startDaemon() }

    @objc func pick(_ sender: NSMenuItem) {
        if let name = sender.representedObject as? String { open(name) }
    }

    @objc func takeOver(_ sender: NSMenuItem) {
        if let vm = sender.representedObject as? String { Runtime.showVM(vm) }
    }

    /// A Mac computer is set up once, by you, in its own window (like a new Mac).
    @objc func setUpMac() {
        Runtime.inTerminal(["mac", "setup"])
    }

    func open(_ name: String) {
        guard let daemon else { return }
        if let w = windows[name] { w.focus(); return }
        let w = SpaceWindow(space: name, daemon: daemon, fullScreen: true)
        w.onClose = { [weak self] in self?.windows[name] = nil; self?.rebuild() }
        windows[name] = w
        NSApp.activate(ignoringOtherApps: true)
        rebuild()
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)     // menu bar only, no Dock icon
let delegate = App()
app.delegate = delegate
app.run()
