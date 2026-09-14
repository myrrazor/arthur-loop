from __future__ import annotations

import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AgentCLI:
    """One coding-agent CLI Arthur Loop knows how to work with."""

    agent_id: str
    name: str
    binary: str
    executor_adapter: str | None
    advisor_adapter: str | None
    # where this agent reads project instructions from, so the wizard can seed it
    instructions_file: str | None
    skills_dir: str | None
    launch_hint: str | None
    version_args: tuple[str, ...] = ("--version",)
    # where this client actually loads project skills / slash commands / MCP
    integration_target: str | None = None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

    def skill_install_path(self) -> str | None:
        """Directory that should contain SKILL.md for this client.

        Claude/Codex/Cursor use a parent skills dir plus `arthur-loop/`.
        Grok Build scans `./.grok/skills/` (and `~/.grok/skills/`); the skill
        folder is `.grok/skills/arthur-loop`. Paths ending in `-skill` are
        already the skill folder (no extra nest).
        """

        if not self.skills_dir:
            return None
        name = Path(self.skills_dir).name
        if name.endswith("-skill") or name == "arthur-loop":
            return self.skills_dir
        return str(Path(self.skills_dir) / "arthur-loop")


@dataclass(frozen=True)
class DetectedAgent:
    """A registry entry that actually exists on this machine."""

    agent: AgentCLI
    path: str
    version: str | None

    def to_record(self) -> dict[str, Any]:
        return {**self.agent.to_record(), "path": self.path, "version": self.version}


# ordering doubles as the default "main agent" preference when several are found
KNOWN_AGENTS: tuple[AgentCLI, ...] = (
    AgentCLI(
        agent_id="claude-code",
        name="Claude Code",
        binary="claude",
        executor_adapter="claude-code",
        advisor_adapter="claude-code",
        instructions_file=None,
        skills_dir=".claude/skills",
        launch_hint='claude "$(cat agent-setup/KICKOFF.md)"',
        integration_target="claude",
    ),
    AgentCLI(
        agent_id="codex",
        name="Codex CLI",
        binary="codex",
        executor_adapter="codex",
        advisor_adapter="codex",
        instructions_file="AGENTS.md",
        skills_dir=".codex/skills",
        launch_hint='codex "$(cat agent-setup/KICKOFF.md)"',
        integration_target="codex",
    ),
    AgentCLI(
        agent_id="cursor",
        name="Cursor",
        binary="cursor",
        executor_adapter=None,
        advisor_adapter=None,
        instructions_file="AGENTS.md",
        skills_dir=".cursor/skills",
        launch_hint=None,
        integration_target="cursor",
    ),
    AgentCLI(
        agent_id="gemini",
        name="Gemini CLI",
        binary="gemini",
        executor_adapter=None,
        advisor_adapter=None,
        instructions_file="GEMINI.md",
        skills_dir=None,
        launch_hint=None,
    ),
    AgentCLI(
        agent_id="grok",
        name="Grok Build",
        binary="grok",
        executor_adapter="grok",
        advisor_adapter="grok",
        instructions_file="AGENTS.md",
        skills_dir=".grok/skills",
        launch_hint='grok -p --always-approve "$(cat agent-setup/KICKOFF.md)"',
        version_args=("version",),
        integration_target="grok",
    ),
    AgentCLI(
        agent_id="goose",
        name="Goose",
        binary="goose",
        executor_adapter=None,
        advisor_adapter=None,
        instructions_file=".goosehints",
        skills_dir=None,
        launch_hint=None,
    ),
)


def find_agent(agent_id: str) -> AgentCLI | None:
    """Look up a registry entry by id."""

    for agent in KNOWN_AGENTS:
        if agent.agent_id == agent_id:
            return agent
    return None


def probe_version(
    binary_path: str,
    timeout_seconds: float = 6.0,
    version_args: tuple[str, ...] = ("--version",),
) -> str | None:
    """Best-effort version probe; agents that hang or error just report None."""

    try:
        proc = subprocess.run(
            [binary_path, *version_args],
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (proc.stdout or proc.stderr).strip()
    return output.splitlines()[0][:80] if output else None


def detect_agents(with_versions: bool = True) -> list[DetectedAgent]:
    """Return the known agent CLIs present on PATH, in registry preference order."""

    detected: list[DetectedAgent] = []
    for agent in KNOWN_AGENTS:
        path = shutil.which(agent.binary)
        if not path:
            continue
        version = probe_version(path, version_args=agent.version_args) if with_versions else None
        detected.append(DetectedAgent(agent=agent, path=path, version=version))
    return detected
