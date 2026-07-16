import Foundation

struct InstanceConfig: Decodable {
    var name: String?
    var root: String
    var arthur: String?
}

struct BarConfig: Decodable {
    var instances: [InstanceConfig]
    var refreshSeconds: Double?
}

enum ConfigLoader {
    static var configURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config/arthurbar/config.json")
    }

    /// First instance from ~/.config/arthurbar/config.json, else $ARTHURBAR_ROOT, else cwd.
    static func load() -> (instance: InstanceConfig, refreshSeconds: Double) {
        if let data = try? Data(contentsOf: configURL) {
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            if let config = try? decoder.decode(BarConfig.self, from: data),
               let first = config.instances.first {
                return (first, config.refreshSeconds ?? 30)
            }
        }
        if let root = ProcessInfo.processInfo.environment["ARTHURBAR_ROOT"] {
            return (InstanceConfig(name: nil, root: root, arthur: nil), 30)
        }
        return (InstanceConfig(name: nil, root: FileManager.default.currentDirectoryPath, arthur: nil), 30)
    }
}

enum FetchError: LocalizedError {
    case arthurNotFound
    case commandFailed(String)

    var errorDescription: String? {
        switch self {
        case .arthurNotFound:
            return "arthur CLI not found — set \"arthur\" in ~/.config/arthurbar/config.json"
        case .commandFailed(let detail):
            return detail
        }
    }
}

enum ArthurRunner {
    /// GUI apps don't inherit shell PATH, so probe the usual install spots.
    static func resolveArthur(explicit: String?) -> String? {
        var candidates: [String] = []
        if let explicit { candidates.append((explicit as NSString).expandingTildeInPath) }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        candidates.append(contentsOf: [
            "\(home)/.local/bin/arthur",
            "/opt/homebrew/bin/arthur",
            "/usr/local/bin/arthur",
        ])
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    static func fetch(root: String, arthur explicit: String? = nil) throws -> LoopStatus {
        guard let arthur = resolveArthur(explicit: explicit) else {
            throw FetchError.arthurNotFound
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: arthur)
        process.arguments = ["status", "--json", "--root", (root as NSString).expandingTildeInPath]
        let stdout = Pipe()
        let stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr

        try process.run()
        let data = stdout.fileHandleForReading.readDataToEndOfFile()
        let errData = stderr.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()

        guard process.terminationStatus == 0 else {
            let detail = String(data: errData, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines)
            throw FetchError.commandFailed(
                detail?.isEmpty == false ? detail! : "arthur status exited \(process.terminationStatus)"
            )
        }
        return try LoopStatus.decode(data)
    }
}

/// Published state for the popover; all mutation lands on the main thread.
final class StatusStore: ObservableObject {
    @Published var status: LoopStatus?
    @Published var errorText: String?
    @Published var lastUpdated: Date?

    let instance: InstanceConfig
    let refreshSeconds: Double

    var instanceName: String {
        instance.name ?? URL(fileURLWithPath: instance.root).lastPathComponent
    }

    init(instance: InstanceConfig, refreshSeconds: Double) {
        self.instance = instance
        self.refreshSeconds = refreshSeconds
    }

    func refresh() {
        let root = instance.root
        let arthur = instance.arthur
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            do {
                let status = try ArthurRunner.fetch(root: root, arthur: arthur)
                DispatchQueue.main.async {
                    self?.status = status
                    self?.errorText = nil
                    self?.lastUpdated = Date()
                }
            } catch {
                DispatchQueue.main.async {
                    self?.errorText = error.localizedDescription
                    self?.lastUpdated = Date()
                }
            }
        }
    }
}
