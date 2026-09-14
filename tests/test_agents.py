from __future__ import annotations

import unittest
from unittest.mock import patch

from arthur_loop.agents import KNOWN_AGENTS, detect_agents, find_agent


def _which_map(available: dict[str, str]):
    def fake_which(binary: str):
        return available.get(binary)

    return fake_which


class AgentRegistryTests(unittest.TestCase):
    def test_find_agent_returns_registry_entry(self) -> None:
        codex = find_agent("codex")
        self.assertIsNotNone(codex)
        self.assertEqual(codex.binary, "codex")
        self.assertEqual(codex.executor_adapter, "codex")
        grok = find_agent("grok")
        self.assertEqual(grok.advisor_adapter, "grok")
        self.assertEqual(grok.version_args, ("version",))
        self.assertEqual(grok.skill_install_path(), ".arthur/integrations/grok-agent-skill")
        self.assertEqual(find_agent("claude-code").skill_install_path(), ".claude/skills/arthur-loop")
        self.assertEqual(find_agent("cursor").integration_target, "cursor")
        self.assertIsNone(find_agent("hal9000"))

    def test_registry_agents_with_adapters_reference_real_packs(self) -> None:
        # every advertised adapter id must be a valid config value
        from arthur_loop.config import KNOWN_ADVISORS, KNOWN_EXECUTORS

        for agent in KNOWN_AGENTS:
            if agent.executor_adapter:
                self.assertIn(agent.executor_adapter, KNOWN_EXECUTORS, agent.agent_id)
            if agent.advisor_adapter:
                self.assertIn(agent.advisor_adapter, KNOWN_ADVISORS, agent.agent_id)


class DetectionTests(unittest.TestCase):
    def test_detects_only_present_binaries_in_preference_order(self) -> None:
        available = {"codex": "/opt/homebrew/bin/codex", "claude": "/usr/local/bin/claude"}
        with patch("arthur_loop.agents.shutil.which", new=_which_map(available)):
            detected = detect_agents(with_versions=False)

        self.assertEqual(
            [item.agent.agent_id for item in detected], ["claude-code", "codex"]
        )
        self.assertEqual(detected[1].path, "/opt/homebrew/bin/codex")
        self.assertIsNone(detected[0].version)

    def test_detects_nothing_on_a_bare_machine(self) -> None:
        with patch("arthur_loop.agents.shutil.which", new=_which_map({})):
            self.assertEqual(detect_agents(), [])

    def test_version_probe_survives_broken_binaries(self) -> None:
        available = {"grok": "/usr/local/bin/grok"}
        with patch("arthur_loop.agents.shutil.which", new=_which_map(available)), patch(
            "arthur_loop.agents.subprocess.run", side_effect=OSError("boom")
        ):
            detected = detect_agents(with_versions=True)

        self.assertEqual(len(detected), 1)
        self.assertIsNone(detected[0].version)


if __name__ == "__main__":
    unittest.main()
