from __future__ import annotations

import unittest

from arthur_loop.reporting import poll_timing_rows, render_poll_timing_table


class ReportingTests(unittest.TestCase):
    def test_poll_timing_rows_measure_scheduled_actual_and_drift(self) -> None:
        jobs = [
            {
                "job_id": "BQ-DEMO_APP-PLAN-001",
                "project_id": "DEMO_APP",
                "status": "completed_with_warnings",
                "submitted_at": "2026-06-22T05:05:42Z",
                "completed_at": "2026-06-22T05:07:16Z",
            }
        ]
        events = [
            {
                "job_id": "BQ-DEMO_APP-PLAN-001",
                "event_type": "attempt_submitted",
                "at": "2026-06-22T05:05:42Z",
                "data": {"next_poll_at": "2026-06-22T05:06:42Z"},
            },
            {
                "job_id": "BQ-DEMO_APP-PLAN-001",
                "event_type": "first_poll_result",
                "at": "2026-06-22T05:07:16Z",
                "data": {"marker_found": True},
            },
        ]

        rows = poll_timing_rows(jobs, events)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].scheduled_delay_minutes, 1)
        self.assertEqual(rows[0].actual_first_poll_delay_minutes, 1.567)
        self.assertEqual(rows[0].poll_drift_seconds, 34)
        self.assertEqual(rows[0].response_latency_minutes, 1.567)

        table = render_poll_timing_table(rows)
        self.assertIn("BQ-DEMO_APP-PLAN-001", table)
        self.assertIn("34 sec", table)


if __name__ == "__main__":
    unittest.main()
