from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from arthur_loop.tracker import render_command, run_action


ATLAS_CONFIG = {"tracker": {"adapter": "atlas-tasker"}}
NONE_CONFIG = {"tracker": {"adapter": "none"}}
CUSTOM_CONFIG = {
    "tracker": {
        "adapter": "command",
        "command_templates": {
            "open_decision": "mytool add --project {project} --name {title}",
        },
    }
}


class TrackerTests(unittest.TestCase):
    def test_none_adapter_skips(self) -> None:
        result = run_action(NONE_CONFIG, "open_decision", project="DEMO_APP", title="Pick one")

        self.assertEqual(result["status"], "skipped")

    def test_atlas_preset_renders_tracker_command(self) -> None:
        result = run_action(
            ATLAS_CONFIG,
            "open_decision",
            dry_run=True,
            project="DEMO_APP",
            title="Session lifetime?",
            reason="loop escalation",
        )

        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["cmd"][0], "tracker")
        self.assertIn("DEMO_APP", result["cmd"])
        self.assertIn("Session lifetime?", result["cmd"])

    def test_custom_template_keeps_spaced_values_as_one_token(self) -> None:
        cmd = render_command(
            "mytool add --project {project} --name {title}",
            {"project": "DEMO_APP", "title": "Two words"},
        )

        self.assertEqual(cmd, ["mytool", "add", "--project", "DEMO_APP", "--name", "Two words"])

    def test_missing_placeholder_is_a_clear_error(self) -> None:
        with self.assertRaises(ValueError):
            run_action(CUSTOM_CONFIG, "open_decision", dry_run=True, project="DEMO_APP")

    def test_unconfigured_action_skips_with_reason(self) -> None:
        result = run_action(CUSTOM_CONFIG, "sprint_gate", dry_run=True, project="X", title="Y")

        self.assertEqual(result["status"], "skipped")
        self.assertIn("no template", result["reason"])

    def test_live_call_runs_from_the_instance_root_not_the_cwd(self) -> None:
        config = {"tracker": {"adapter": "command", "command_templates": {"open_decision": "pwd"}}}
        with tempfile.TemporaryDirectory() as tmp:
            result = run_action(config, "open_decision", cwd=Path(tmp), project="X", title="Y")

            self.assertEqual(result["status"], "ok")
            self.assertEqual(Path(result["stdout"]).resolve(), Path(tmp).resolve())
            self.assertEqual(result["cwd"], tmp)

    def test_missing_tracker_binary_is_an_error_result_not_a_traceback(self) -> None:
        config = {"tracker": {"adapter": "command", "command_templates": {"open_decision": "definitely-not-a-binary-xyz {project}"}}}
        result = run_action(config, "open_decision", project="X", title="Y")

        self.assertEqual(result["status"], "error")
        self.assertIn("definitely-not-a-binary-xyz", result["stderr"])


if __name__ == "__main__":
    unittest.main()
