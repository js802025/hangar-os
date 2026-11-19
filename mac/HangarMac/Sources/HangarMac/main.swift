import AppKit

/// Hangar for Mac: Hangar's own app (the same window as on Hangar OS: chats,
/// spaces, review), plus what only a Mac needs, from a ✦ in the menu bar:
/// starting Hangar on Apple's container, spaces full screen on their own macOS
/// Space, sign-ins from Apple Passwords, and Mac computers.
@MainActor
final class App: NSObject, NSApplicationDelegate, NSMenuDelegate {
    var item: NSStatusItem!
    var daemon: Daemon?
    var spaces: [Daemon.Space] = []
    var problem: String?
    var down = false
    var starting = false
    var approvals: Approvals?

    func applicationDidFinishLaunching(_ note: Notification) {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "✦"
        let menu = NSMenu()
        menu.delegate = self
        item.menu = menu
        Task {
            await refresh()
            if down { startDaemon() } else { openHangar() }   // start Hangar with the app
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
        if starting { starting = false; openHangar() }    // it just came up: show Hangar
        if approvals == nil {
            let a = Approvals(daemon: daemon)
            a.onChange = { [weak self] in self?.rebuild() }
            a.start()
            approvals = a
        }
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
        if !down {
            let o = NSMenuItem(title: "Open Hangar", action: #selector(openHangarPicked), keyEquivalent: "o")
            o.target = self
            menu.addItem(o)
            menu.addItem(.separator())
        }
        if let problem {
            add(menu, problem, nil)
            if down && !starting { add(menu, "Start Hangar", #selector(startPicked)) }
        } else if spaces.isEmpty {
            add(menu, "No spaces yet. Ask the pilot, or `hangar new`.", nil)
        } else {
            add(menu, "Spaces", nil)
            for s in spaces {
                let kind = s.isMac ? "  (Mac)" : ""
                let m = NSMenuItem(title: "✦ \(s.name)\(kind)", action: #selector(pick(_:)), keyEquivalent: "")
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
        if let n = approvals?.pending.count, n > 0 {
            menu.addItem(.separator())
            add(menu, n == 1 ? "1 Sign-in Request…" : "\(n) Sign-in Requests…", #selector(showApprovals))
            item.button?.title = "✦ \(n)"
        } else {
            item.button?.title = "✦"
        }
        menu.addItem(.separator())
        add(menu, "Set Up a Mac Computer…", #selector(setUpMac))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit Hangar", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
    }

    @objc func startPicked() { startDaemon() }

    @objc func openHangarPicked() { openHangar() }

    /// Hangar's own window (the Qt app, the same as on Hangar OS).
    func openHangar() { Runtime.run(["open", "home"]) }

    /// hangar-mac://approve/ID: Hangar's window hands sign-ins to the Passwords prompt.
    func application(_ application: NSApplication, open urls: [URL]) {
        for u in urls where u.scheme == "hangar-mac" {
            if u.host == "approve", let id = u.pathComponents.last, id != "/" { approvals?.show(id: id) }
            else if u.host == "open" { openHangar() }
        }
    }

    @objc func showApprovals() { approvals?.showFirst() }

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

    /// The space in Hangar's own space window, full screen on its own macOS Space.
    func open(_ name: String) {
        Runtime.run(["open", "space", name])
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)     // menu bar only, no Dock icon
let delegate = App()
app.delegate = delegate
app.run()
