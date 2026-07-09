import SwiftUI

// color vocabulary matches the terminal dashboard exactly
func loopStateColor(_ state: String) -> Color {
    switch state {
    case "POLL_DUE": return .green
    case "BLOCKED_BY_BROWSER_LOCK": return .yellow
    case "BLOCKED_BY_QUOTA": return .red
    case "HUMAN_INPUT_REQUIRED": return .purple
    case "WAIT": return .cyan
    default: return .gray
    }
}

func sessionStateColor(_ state: String) -> Color {
    switch state {
    case "working": return .green
    case "waiting": return .yellow
    case "blocked": return .red
    default: return .gray
    }
}

func jobStatusColor(_ status: String) -> Color {
    switch status {
    case "queued": return .cyan
    case "claimed", "submitted": return .yellow
    case "waiting_for_chatgpt": return .blue
    case "stopped_no_output", "needs_recovery": return .red
    default: return .gray
    }
}

func quotaColor(_ state: String) -> Color {
    switch state {
    case "GREEN": return .green
    case "YELLOW": return .yellow
    case "RED": return .red
    default: return .gray
    }
}

struct StateChip: View {
    let state: String

    var body: some View {
        let color = loopStateColor(state)
        Text(state.replacingOccurrences(of: "_", with: " "))
            .font(.caption2.weight(.semibold))
            .foregroundStyle(color)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(Capsule().fill(color.opacity(0.16)))
    }
}

struct SectionHeader: View {
    let title: String
    var trailing: String = ""

    var body: some View {
        HStack {
            Text(title)
                .font(.caption2.weight(.semibold))
                .kerning(0.8)
                .foregroundStyle(.secondary)
            Spacer()
            if !trailing.isEmpty {
                Text(trailing)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
        }
    }
}

struct DotRow: View {
    let color: Color
    let title: String
    let subtitle: String
    let trailing: String
    var trailingColor: Color? = nil
    var dimmed: Bool = false

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Circle()
                .fill(color)
                .frame(width: 7, height: 7)
                .alignmentGuide(.firstTextBaseline) { d in d[VerticalAlignment.center] + 3 }
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                    .font(.footnote.weight(.medium))
                    .lineLimit(1)
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 8)
            Group {
                if let trailingColor {
                    Text(trailing).foregroundStyle(trailingColor)
                } else {
                    Text(trailing).foregroundStyle(.tertiary)
                }
            }
            .font(.caption)
            .monospacedDigit()
        }
        .opacity(dimmed ? 0.5 : 1)
    }
}

struct EmptyRow: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(.tertiary)
    }
}

struct ContentView: View {
    let status: LoopStatus?
    let errorText: String?
    let instanceName: String
    let updatedText: String
    var now: Date = Date()
    var onRefresh: (() -> Void)?
    var onQuit: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            Divider()
            if let status {
                workers(status)
                queue(status)
                projects(status)
                if !status.decisions.isEmpty {
                    decisions(status)
                }
                Divider()
                resources(status)
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    Text(errorText ?? "Loading…")
                        .font(.footnote)
                        .foregroundStyle(errorText == nil ? .secondary : Color.red)
                    Text("Configure instances in ~/.config/arthurbar/config.json")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
            }
            Divider()
            footer
        }
        .padding(12)
        .frame(width: 320)
    }

    private var header: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 2) {
                Text("ARTHUR LOOP")
                    .font(.caption2.weight(.semibold))
                    .kerning(1.4)
                    .foregroundStyle(.secondary)
                Text(instanceName)
                    .font(.headline)
            }
            Spacer()
            if let status {
                StateChip(state: status.state)
            }
        }
    }

    private func workers(_ status: LoopStatus) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            SectionHeader(title: "WORKERS", trailing: status.sessions.isEmpty ? "" : "\(status.sessions.count)")
            if status.sessions.isEmpty {
                EmptyRow(text: "no sessions reporting")
            } else {
                ForEach(status.sessions, id: \.sessionId) { session in
                    DotRow(
                        color: sessionStateColor(session.state),
                        title: session.projectId.map { "\(session.sessionId) · \($0)" } ?? session.sessionId,
                        subtitle: session.activity,
                        trailing: RelativeTime.describe(session.at, relativeTo: now)
                            + (session.stale ? " · stale" : ""),
                        dimmed: session.stale
                    )
                }
            }
        }
    }

    private func queue(_ status: LoopStatus) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            SectionHeader(
                title: "QUEUE",
                trailing: status.hiddenTerminalJobs > 0 ? "\(status.hiddenTerminalJobs) finished hidden" : ""
            )
            if status.queue.isEmpty {
                EmptyRow(text: "queue is empty")
            } else {
                ForEach(status.queue, id: \.jobId) { job in
                    let eta = pollEta(job)
                    DotRow(
                        color: jobStatusColor(job.status),
                        title: job.jobId,
                        subtitle: "\(job.status) · attempt \(job.attemptCount)",
                        trailing: eta,
                        trailingColor: eta == "ready" ? .green : (eta.hasPrefix("overdue") ? .red : nil)
                    )
                }
            }
        }
    }

    private func pollEta(_ job: LoopStatus.Job) -> String {
        if job.status == "queued" { return "ready" }
        let described = RelativeTime.describe(job.nextPollAt, relativeTo: now)
        if described.hasSuffix(" ago") {
            return "overdue \(described.dropLast(4))"
        }
        return described
    }

    private func projects(_ status: LoopStatus) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            SectionHeader(title: "PROJECTS")
            if status.projects.isEmpty {
                EmptyRow(text: "no projects yet")
            } else {
                ForEach(status.projects, id: \.projectId) { project in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(project.projectId)
                                .font(.footnote.weight(.medium))
                            Text(project.summary)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                        Spacer(minLength: 8)
                        if project.blockedByDecision {
                            Text("BLOCKED")
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(.red)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(Capsule().fill(Color.red.opacity(0.14)))
                        }
                    }
                }
            }
        }
    }

    private func decisions(_ status: LoopStatus) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            SectionHeader(title: "NEEDS YOU")
            ForEach(status.decisions, id: \.title) { decision in
                HStack(spacing: 6) {
                    Image(systemName: "person.crop.circle.badge.questionmark")
                        .font(.caption)
                        .foregroundStyle(.purple)
                    Text(decision.title)
                        .font(.footnote)
                        .foregroundStyle(.purple)
                        .lineLimit(1)
                    Spacer(minLength: 0)
                }
            }
        }
    }

    private func resources(_ status: LoopStatus) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if let quota = status.quota {
                let color = quotaColor(quota.state)
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text("Quota")
                            .font(.footnote.weight(.medium))
                        Spacer()
                        if let left = quota.leftPercent {
                            Text("\(Int(left.rounded()))% left")
                                .font(.caption)
                                .foregroundStyle(color)
                                .monospacedDigit()
                        } else {
                            Text("no snapshot yet")
                                .font(.caption)
                                .foregroundStyle(.tertiary)
                        }
                    }
                    UsageBar(fraction: (quota.leftPercent ?? 0) / 100.0, tint: color)
                    if let reset = quota.reset {
                        Text(reset)
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                }
            } else {
                HStack {
                    Text("Quota")
                        .font(.footnote.weight(.medium))
                    Spacer()
                    Text("governor off")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
            }

            HStack {
                Text("Browser lock")
                    .font(.footnote.weight(.medium))
                Spacer()
                if let lock = status.browserLock {
                    Text(lock.fresh ? "held by \(lock.holder)" : "stale — takeover allowed")
                        .font(.caption)
                        .foregroundStyle(lock.fresh ? Color.yellow : .secondary)
                } else {
                    Text("free")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
            }
        }
    }

    private var footer: some View {
        HStack(spacing: 8) {
            // plain views when no callbacks (snapshot mode) — Button chrome
            // does not survive ImageRenderer, and the layout must stay identical
            if let onRefresh {
                Button(action: onRefresh) {
                    Image(systemName: "arrow.clockwise").font(.caption)
                }
                .buttonStyle(.borderless)
                .help("Refresh now")
            } else {
                Image(systemName: "arrow.clockwise")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Text(updatedText)
                .font(.caption2)
                .foregroundStyle(.tertiary)
            Spacer()
            if let onQuit {
                Button("Quit", action: onQuit)
                    .buttonStyle(.borderless)
                    .font(.caption)
            } else {
                Text("Quit")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }
}
