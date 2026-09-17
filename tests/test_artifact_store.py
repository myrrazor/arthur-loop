from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arthur_loop.artifact_store import (
    implementation_gate,
    latest_artifacts,
    parse_control_block,
    parse_control_block_result,
    save_chatgpt_artifact,
)
from arthur_loop.decisions import open_decision
from arthur_loop.queue_ledger import QueueJob, QueueLedger
from arthur_loop.tick import open_human_decision_projects


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
            index_text = index.read_text(encoding="utf-8")
            self.assertIn("Read this index before opening full advisor/executor response files", index_text)
            self.assertIn("| ok |", index_text)

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


PLAN_OK = """```text
PROJECT_ID: DEMO_APP
REVIEW_TYPE: CODEX_PLAN
PLAN_STATUS: READY_FOR_CHATGPT_REVIEW
IMPLEMENTATION_STARTED: false
```"""

PLAN_IMPLEMENTED = PLAN_OK.replace("IMPLEMENTATION_STARTED: false", "IMPLEMENTATION_STARTED: true")

APPROVE = """```text
PROJECT_ID: DEMO_APP
REVIEW_TYPE: PLAN_APPROVAL
APPROVAL_DECISION: APPROVE_PLAN
PLAN_HAS_P0_P1: false
```"""

REVISE = APPROVE.replace("APPROVE_PLAN", "REVISE_PLAN")

HANDOFF = """```text
PROJECT_ID: DEMO_APP
SPRINT_ID: S1
REVIEW_TYPE: CODEX_IMPLEMENTATION_HANDOFF
IMPLEMENTATION_STATUS: COMPLETE
TESTS_RUN: true
READY_FOR_CHATGPT_REVIEW: true
```"""

SPRINT_APPROVED = """```text
PROJECT_ID: DEMO_APP
SPRINT_ID: S1
REVIEW_TYPE: SPRINT_REVIEW
APPROVAL_DECISION: APPROVE_SPRINT
HAS_P0_P1: false
```"""

NEEDS_HUMAN = """The scope is ambiguous; a human must pick.

```text
PROJECT_ID: DEMO_APP
REVIEW_TYPE: NEXT_PLAN_REQUEST
APPROVAL_DECISION: HUMAN_INPUT_REQUIRED
HAS_P0_P1: false
```"""


def _capture(root: Path, kind: str, text: str, job_id: str = "BQ-DEMO_APP-001", project_id: str = "DEMO_APP", **extra):
    return save_chatgpt_artifact(
        root,
        project_id=project_id,
        job_id=job_id,
        kind=kind,
        source_chat_title="test",
        text=text,
        link_queue=False,
        **extra,
    )


class ControlBlockRuleTests(unittest.TestCase):
    def test_plan_that_already_implemented_is_invalid(self) -> None:
        result = parse_control_block_result(PLAN_IMPLEMENTED, kind="plan", project_id="DEMO_APP")

        self.assertFalse(result.valid)
        self.assertTrue(any("implementation_started: true" in reason for reason in result.reasons))
        self.assertTrue(parse_control_block_result(PLAN_OK, kind="plan", project_id="DEMO_APP").valid)

    def test_review_type_must_match_the_captured_kind(self) -> None:
        result = parse_control_block_result(APPROVE, kind="sprint-review", project_id="DEMO_APP")

        self.assertFalse(result.valid)
        self.assertTrue(any("does not match kind sprint-review" in reason for reason in result.reasons))

    def test_project_id_must_match_the_captured_project(self) -> None:
        result = parse_control_block_result(APPROVE, kind="plan-review", project_id="OTHER_APP")

        self.assertFalse(result.valid)
        self.assertTrue(any("does not match captured project OTHER_APP" in reason for reason in result.reasons))

    def test_a_review_without_a_decision_is_not_a_decision(self) -> None:
        text = "```text\nPROJECT_ID: DEMO_APP\nREVIEW_TYPE: PLAN_APPROVAL\nPLAN_HAS_P0_P1: false\n```"
        result = parse_control_block_result(text, kind="plan-review", project_id="DEMO_APP")

        self.assertFalse(result.valid)
        self.assertIn("missing approval_decision for kind plan-review", result.reasons)

    def test_unknown_kind_is_rejected_up_front(self) -> None:
        with self.assertRaises(ValueError):
            parse_control_block_result(APPROVE, kind="vibes")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                _capture(Path(tmp), "vibes", APPROVE)

    def test_kind_agnostic_parse_keeps_old_behaviour(self) -> None:
        # no kind/project → only the enum and structure rules apply
        self.assertTrue(parse_control_block_result(APPROVE).valid)


class EscalationTests(unittest.TestCase):
    def test_quarantined_artifact_opens_a_decision_that_blocks_the_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = _capture(root, "plan", PLAN_IMPLEMENTED)

            self.assertFalse(artifact.control_block_valid)
            self.assertTrue(artifact.needs_human)
            self.assertEqual(artifact.escalated_decision, "DEMO_APP Quarantined artifact — plan BQ-DEMO_APP-001")
            open_md = (root / "human-decisions/open.md").read_text(encoding="utf-8")
            self.assertIn("## DEMO_APP Quarantined artifact — plan BQ-DEMO_APP-001", open_md)
            self.assertIn("Status: `OPEN`", open_md)
            self.assertIn("implementation_started: true", open_md)
            self.assertEqual(open_human_decision_projects(root), ["DEMO_APP"])
            self.assertIn("QUARANTINED", (root / "projects/DEMO_APP/artifacts/chatgpt/index.md").read_text(encoding="utf-8"))

    def test_human_input_required_opens_a_decision_even_when_the_block_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = _capture(root, "next-plan-request", NEEDS_HUMAN)

            self.assertTrue(artifact.control_block_valid)
            self.assertTrue(artifact.needs_human)
            self.assertEqual(open_human_decision_projects(root), ["DEMO_APP"])
            self.assertIn("Human input required", artifact.escalated_decision)

    def test_capturing_the_same_bad_response_twice_opens_one_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _capture(root, "plan", PLAN_IMPLEMENTED)
            second = _capture(root, "plan", PLAN_IMPLEMENTED)

            self.assertEqual(second.escalated_decision, "DEMO_APP Quarantined artifact — plan BQ-DEMO_APP-001")
            open_md = (root / "human-decisions/open.md").read_text(encoding="utf-8")
            self.assertEqual(open_md.count("## DEMO_APP Quarantined artifact"), 1)

    def test_no_escalate_saves_and_quarantines_without_a_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = _capture(root, "plan", PLAN_IMPLEMENTED, escalate=False)

            self.assertFalse(artifact.control_block_valid)
            self.assertIsNone(artifact.escalated_decision)
            self.assertFalse((root / "human-decisions/open.md").exists())

    def test_escalation_does_not_hide_unrelated_validation_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "arthur_loop.artifact_store.open_decision",
                side_effect=ValueError("decision storage is invalid"),
            ):
                with self.assertRaisesRegex(ValueError, "decision storage is invalid"):
                    _capture(Path(tmp), "plan", PLAN_IMPLEMENTED)

    def test_valid_trusted_artifacts_open_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = _capture(root, "plan-review", APPROVE)

            self.assertFalse(artifact.needs_human)
            self.assertIsNone(artifact.escalated_decision)
            self.assertEqual(open_human_decision_projects(root), [])


class ImplementationGateTests(unittest.TestCase):
    def test_no_go_without_an_approved_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(implementation_gate(root, "DEMO_APP").go)

            _capture(root, "plan-review", REVISE)
            result = implementation_gate(root, "DEMO_APP")
            self.assertFalse(result.go)
            self.assertTrue(any("REVISE_PLAN, not APPROVE_PLAN" in reason for reason in result.reasons))

    def test_go_after_a_valid_approval_and_no_go_once_planning_reopens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            approval = _capture(root, "plan-review", APPROVE, created_at="2026-07-02T12:00:00Z")
            result = implementation_gate(root, "DEMO_APP")
            self.assertTrue(result.go, result.reasons)
            self.assertEqual(result.approval_artifact, approval.artifact_id)

            _capture(root, "plan", PLAN_OK, created_at="2026-07-02T12:00:00Z")  # same second: order still wins
            result = implementation_gate(root, "DEMO_APP")
            self.assertFalse(result.go)
            self.assertTrue(any("planning re-opened" in reason for reason in result.reasons))

    def test_no_go_once_the_sprint_is_approved_or_a_decision_is_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _capture(root, "plan-review", APPROVE, created_at="2026-07-02T12:00:00Z")
            _capture(root, "implementation-handoff", HANDOFF, created_at="2026-07-02T12:30:00Z")
            self.assertTrue(implementation_gate(root, "DEMO_APP").go)

            _capture(root, "sprint-review", SPRINT_APPROVED, created_at="2026-07-02T13:00:00Z")
            result = implementation_gate(root, "DEMO_APP")
            self.assertFalse(result.go)
            self.assertTrue(any("closed the approved sprint" in reason for reason in result.reasons))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _capture(root, "plan-review", APPROVE)
            open_decision(root, project_id="DEMO_APP", title="Scope?", body="Pick one.")
            result = implementation_gate(root, "DEMO_APP")
            self.assertFalse(result.go)
            self.assertTrue(any("open human decision" in reason for reason in result.reasons))

    def test_implementation_handoff_without_an_approved_plan_is_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = _capture(root, "implementation-handoff", HANDOFF)

            self.assertFalse(artifact.control_block_valid)
            self.assertTrue(any(reason.startswith("implementation gate:") for reason in artifact.control_block_reasons))
            self.assertEqual(open_human_decision_projects(root), ["DEMO_APP"])
            # evidence is kept even though it is not trusted
            self.assertTrue((root / artifact.path).exists())

    def test_gated_implementation_handoff_is_trusted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _capture(root, "plan-review", APPROVE)
            artifact = _capture(root, "implementation-handoff", HANDOFF)

            self.assertTrue(artifact.control_block_valid)
            self.assertEqual(artifact.implementation_status, "COMPLETE")
            self.assertEqual(open_human_decision_projects(root), [])


if __name__ == "__main__":
    unittest.main()
