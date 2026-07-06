from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from arthur_loop.usage_attribution import (
    append_snapshot,
    append_task_usage,
    estimate_task_usage,
    latest_snapshots,
    render_usage_dashboard,
    snapshot_from_codexbar_json,
    task_usage_records,
)


SAMPLE_BEFORE = [
    {
        "provider": "codex",
        "source": "openai-web",
        "usage": {
            "accountEmail": "person@example.com",
            "loginMethod": "Pro 20x",
            "primary": {
                "usedPercent": 24,
                "windowMinutes": 300,
                "resetDescription": "Resets 4:28 AM",
            },
            "secondary": {
                "usedPercent": 42,
                "windowMinutes": 10080,
                "resetDescription": "Resets Jun 24, 2026 5:19 PM",
            },
        },
    }
]

SAMPLE_AFTER = [
    {
        "provider": "codex",
        "source": "openai-web",
        "usage": {
            "accountEmail": "person@example.com",
            "loginMethod": "Pro 20x",
            "primary": {
                "usedPercent": 26,
                "windowMinutes": 300,
                "resetDescription": "Resets 4:28 AM",
            },
            "secondary": {
                "usedPercent": 43,
                "windowMinutes": 10080,
                "resetDescription": "Resets Jun 24, 2026 5:19 PM",
            },
        },
    }
]


class UsageAttributionTests(unittest.TestCase):
    def test_normalizes_codexbar_json_and_estimates_high_confidence_delta(self) -> None:
        before = snapshot_from_codexbar_json(
            SAMPLE_BEFORE,
            snapshot_id="before",
            captured_at="2026-06-22T06:00:00Z",
        )
        after = snapshot_from_codexbar_json(
            SAMPLE_AFTER,
            snapshot_id="after",
            captured_at="2026-06-22T06:10:00Z",
        )

        self.assertEqual(before.primary_used_percent, 24)
        self.assertEqual(before.primary_left_percent, 76)

        estimate = estimate_task_usage(
            before,
            after,
            task_id="arthur-loop-master-001",
            project_id="ARTHUR_LOOP",
            role="Master Orchestrator",
            task_label="Run Demo App plan loop",
        )

        self.assertEqual(estimate.primary_delta_used_percent, 2)
        self.assertEqual(estimate.secondary_delta_used_percent, 1)
        self.assertEqual(estimate.confidence, "HIGH")

        dashboard = render_usage_dashboard([estimate])
        self.assertIn("| ARTHUR_LOOP | Master Orchestrator | Run Demo App plan loop | +2% | HIGH |", dashboard)

    def test_snapshot_and_task_ledgers_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = snapshot_from_codexbar_json(SAMPLE_BEFORE, snapshot_id="before")
            after = snapshot_from_codexbar_json(SAMPLE_AFTER, snapshot_id="after")

            append_snapshot(root, before)
            append_snapshot(root, after)
            snapshots = latest_snapshots(root)
            self.assertEqual(set(snapshots), {"before", "after"})

            estimate = estimate_task_usage(
                snapshots["before"],
                snapshots["after"],
                task_id="task",
                project_id="DEMO_APP",
                role="Codex Coding Session",
                task_label="Sprint plan",
                active_codex_tasks=2,
            )
            append_task_usage(root, estimate)

            records = task_usage_records(root)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].confidence, "LOW")

    def test_negative_delta_marks_reset_crossing_unknown(self) -> None:
        before = snapshot_from_codexbar_json(SAMPLE_BEFORE, snapshot_id="before")
        after_payload = [dict(SAMPLE_AFTER[0])]
        after_payload[0]["usage"] = dict(SAMPLE_AFTER[0]["usage"])
        after_payload[0]["usage"]["primary"] = dict(SAMPLE_AFTER[0]["usage"]["primary"])
        after_payload[0]["usage"]["primary"]["usedPercent"] = 2
        after = snapshot_from_codexbar_json(after_payload, snapshot_id="after")

        estimate = estimate_task_usage(
            before,
            after,
            task_id="task",
            project_id="SAMPLE_APP",
            role="Project Loop Manager",
            task_label="Review plan",
        )

        self.assertEqual(estimate.confidence, "UNKNOWN")
        self.assertTrue(any("reset likely crossed" in note for note in estimate.notes))

    def test_relative_reset_description_change_does_not_force_unknown(self) -> None:
        before = snapshot_from_codexbar_json(SAMPLE_BEFORE, snapshot_id="before")
        after_payload = [dict(SAMPLE_AFTER[0])]
        after_payload[0]["usage"] = dict(SAMPLE_AFTER[0]["usage"])
        after_payload[0]["usage"]["primary"] = dict(SAMPLE_AFTER[0]["usage"]["primary"])
        after_payload[0]["usage"]["primary"]["resetDescription"] = "Resets 9:00 AM"
        after = snapshot_from_codexbar_json(after_payload, snapshot_id="after")

        estimate = estimate_task_usage(
            before,
            after,
            task_id="task",
            project_id="SAMPLE_APP",
            role="Project Loop Manager",
            task_label="Review plan",
        )

        self.assertEqual(estimate.confidence, "HIGH")
        self.assertTrue(any("changed but no reset crossing" in note for note in estimate.notes))

    def test_absolute_reset_window_moving_forward_marks_unknown(self) -> None:
        before_payload = [dict(SAMPLE_BEFORE[0])]
        before_payload[0]["usage"] = dict(SAMPLE_BEFORE[0]["usage"])
        before_payload[0]["usage"]["primary"] = dict(SAMPLE_BEFORE[0]["usage"]["primary"])
        before_payload[0]["usage"]["primary"]["resetDescription"] = "Resets Jun 24, 2026 5:19 PM"
        after_payload = [dict(SAMPLE_AFTER[0])]
        after_payload[0]["usage"] = dict(SAMPLE_AFTER[0]["usage"])
        after_payload[0]["usage"]["primary"] = dict(SAMPLE_AFTER[0]["usage"]["primary"])
        after_payload[0]["usage"]["primary"]["resetDescription"] = "Resets Jun 25, 2026 5:19 PM"

        before = snapshot_from_codexbar_json(before_payload, snapshot_id="before", captured_at="2026-06-24T16:00:00Z")
        after = snapshot_from_codexbar_json(after_payload, snapshot_id="after", captured_at="2026-06-24T18:00:00Z")

        estimate = estimate_task_usage(
            before,
            after,
            task_id="task",
            project_id="SAMPLE_APP",
            role="Project Loop Manager",
            task_label="Review plan",
        )

        self.assertEqual(estimate.confidence, "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
