import XCTest
@testable import ArthurBar

final class ModelTests: XCTestCase {
    let fixture = """
    {
      "generated_at": "2026-07-09T12:00:00Z",
      "root": "/tmp/demo",
      "state": "POLL_DUE",
      "tick": {"status": "POLL_DUE", "next_due_at": "2026-07-09T12:01:00Z", "stale_job_ids": ["BQ-OLD-001"]},
      "sessions": [
        {"session_id": "master", "role": "master", "project_id": null,
         "state": "working", "activity": "orchestrating", "at": "2026-07-09T11:57:00Z",
         "stale": false, "age_minutes": 3.0}
      ],
      "queue": [
        {"job_id": "BQ-DEMO_APP-002", "project_id": "DEMO_APP", "status": "queued",
         "attempt_count": 0, "next_poll_at": null, "target_chat_title": "Demo App Planning",
         "target_chat_url": "manual", "priority": 100, "created_at": "2026-07-09T11:50:00Z"}
      ],
      "hidden_terminal_jobs": 2,
      "projects": [
        {"project_id": "DEMO_APP", "summary": "Sprint 2 implementing.",
         "blocked_by_decision": false, "state_path": "projects/DEMO_APP/state.md"}
      ],
      "decisions": [{"title": "SAMPLE_APP Auth Scope Gate", "project_id": "SAMPLE_APP"}],
      "quota": {"left_percent": 62, "state": "GREEN", "captured_at": "2026-07-09T11:55:00Z",
                "reset": "Resets 4:00 AM", "secondary_left_percent": null, "provider": "codex"},
      "browser_lock": null,
      "reserve_percent": 5.0
    }
    """

    func testDecodesStatusJson() throws {
        let status = try LoopStatus.decode(Data(fixture.utf8))

        XCTAssertEqual(status.state, "POLL_DUE")
        XCTAssertEqual(status.sessions.first?.sessionId, "master")
        XCTAssertEqual(status.queue.first?.jobId, "BQ-DEMO_APP-002")
        XCTAssertEqual(status.hiddenTerminalJobs, 2)
        XCTAssertEqual(status.projects.first?.blockedByDecision, false)
        XCTAssertEqual(status.quota?.leftPercent, 62)
        XCTAssertNil(status.browserLock)
        XCTAssertEqual(status.tick.staleJobIds, ["BQ-OLD-001"])
    }

    func testAttentionCountsDecisionsAndStaleJobs() throws {
        let status = try LoopStatus.decode(Data(fixture.utf8))
        XCTAssertEqual(status.attentionCount, 2)
    }

    func testRelativeTimeDescriptions() {
        let now = RelativeTime.parse("2026-07-09T12:00:00Z")!

        XCTAssertEqual(RelativeTime.describe("2026-07-09T11:57:00Z", relativeTo: now), "3m ago")
        XCTAssertEqual(RelativeTime.describe("2026-07-09T12:04:00Z", relativeTo: now), "in 4m")
        XCTAssertEqual(RelativeTime.describe("2026-07-09T12:00:20Z", relativeTo: now), "now")
        XCTAssertEqual(RelativeTime.describe(nil, relativeTo: now), "—")
        XCTAssertEqual(RelativeTime.describe("2026-07-10T13:00:00Z", relativeTo: now), "in 1d 1h")
    }
}
