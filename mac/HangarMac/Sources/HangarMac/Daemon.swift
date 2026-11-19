import CryptoKit
import Foundation

/// Talks to the Hangar daemon on 127.0.0.1, with the same admin token the CLI
/// derives: HMAC-SHA256(secret, "admin"), hex, first 48 characters.
struct Daemon: Sendable {
    let base: URL
    let token: String

    struct Space: Decodable, Identifiable, Sendable {
        let name: String
        let status: String
        let model: String?
        let task: String?
        let computer: String?     // "linux", or "mac" when its screen is a macOS VM
        let vm: String?
        var id: String { name }
        var isMac: Bool { computer == "mac" }
    }

    enum Problem: Error, CustomStringConvertible {
        case noSecret(String)
        case http(Int)
        var description: String {
            switch self {
            case .noSecret(let p): return "No Hangar secret at \(p). Run any `hangar` command once."
            case .http(let c): return "The daemon answered \(c)."
            }
        }
    }

    static func local() throws -> Daemon {
        let env = ProcessInfo.processInfo.environment
        let state = env["HANGAR_STATE_DIR"].map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/state/hangar")
        let path = state.appendingPathComponent("secret").path
        guard let raw = try? String(contentsOfFile: path, encoding: .utf8) else { throw Problem.noSecret(path) }
        let key = SymmetricKey(data: Data(raw.trimmingCharacters(in: .whitespacesAndNewlines).utf8))
        let mac = HMAC<SHA256>.authenticationCode(for: Data("admin".utf8), using: key)
        let hex = mac.map { String(format: "%02x", $0) }.joined()
        let port = env["HANGAR_DAEMON_PORT"] ?? "7700"
        return Daemon(base: URL(string: "http://127.0.0.1:\(port)")!, token: String(hex.prefix(48)))
    }

    private func get(_ path: String, query: [URLQueryItem] = []) async throws -> Data {
        var c = URLComponents(url: base.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { c.queryItems = query }
        var req = URLRequest(url: c.url!, timeoutInterval: 15)
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let (data, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200 else { throw Problem.http(code) }
        return data
    }

    /// Any admin request (JSON in, the response body out).
    func request(_ method: String, _ path: String, body: Data? = nil) async throws -> Data {
        var req = URLRequest(url: base.appendingPathComponent(path), timeoutInterval: 60)
        req.httpMethod = method
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let body {
            req.httpBody = body
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard code < 500 else { throw Problem.http(code) }
        return data
    }

    func alive() async -> Bool {
        var req = URLRequest(url: base.appendingPathComponent("healthz"), timeoutInterval: 2)
        req.httpMethod = "GET"
        let code = (try? await URLSession.shared.data(for: req)).flatMap { ($0.1 as? HTTPURLResponse)?.statusCode }
        return code == 200
    }

    func spaces() async throws -> [Space] {
        try JSONDecoder().decode([Space].self, from: try await get("api/agents"))
    }

    func screenshot(_ space: String, width: Int) async throws -> Data {
        try await get("api/agents/\(space)/screenshot.png", query: [URLQueryItem(name: "w", value: String(width))])
    }
}
