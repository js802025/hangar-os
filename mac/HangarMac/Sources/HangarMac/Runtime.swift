import AppKit
import Foundation

/// Starting Hangar's daemon on this Mac, with agents on Apple's container
/// (mac/bin/hangar sets HANGAR_RUNTIME=apple), and the terminal-mac bits.
enum Runtime {
    /// The repo checkout this app runs from ($HANGAR_REPO, or a parent of the
    /// executable or the working directory that has mac/bin/hangar).
    static func repo() -> URL? {
        let fm = FileManager.default
        if let r = ProcessInfo.processInfo.environment["HANGAR_REPO"] { return URL(fileURLWithPath: r) }
        if let r = Bundle.main.object(forInfoDictionaryKey: "HangarRepo") as? String,   // set by build-app.sh
           fm.isExecutableFile(atPath: r + "/mac/bin/hangar") { return URL(fileURLWithPath: r) }
        let starts = [Bundle.main.executableURL, URL(fileURLWithPath: fm.currentDirectoryPath)].compactMap { $0 }
        for start in starts {
            var dir = start.standardizedFileURL
            for _ in 0..<8 {
                if fm.isExecutableFile(atPath: dir.appendingPathComponent("mac/bin/hangar").path) { return dir }
                dir = dir.deletingLastPathComponent()
            }
        }
        return nil
    }

    static var hangar: URL? { repo()?.appendingPathComponent("mac/bin/hangar") }

    /// Start `hangar daemon` in the background (its log goes to the state folder).
    static func startDaemon() throws {
        guard let exe = hangar else { throw NSError(domain: "Hangar", code: 1,
            userInfo: [NSLocalizedDescriptionKey: "Can't find mac/bin/hangar (set HANGAR_REPO)"]) }
        let log = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".local/state/hangar/daemon-mac.log")
        try? FileManager.default.createDirectory(at: log.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: log.path, contents: nil)
        let p = Process()
        p.executableURL = exe
        p.arguments = ["daemon"]
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = (env["PATH"] ?? "") + ":/opt/homebrew/bin:/usr/local/bin:" + NSHomeDirectory() + "/.local/bin"
        p.environment = env
        let h = try FileHandle(forWritingTo: log)
        p.standardOutput = h
        p.standardError = h
        try p.run()
    }

    /// The helper app Hangar's windows run from, so the Dock says "Hangar" (build-app.sh).
    static var uiPython: String? {
        let p = Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/Hangar.app/Contents/MacOS/Hangar").path
        return FileManager.default.isExecutableFile(atPath: p) ? p : nil
    }

    /// Run a hangar command in the background (e.g. `open` to show Hangar's window).
    static func run(_ args: [String]) {
        guard let exe = hangar else { return }
        let p = Process()
        p.executableURL = exe
        p.arguments = args
        var env = ProcessInfo.processInfo.environment
        if let ui = uiPython { env["HANGAR_UI_PYTHON"] = ui }
        env["PATH"] = (env["PATH"] ?? "") + ":/opt/homebrew/bin:/usr/local/bin:" + NSHomeDirectory() + "/.local/bin"
        p.environment = env
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        try? p.run()
    }

    /// Run a hangar command in Terminal (for the interactive ones, like Mac setup).
    static func inTerminal(_ args: [String]) {
        guard let exe = hangar else { return }
        let quoted = ([exe.path] + args).map { "'" + $0.replacingOccurrences(of: "'", with: "'\\''") + "'" }
        let script = FileManager.default.temporaryDirectory.appendingPathComponent("hangar-\(UUID().uuidString).command")
        let body = "#!/bin/bash\nexport PATH=\"$PATH:/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin\"\n"
            + quoted.joined(separator: " ") + "\n"
        try? body.write(to: script, atomically: true, encoding: .utf8)
        try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: script.path)
        NSWorkspace.shared.open(script)
    }

    /// Show a Mac computer's own window (terminal-mac show), to take it over.
    static func showVM(_ vm: String) {
        let paths = ["/opt/homebrew/bin/terminal-mac", "/usr/local/bin/terminal-mac",
                     NSHomeDirectory() + "/.local/bin/terminal-mac"]
        guard let exe = ProcessInfo.processInfo.environment["HANGAR_TERMINAL_MAC"]
                ?? paths.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: exe)
        p.arguments = ["show", vm]
        try? p.run()
    }
}
