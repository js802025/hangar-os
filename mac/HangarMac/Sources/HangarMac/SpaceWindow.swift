import AppKit

/// One agent space in a window of its own. Going full screen gives it its own
/// macOS Space (shown as "✦ <space>" in Mission Control), the Mac counterpart
/// of hangar/kde.py putting a space on its own KWin virtual desktop.
@MainActor
final class SpaceWindow: NSObject, NSWindowDelegate {
    let space: String
    let daemon: Daemon
    let window: NSWindow
    private let image = NSImageView()
    private let status = NSTextField(labelWithString: "Connecting…")
    private var task: Task<Void, Never>?
    var onClose: (() -> Void)?

    init(space: String, daemon: Daemon, fullScreen: Bool) {
        self.space = space
        self.daemon = daemon
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 800),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        super.init()
        window.title = "✦ \(space)"
        window.collectionBehavior = [.fullScreenPrimary, .managed]
        window.isReleasedWhenClosed = false
        window.backgroundColor = NSColor(red: 0.06, green: 0.07, blue: 0.09, alpha: 1)
        window.delegate = self

        image.imageScaling = .scaleProportionallyUpOrDown
        image.translatesAutoresizingMaskIntoConstraints = false
        status.textColor = .secondaryLabelColor
        status.translatesAutoresizingMaskIntoConstraints = false
        let root = NSView()
        root.addSubview(image)
        root.addSubview(status)
        NSLayoutConstraint.activate([
            image.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            image.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            image.topAnchor.constraint(equalTo: root.topAnchor),
            image.bottomAnchor.constraint(equalTo: root.bottomAnchor),
            status.centerXAnchor.constraint(equalTo: root.centerXAnchor),
            status.centerYAnchor.constraint(equalTo: root.centerYAnchor),
        ])
        window.contentView = root
        window.center()
        window.makeKeyAndOrderFront(nil)
        if fullScreen { window.toggleFullScreen(nil) }
        start()
    }

    func focus() {
        window.makeKeyAndOrderFront(nil)    // macOS switches to the window's Space
    }

    /// The spike polls screenshots (~2 fps). A real build would speak VNC, like
    /// hangar/apps/vnc.py, for smooth video and taking over.
    private func start() {
        task = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                let width = Int((self.window.contentView?.bounds.width ?? 1280) * (self.window.backingScaleFactor))
                do {
                    let png = try await self.daemon.screenshot(self.space, width: min(width, 2560))
                    if let img = NSImage(data: png) {
                        self.image.image = img
                        self.status.isHidden = true
                    }
                } catch {
                    self.status.stringValue = "\(self.space): \(error)"
                    self.status.isHidden = false
                }
                try? await Task.sleep(for: .milliseconds(500))
            }
        }
    }

    func windowWillClose(_ notification: Notification) {
        task?.cancel()
        onClose?()
    }
}
