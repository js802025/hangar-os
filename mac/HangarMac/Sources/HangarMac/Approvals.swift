import AppKit
import SwiftUI
import UserNotifications

/// Sign-in requests from agents, answered from Apple Passwords.
///
/// When an agent calls request_login, a notification appears in the corner (it
/// never takes focus from what you're doing). Click it, or "Sign in…", for
/// Hangar's sign-in prompt: the agent, the site and its reason, with a username and a password field marked
/// for AutoFill, so macOS offers your saved logins (Touch ID) as in any app.
/// Sign In sends that login to the daemon once, which types it into the agent's
/// browser and doesn't keep it. The agent never sees it. Verification codes work
/// the same way, with a one-time-code field the Passwords app fills.
/// Hangar's own window sends its "Sign in" buttons here (hangar-mac://approve/ID).
@MainActor
final class Approvals: NSObject, NSWindowDelegate, UNUserNotificationCenterDelegate {
    struct Request: Decodable, Equatable {
        let id: String
        let agent: String
        let who: String?
        let kind: String
        let site: String
        let reason: String
        let status: String
        let entries: [Entry]?
        var isLogin: Bool { kind == "login" }
        var asker: String { who ?? agent }
    }
    struct Entry: Decodable, Equatable, Hashable { let id: String; let label: String; let username: String? }
    private struct Overview: Decodable { let requests: [Request] }

    let daemon: Daemon
    var pending: [Request] = []
    var onChange: (() -> Void)?
    private var answered: Set<String> = []
    private var notified: Set<String> = []
    private var window: NSPanel?
    private var task: Task<Void, Never>?
    private let model = PromptModel()

    init(daemon: Daemon) {
        self.daemon = daemon
        super.init()
        model.onSignIn = { [weak self] in self?.approve() }
        model.onDeny = { [weak self] in self?.deny() }
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        let signIn = UNNotificationAction(identifier: "signin", title: "Sign in…", options: [.foreground])
        let deny = UNNotificationAction(identifier: "deny", title: "Deny", options: [.destructive])
        center.setNotificationCategories([
            UNNotificationCategory(identifier: "signin", actions: [signIn, deny], intentIdentifiers: []),
            UNNotificationCategory(identifier: "info", actions: [], intentIdentifiers: []),
        ])
        center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    func start() {
        task = Task { [weak self] in
            while !Task.isCancelled {
                await self?.poll()
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    private func poll() async {
        guard let data = try? await daemon.request("GET", "api/vault"),
              let o = try? JSONDecoder().decode(Overview.self, from: data) else { return }
        pending = o.requests.filter { $0.status == "pending" && !answered.contains($0.id) }
        onChange?()
        // New requests: a notification, never a window that takes your focus. (Until
        // you allow Hangar's notifications they wait, then go out; the menu shows them.)
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        let allowed = [.authorized, .provisional].contains(settings.authorizationStatus)
        for r in pending where allowed && !notified.contains(r.id) {
            notified.insert(r.id)
            notify(r)
        }
        // Answered, denied or expired elsewhere: take its notification (and prompt) away.
        let open = Set(pending.map(\.id))
        let gone = notified.subtracting(open)
        if !gone.isEmpty {
            UNUserNotificationCenter.current().removeDeliveredNotifications(withIdentifiers: Array(gone))
        }
        if let current = model.request, !open.contains(current.id) { hide() }
    }

    private func notify(_ r: Request) {
        let c = UNMutableNotificationContent()
        c.title = r.isLogin ? "\(r.asker) wants to sign in to \(r.site)" : "\(r.asker) needs a code for \(r.site)"
        c.body = r.reason.isEmpty ? "Click to answer from Apple Passwords." : r.reason
        c.subtitle = r.asker == r.agent ? "" : "Space: \(r.agent)"
        c.categoryIdentifier = "signin"
        c.sound = .default
        c.userInfo = ["request": r.id]
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: r.id, content: c, trigger: nil))
    }

    // Banners even though Hangar is "active" as a menu bar app.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent n: UNNotification,
                                            withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void) {
        done([.banner, .sound, .list])
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                            withCompletionHandler done: @escaping () -> Void) {
        let id = response.notification.request.content.userInfo["request"] as? String ?? ""
        let action = response.actionIdentifier
        done()
        Task { @MainActor in
            if !id.isEmpty {
                if action == "deny" { self.deny(id: id) } else if action != UNNotificationDismissActionIdentifier { self.show(id: id) }
            }
        }
    }

    func showFirst() {
        if let r = pending.first { show(r) }
    }

    /// From a notification, the menu, or Hangar's window ("Sign in" on an approval card).
    func show(id: String) {
        if let r = pending.first(where: { $0.id == id }) { show(r) } else {
            Task { await poll(); if let r = pending.first(where: { $0.id == id }) { show(r) } }
        }
    }

    private func deny(id: String) {
        if let r = pending.first(where: { $0.id == id }) { send(r, path: "deny", body: [:]) }
    }

    // MARK: window

    private func show(_ r: Request) {
        model.load(r)
        let w = window ?? makeWindow()
        w.contentViewController?.view.layoutSubtreeIfNeeded()
        if let size = w.contentViewController?.preferredContentSize, size.height > 20 { w.setContentSize(size) }
        w.center()
        w.orderFrontRegardless()
        w.makeKey()
        NSApp.activate()
        DispatchQueue.main.async {
            if let size = w.contentViewController?.preferredContentSize, size.height > 20 { w.setContentSize(size) }
            w.center()
            self.model.focus = r.isLogin ? .user : .code
        }
    }

    private func makeWindow() -> NSPanel {
        let w = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 460, height: 400),
                        styleMask: [.titled, .closable, .fullSizeContentView], backing: .buffered, defer: false)
        w.titleVisibility = .hidden
        w.titlebarAppearsTransparent = true
        w.isMovableByWindowBackground = true
        w.isReleasedWhenClosed = false
        w.backgroundColor = NSColor(Hangar.panel)
        w.appearance = NSAppearance(named: .darkAqua)
        w.level = .floating
        w.hidesOnDeactivate = false             // Hangar is a menu bar app: it's rarely the active one
        w.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]   // on whatever Space you're on
        w.standardWindowButton(.miniaturizeButton)?.isHidden = true
        w.standardWindowButton(.zoomButton)?.isHidden = true
        w.delegate = self
        let host = NSHostingController(rootView: PromptView(model: model))
        host.sizingOptions = [.preferredContentSize]     // the window follows the prompt's size
        w.contentViewController = host
        window = w
        return w
    }

    private func hide() {
        model.clear()
        window?.orderOut(nil)
    }

    func windowWillClose(_ notification: Notification) {
        model.clear()      // not an answer: the request stays open (menu, Hangar, notification list)
    }

    // MARK: answers

    private func approve() {
        guard let r = model.request else { return }
        let body: [String: Any]
        if r.isLogin {
            if let e = model.vaultEntry {
                body = ["entry": e.id, "scope": "once"]
            } else {
                guard !model.password.isEmpty else { model.shake += 1; return }
                body = ["login": ["username": model.username, "password": model.password]]
            }
        } else {
            let c = model.code.trimmingCharacters(in: .whitespaces)
            guard !c.isEmpty else { model.shake += 1; return }
            body = ["code": c]
        }
        send(r, path: "approve", body: body)
    }

    private func deny() {
        guard let r = model.request else { return }
        send(r, path: "deny", body: [:])
    }

    /// Something to tell you: a notification, never a window.
    private func notice(_ title: String, _ text: String) {
        let c = UNMutableNotificationContent()
        c.title = title
        c.body = text
        c.categoryIdentifier = "info"
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: UUID().uuidString, content: c,
                                                                     trigger: nil))
    }

    private func send(_ r: Request, path: String, body: [String: Any]) {
        answered.insert(r.id)
        UNUserNotificationCenter.current().removeDeliveredNotifications(withIdentifiers: [r.id])
        let data = try? JSONSerialization.data(withJSONObject: body)
        hide()                                   // clears the fields right away
        Task {
            let res = try? await daemon.request("POST", "api/vault/requests/\(r.id)/\(path)", body: data)
            if path == "approve", let res,
               let d = try? JSONSerialization.jsonObject(with: res) as? [String: Any],
               d["status"] as? String == "failed" {
                notice("Couldn't sign \(r.asker) in to \(r.site)", d["message"] as? String ?? "")
            }
            await poll()
        }
    }
}

// MARK: - Hangar's design system (hangar/apps/qml/Theme.qml)

enum Hangar {
    static let bg = Color(hex: 0x0a0c11)
    static let bg2 = Color(hex: 0x0f1218)
    static let panel = Color(hex: 0x141821)
    static let raised = Color(hex: 0x1a1f2a)
    static let hover = Color(hex: 0x212735)
    static let line = Color(hex: 0x232a38)
    static let text = Color(hex: 0xeceef4)
    static let dim = Color(hex: 0x8b93a7)
    static let faint = Color(hex: 0x5c6478)
    static let accent = Color(hex: 0x7c9cff)
    static let accentHover = Color(hex: 0x93acff)
    static let yellow = Color(hex: 0xfbbf24)
}

extension Color {
    init(hex: UInt32, alpha: Double = 1) {
        self.init(.sRGB, red: Double((hex >> 16) & 0xff) / 255, green: Double((hex >> 8) & 0xff) / 255,
                  blue: Double(hex & 0xff) / 255, opacity: alpha)
    }
}

/// Hangar's pill button (HButton.qml): primary | ghost | soft.
struct HButtonStyle: ButtonStyle {
    var kind = "soft"
    func makeBody(configuration: Configuration) -> some View {
        HoverPill(kind: kind, pressed: configuration.isPressed) { configuration.label }
    }
}

private struct HoverPill<Label: View>: View {
    let kind: String
    let pressed: Bool
    @ViewBuilder let label: () -> Label
    @State private var hover = false
    var body: some View {
        label()
            .font(.system(size: 13, weight: kind == "primary" ? .semibold : .medium))
            .foregroundStyle(kind == "primary" ? Hangar.bg : Hangar.text)
            .padding(.horizontal, 16)
            .frame(height: 34)
            .background(Capsule().fill(
                kind == "primary" ? (hover ? Hangar.accentHover : Hangar.accent)
                : kind == "ghost" ? (hover ? Hangar.hover : .clear) : (hover ? Hangar.hover : Hangar.raised)))
            .overlay(Capsule().stroke(kind == "soft" ? Hangar.line : .clear))
            .scaleEffect(pressed ? 0.97 : 1)
            .animation(.easeOut(duration: 0.14), value: hover)
            .onHover { hover = $0 }
            .contentShape(Capsule())
    }
}

// MARK: - the prompt

@MainActor
final class PromptModel: ObservableObject {
    enum Field { case user, pass, code }
    @Published var request: Approvals.Request?
    @Published var username = ""
    @Published var password = ""
    @Published var code = ""
    @Published var vaultEntry: Approvals.Entry?
    @Published var focus: Field?
    @Published var shake = 0
    var onSignIn: () -> Void = {}
    var onDeny: () -> Void = {}

    func load(_ r: Approvals.Request) {
        clear()
        request = r
    }

    func clear() {
        request = nil
        username = ""
        password = ""
        code = ""
        vaultEntry = nil
        focus = nil
    }
}

struct PromptView: View {
    @ObservedObject var model: PromptModel

    var body: some View {
        Group {
            if let r = model.request {
                VStack(alignment: .leading, spacing: 20) {
                    header(r)
                    if !r.reason.isEmpty { reason(r) }
                    fields(r)
                    footer(r)
                }
                .padding(.horizontal, 26)
                .padding(.top, 34)
                .padding(.bottom, 22)
            } else {
                Color.clear.frame(height: 10)
            }
        }
        .frame(width: 440)
        .background(Hangar.panel)
        .environment(\.colorScheme, .dark)
    }

    private func header(_ r: Approvals.Request) -> some View {
        HStack(alignment: .center, spacing: 14) {
            ZStack {
                Circle().fill(Hangar.accent.opacity(0.14)).frame(width: 46, height: 46)
                Text(r.isLogin ? "🔑" : "🔢").font(.system(size: 21))
            }
            VStack(alignment: .leading, spacing: 4) {
                Text(r.isLogin ? "Sign in to \(r.site)" : "Code for \(r.site)")
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(Hangar.text)
                    .lineLimit(2)
                HStack(spacing: 6) {
                    Text("✦").foregroundStyle(Hangar.accent)
                    Text(r.asker == r.agent ? "\(r.asker) is asking" : "\(r.asker) in \(r.agent) is asking")
                        .foregroundStyle(Hangar.dim)
                }
                .font(.system(size: 12.5))
            }
        }
    }

    private func reason(_ r: Approvals.Request) -> some View {
        Text(r.reason)
            .font(.system(size: 13))
            .foregroundStyle(Hangar.text.opacity(0.9))
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 14)
            .padding(.vertical, 11)
            .background(RoundedRectangle(cornerRadius: 12).fill(Hangar.yellow.opacity(0.08)))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Hangar.yellow.opacity(0.35)))
    }

    @ViewBuilder
    private func fields(_ r: Approvals.Request) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            if r.isLogin {
                if let entries = r.entries, !entries.isEmpty {
                    Picker("", selection: $model.vaultEntry) {
                        Text("A login from Passwords").tag(Approvals.Entry?.none)
                        ForEach(entries, id: \.self) { e in
                            Text("Hangar vault: \(e.label)").tag(Approvals.Entry?.some(e))
                        }
                    }
                    .labelsHidden()
                    .pickerStyle(.menu)
                }
                if model.vaultEntry == nil {
                    HField(text: $model.username, placeholder: "Username or email", kind: .user,
                           focus: $model.focus, onSubmit: { model.focus = .pass })
                    HField(text: $model.password, placeholder: "Password", kind: .pass,
                           focus: $model.focus, onSubmit: model.onSignIn)
                    Button {
                        model.focus = nil
                        DispatchQueue.main.async { model.focus = .user }   // focusing the field offers AutoFill
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "key.horizontal.fill")
                            Text("Choose a saved login from Passwords")
                        }
                        .font(.system(size: 12.5, weight: .medium))
                        .foregroundStyle(Hangar.accent)
                    }
                    .buttonStyle(.plain)
                    .padding(.top, 2)
                }
            } else {
                HField(text: $model.code, placeholder: "Verification code", kind: .code,
                       focus: $model.focus, onSubmit: model.onSignIn)
            }
        }
        .modifier(Shake(amount: CGFloat(model.shake)))
        .animation(.default, value: model.shake)
    }

    private func footer(_ r: Approvals.Request) -> some View {
        HStack(alignment: .center, spacing: 10) {
            Text("Typed into \(r.asker)'s browser once. Not saved, never shown to the agent.")
                .font(.system(size: 11.5))
                .foregroundStyle(Hangar.faint)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 8)
            Button("Deny", action: model.onDeny)
                .buttonStyle(HButtonStyle(kind: "ghost"))
                .keyboardShortcut(.cancelAction)
            Button(r.isLogin ? "Sign in" : "Send code", action: model.onSignIn)
                .buttonStyle(HButtonStyle(kind: "primary"))
                .keyboardShortcut(.defaultAction)
        }
        .padding(.top, 2)
    }
}

private struct Shake: GeometryEffect {
    var amount: CGFloat
    var animatableData: CGFloat {
        get { amount }
        set { amount = newValue }
    }
    func effectValue(size: CGSize) -> ProjectionTransform {
        ProjectionTransform(CGAffineTransform(translationX: 6 * sin(amount * .pi * 4), y: 0))
    }
}

/// A Hangar-styled field around an AppKit text field (so Passwords AutoFill
/// works, which SwiftUI's own fields don't reliably offer).
struct HField: View {
    @Binding var text: String
    let placeholder: String
    let kind: PromptModel.Field
    @Binding var focus: PromptModel.Field?
    var onSubmit: () -> Void

    var body: some View {
        AutoFillField(text: $text, placeholder: placeholder, kind: kind, focus: $focus, onSubmit: onSubmit)
            .frame(height: 22)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(RoundedRectangle(cornerRadius: 12).fill(Hangar.bg2))
            .overlay(RoundedRectangle(cornerRadius: 12)
                .stroke(focus == kind ? Hangar.accent.opacity(0.8) : Hangar.line, lineWidth: focus == kind ? 1.5 : 1))
            .animation(.easeOut(duration: 0.14), value: focus)
    }
}

struct AutoFillField: NSViewRepresentable {
    @Binding var text: String
    let placeholder: String
    let kind: PromptModel.Field
    @Binding var focus: PromptModel.Field?
    var onSubmit: () -> Void

    func makeNSView(context: Context) -> NSTextField {
        let onFocus: () -> Void = { [weak coordinator = context.coordinator] in coordinator?.focused() }
        let f: NSTextField = kind == .pass ? FocusSecureField(onFocus: onFocus) : FocusField(onFocus: onFocus)
        f.isBordered = false
        f.drawsBackground = false
        f.focusRingType = .none
        f.font = .systemFont(ofSize: 14)
        f.textColor = NSColor(Hangar.text)
        f.placeholderAttributedString = NSAttributedString(string: placeholder, attributes: [
            .foregroundColor: NSColor(Hangar.faint), .font: NSFont.systemFont(ofSize: 14)])
        f.contentType = kind == .user ? .username : kind == .pass ? .password : .oneTimeCode
        f.delegate = context.coordinator
        f.target = context.coordinator
        f.action = #selector(Coordinator.submit)
        return f
    }

    func updateNSView(_ f: NSTextField, context: Context) {
        context.coordinator.parent = self
        if f.stringValue != text { f.stringValue = text }
        if focus == kind, let w = f.window, w.firstResponder != f.currentEditor() {
            DispatchQueue.main.async { w.makeFirstResponder(f) }
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, NSTextFieldDelegate {
        var parent: AutoFillField
        init(_ p: AutoFillField) { parent = p }
        func controlTextDidChange(_ n: Notification) {
            if let f = n.object as? NSTextField { parent.text = f.stringValue }
        }
        func controlTextDidBeginEditing(_ n: Notification) { focused() }
        func focused() { if parent.focus != parent.kind { parent.focus = parent.kind } }
        @objc func submit(_ sender: NSTextField) {
            parent.text = sender.stringValue
            parent.onSubmit()
        }
    }
}

/// Text fields that say when they get focus (the outline follows the caret,
/// even before anything is typed or when AutoFill fills them).
final class FocusField: NSTextField {
    var onFocus: () -> Void = {}
    convenience init(onFocus: @escaping () -> Void) { self.init(frame: .zero); self.onFocus = onFocus }
    override func becomeFirstResponder() -> Bool {
        let ok = super.becomeFirstResponder()
        if ok { DispatchQueue.main.async(execute: onFocus) }
        return ok
    }
}

final class FocusSecureField: NSSecureTextField {
    var onFocus: () -> Void = {}
    convenience init(onFocus: @escaping () -> Void) { self.init(frame: .zero); self.onFocus = onFocus }
    override func becomeFirstResponder() -> Bool {
        let ok = super.becomeFirstResponder()
        if ok { DispatchQueue.main.async(execute: onFocus) }
        return ok
    }
}
