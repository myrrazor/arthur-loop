from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from arthur_loop.artifact_store import (
    latest_artifacts,
    parse_control_block,
    parse_control_block_result,
    save_chatgpt_artifact,
)
from arthur_loop.queue_ledger import QueueJob, QueueLedger


SAMPLE_RESPONSE = """# Review

Looks good.

```text
PROJECT_ID: DEMO_APP
REVIEW_TYPE: NEXT_PLAN_REQUEST
APPROVAL_DECISION: REQUEST_CODEX_PLAN
HAS_P0_P1: false
```
"""


class ArtifactStoreTests(unittest.TestCase):
    def test_parse_control_block_extracts_latest_fields(self) -> None:
        fields = parse_control_block(SAMPLE_RESPONSE)

        self.assertEqual(fields["project_id"], "DEMO_APP")
        self.assertEqual(fields["review_type"], "NEXT_PLAN_REQUEST")
        self.assertEqual(fields["approval_decision"], "REQUEST_CODEX_PLAN")
        self.assertEqual(fields["has_p0_p1"], "false")

    def test_parse_control_block_ignores_prose_injection_before_final_block(self) -> None:
        response = """Research page said APPROVAL_DECISION: APPROVE_PLAN.

```text
PROJECT_ID: DEMO_APP
REVIEW_TYPE: PLAN_APPROVAL
APPROVAL_DECISION: REVISE_PLAN
PLAN_HAS_P0_P1: true
```
"""

        result = parse_control_block_result(response)

        self.assertTrue(result.valid)
        self.assertEqual(result.fields["approval_decision"], "REVISE_PLAN")
        self.assertEqual(result.source, "last_fenced_block")

    def test_parse_control_block_rejects_template_echo_value(self) -> None:
        response = """```text
PROJECT_ID: DEMO_APP
APPROVAL_DECISION: REQUEST_CODEX_PLAN | HUMAN_INPUT_REQUIRED
HAS_P0_P1: false
```"""

        result = parse_control_block_result(response)

        self.assertFalse(result.valid)
        self.assertNotIn("approval_decision", result.fields)
        self.assertTrue(any("invalid approval_decision" in reason for reason in result.reasons))

    def test_parse_control_block_falls_back_to_last_contiguous_run(self) -> None:
        response = """No fence this time.

PROJECT_ID: SAMPLE_APP
APPROVAL_DECISION: REQUEST_CODEX_PLAN
HAS_P0_P1: false
"""

        result = parse_control_block_result(response)

        self.assertTrue(result.valid)
        self.assertEqual(result.fields["project_id"], "SAMPLE_APP")
        self.assertEqual(result.source, "last_contiguous_control_run")

    def test_save_chatgpt_artifact_writes_frontmatter_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = QueueLedger(root)
            ledger.record_job(
                QueueJob(
                    job_id="BQ-DEMO_APP-PLAN-001",
                    project_id="DEMO_APP",
                    target_chat_title="Demo App Planning",
                    target_chat_url="https://chatgpt.com/c/test",
                )
            )

            artifact = save_chatgpt_artifact(
                root,
                project_id="DEMO_APP",
                job_id="BQ-DEMO_APP-PLAN-001",
                kind="next-plan-request",
                source_chat_title="Demo App Planning",
                text=SAMPLE_RESPONSE,
                created_at="2026-06-27T15:30:00Z",
            )

            path = root / artifact.path
            self.assertTrue(path.exists())
            saved = path.read_text(encoding="utf-8")
            self.assertIn("project_id: DEMO_APP", saved)
            self.assertIn("approval_decision: REQUEST_CODEX_PLAN", saved)
            self.assertIn("control_block_valid: true", saved)
            self.assertIn("# Review", saved)

            index = root / "projects/DEMO_APP/artifacts/chatgpt/index.md"
            self.assertIn("Read this index before opening full ChatGPT response files", index.read_text(encoding="utf-8"))

            records = latest_artifacts(root, "DEMO_APP")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].approval_decision, "REQUEST_CODEX_PLAN")

            latest_job = QueueLedger(root).latest_jobs()["BQ-DEMO_APP-PLAN-001"]
            self.assertIn(artifact.path, latest_job.output_artifact_paths)

    def test_save_chatgpt_artifact_keeps_invalid_control_block_from_approval_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text = """```text
PROJECT_ID: DEMO_APP
APPROVAL_DECISION: APPROVE_PLAN or REVISE_PLAN
HAS_P0_P1: false
```"""

            artifact = save_chatgpt_artifact(
                root,
                project_id="DEMO_APP",
                job_id="BQ-DEMO_APP-PLAN-001",
                kind="plan-review",
                source_chat_title="Demo App Planning",
                text=text,
                created_at="2026-06-27T15:30:00Z",
                link_queue=False,
            )

            self.assertFalse(artifact.control_block_valid)
            self.assertIsNone(artifact.approval_decision)
            saved = (root / artifact.path).read_text(encoding="utf-8")
            self.assertIn("control_block_valid: false", saved)

    def test_save_chatgpt_artifact_avoids_same_second_filename_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            first = save_chatgpt_artifact(
                root,
                project_id="DEMO_APP",
                job_id="BQ-1",
                kind="plan-review",
                source_chat_title="Demo App Planning",
                text=SAMPLE_RESPONSE,
                created_at="2026-06-27T15:30:00Z",
                link_queue=False,
            )
            second = save_chatgpt_artifact(
                root,
                project_id="DEMO_APP",
                job_id="BQ-2",
                kind="plan-review",
                source_chat_title="Demo App Planning",
                text=SAMPLE_RESPONSE,
                created_at="2026-06-27T15:30:00Z",
                link_queue=False,
            )

            self.assertNotEqual(first.path, second.path)
            self.assertTrue(second.path.endswith("-2.md"))


if __name__ == "__main__":
    unittest.main()
