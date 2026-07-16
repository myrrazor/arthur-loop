import Foundation

/// Decoded shape of `arthur status --json` (snake_case handled by the decoder).
struct LoopStatus: Decodable, Equatable {
    let generatedAt: String
    let state: String
    let sessions: [Session]
    let queue: [Job]
    let hiddenTerminalJobs: Int
    let projects: [Project]
    let decisions: [Decision]
    let quota: Quota?
    let browserLock: BrowserLock?
    let tick: Tick

    struct Session: Decodable, Equatable {
        let sessionId: String
        let role: String
        let projectId: String?
        let state: String
        let activity: String
        let at: String
        let stale: Bool
    }

    struct Job: Decodable, Equatable {
        let jobId: String
        let projectId: String
        let status: String
        let attemptCount: Int
        let nextPollAt: String?
    }

    struct Project: Decodable, Equatable {
        let projectId: String
        let summary: String
        let blockedByDecision: Bool
    }

    struct Decision: Decodable, Equatable {
        let title: String
        let projectId: String
    }

    struct Quota: Decodable, Equatable {
        let leftPercent: Double?
        let state: String
        let reset: String?
        let capturedAt: String?
    }

    struct BrowserLock: Decodable, Equatable {
        let holder: String
        let fresh: Bool
        let staleAfter: String?
    }

    struct Tick: Decodable, Equatable {
        let nextDueAt: String?
        let staleJobIds: [String]?
    }

    /// Items that should pull a human in — drives the menu bar badge.
    var attentionCount: Int {
        decisions.count + (tick.staleJobIds?.count ?? 0)
    }

    static func decode(_ data: Data) throws -> LoopStatus {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(LoopStatus.self, from: data)
    }
}

enum RelativeTime {
    private static let iso: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    static func parse(_ value: String?) -> Date? {
        guard let value else { return nil }
        return iso.date(from: value)
    }

    /// "now", "3m ago", "in 4m", "2h 5m ago" — mirrors the terminal dashboard.
    static func describe(_ value: String?, relativeTo now: Date = Date()) -> String {
        guard let moment = parse(value) else { return "—" }
        let seconds = moment.timeIntervalSince(now)
        if abs(seconds) < 45 { return "now" }
        let span = spanText(abs(seconds))
        return seconds > 0 ? "in \(span)" : "\(span) ago"
    }

    static func spanText(_ seconds: TimeInterval) -> String {
        let minutes = Int((seconds / 60).rounded())
        if minutes < 60 { return "\(minutes)m" }
        let hours = minutes / 60
        let rem = minutes % 60
        if hours < 24 { return rem == 0 ? "\(hours)h" : "\(hours)h \(rem)m" }
        let days = hours / 24
        let remHours = hours % 24
        return remHours == 0 ? "\(days)d" : "\(days)d \(remHours)h"
    }
}
