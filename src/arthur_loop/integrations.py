"""Install Arthur Loop where coding agents actually load skills and MCP."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from arthur_loop.agents import detect_agents


MANAGED_BEGIN = "<!-- arthur-loop:begin -->"
MANAGED_END = "<!-- arthur-loop:end -->"

TARGETS = ("claude", "codex", "cursor", "grok", "generic")

# Where each client actually loads project skills / slash commands / MCP.
TARGET_SPEC: dict[str, dict[str, Any]] = {
    "claude": {
        "display_name": "Claude Code",
        "binary": "claude",
        "instruction_file": "CLAUDE.md",
        "skill_dir": ".claude/skills/arthur-loop",
        "command_dir": ".claude/commands",
        "mcp_path": ".mcp.json",
        "mcp_format": "json",
        "tool_name_style": "dotted",
    },
    "codex": {
        "display_name": "Codex",
        "binary": "codex",
        "instruction_file": "AGENTS.md",
        "skill_dir": ".codex/skills/arthur-loop",
        "command_dir": None,
        "mcp_path": ".codex/config.toml",
        "mcp_format": "toml",
        "tool_name_style": "dotted",
        "markers": ("<!-- arthur-loop:codex:begin -->", "<!-- arthur-loop:codex:end -->"),
    },
    "cursor": {
        "display_name": "Cursor",
        "binary": "cursor",
        "instruction_file": "AGENTS.md",
        "skill_dir": ".cursor/skills/arthur-loop",
        # legacy slash path still loads; current Cursor also surfaces the skill as /arthur-loop
        "command_dir": ".cursor/commands",
        "mcp_path": ".cursor/mcp.json",
        "mcp_format": "json",
        "tool_name_style": "dotted",
        "markers": ("<!-- arthur-loop:cursor:begin -->", "<!-- arthur-loop:cursor:end -->"),
    },
    "grok": {
        "display_name": "Grok Build",
        "binary": "grok",
        "instruction_file": "AGENTS.md",
        # Grok Build walks from cwd to the repo root looking for ./.grok/skills/
        # (and ~/.grok/skills/). It does not scan .arthur/integrations/.
        "skill_dir": ".grok/skills/arthur-loop",
        "command_dir": None,
        "mcp_path": ".grok/config.toml",
        "mcp_format": "toml",
        "tool_name_style": "portable",
        "markers": ("<!-- arthur-loop:grok:begin -->", "<!-- arthur-loop:grok:end -->"),
        "native_mcp": True,
    },
    "generic": {
        "display_name": "Generic agent",
        "binary": None,
        "instruction_file": "AGENTS.md",
        "skill_dir": ".arthur/integrations/generic-agent-skill",
        "command_dir": None,
        "mcp_path": ".arthur/integrations/arthur-mcp.json",
        "mcp_format": "json",
        "tool_name_style": "dotted",
        "markers": ("<!-- arthur-loop:generic:begin -->", "<!-- arthur-loop:generic:end -->"),
    },
}


@dataclass
class Detection:
    target: str
    found: bool
    reasons: list[str] = field(default_factory=list)

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InstallResult:
    target: str
    status: str
    written: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def parse_targets(raw: str | None) -> list[str]:
    if not raw or not raw.strip():
        return []
    parts = [part.strip().lower() for part in re.split(r"[,\s]+", raw) if part.strip()]
    unknown = [part for part in parts if part not in TARGETS]
    if unknown:
        raise ValueError(f"unknown integration target(s): {', '.join(unknown)} (valid: {', '.join(TARGETS)})")
    seen: list[str] = []
    for part in parts:
        if part not in seen:
            seen.append(part)
    return seen


def detect_targets(workspace: Path, *, home: Path | None = None) -> list[Detection]:
    """Read-only: PATH binaries and workspace markers.

    A home config dir (`~/.cursor`, `~/.grok`) is not a detection signal — those
    exist on machines that never run the CLI (this was a false-positive).
    """

    # `home` is accepted so callers can still pass it; it is not a found-reason.
    _ = home or Path(os.path.expanduser("~"))
    out: list[Detection] = []
    for target in TARGETS:
        spec = TARGET_SPEC[target]
        reasons: list[str] = []
        binary = spec.get("binary")
        if binary:
            path = shutil.which(binary)
            if path:
                reasons.append(f"binary on PATH ({path})")
        rel_skill = spec["skill_dir"].split("/")[0]
        if (workspace / rel_skill).is_dir():
            reasons.append(f"workspace {rel_skill}/")
        instruction = spec["instruction_file"]
        if instruction and (workspace / instruction).is_file() and target in {"claude"}:
            reasons.append(f"workspace {instruction}")
        if target == "generic":
            out.append(Detection(target=target, found=False, reasons=[]))
            continue
        out.append(Detection(target=target, found=bool(reasons), reasons=reasons))
    return out


def detected_target_ids(workspace: Path, *, home: Path | None = None) -> list[str]:
    return [item.target for item in detect_targets(workspace, home=home) if item.found]


def arthur_argv(tool_name_style: str = "dotted") -> list[str]:
    args = ["mcp", "serve"]
    if tool_name_style == "portable":
        args.extend(["--tool-name-style", "portable"])
    return args


def _skill_body() -> str:
    return (resources.files("arthur_loop") / "seed" / "skill" / "SKILL.md").read_text(encoding="utf-8")


def _command_body() -> str:
    path = resources.files("arthur_loop") / "seed" / "commands" / "arthur-loop.md"
    return path.read_text(encoding="utf-8")


def _instruction_block(target: str) -> str:
    spec = TARGET_SPEC[target]
    style = spec["tool_name_style"]
    mcp_hint = (
        "MCP tools use portable names (`arthur_status`, `arthur_loop_create`) because "
        "Grok skips dotted tool names."
        if style == "portable"
        else "MCP tools use dotted names (`arthur.status`, `arthur.loop.create`)."
    )
    return (
        f"## Arthur Loop\n"
        f"\n"
        f"This directory is (or contains) an Arthur Loop instance. You are a role in the loop.\n"
        f"\n"
        f"1. Read `{spec['skill_dir']}/SKILL.md`.\n"
        f"2. Use `/arthur-loop` (or the skill) to pick advisor / executor / tracker and create a loop.\n"
        f"3. Drive the loop through MCP or the `arthur` CLI: status, follow, queue claim/submit, capture, gate, decision.\n"
        f"4. Never hand-edit `queue/*.jsonl`. Never claim, submit, poll, complete, or fail a job on a project paused by an open human decision.\n"
        f"\n"
        f"{mcp_hint} "
        + (
            "Install runs `grok mcp add --scope project arthur-loop -- arthur mcp serve --tool-name-style portable` "
            "when `grok` is on PATH so the server is trusted. Then `grok --trust` this folder "
            "(untrusted folder = project MCP does not spawn). Prove with `grok mcp list` / "
            "`grok --always-approve -p`.\n"
            if target == "grok"
            else "A written MCP entry still needs a client restart before tools appear.\n"
        )
        + "There is no drag-drop graph composer. `arthur loop create` and the web wizard create a project "
        "and the first queue job. `arthur follow` drives claim → capture → gate.\n"
    )


def _replace_managed_block(text: str, body: str, begin: str, end: str) -> str:
    block = f"{begin}\n{body.rstrip()}\n{end}\n"
    if begin in text and end in text:
        pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)
        return pattern.sub(block, text, count=1)
    stripped = text.rstrip()
    if not stripped:
        return block
    return stripped + "\n\n" + block


def _write_text(path: Path, body: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == body:
        return "none"
    created = not path.exists()
    path.write_text(body, encoding="utf-8")
    return "create" if created else "update"


def _write_instruction(path: Path, body: str, begin: str, end: str, *, force: bool) -> str:
    """Refresh only the managed marker block. `--force` does not wipe house rules."""

    del force  # kept for call-site compatibility; never replace the whole file
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return _write_text(path, f"{begin}\n{body.rstrip()}\n{end}\n")
    current = path.read_text(encoding="utf-8")
    updated = _replace_managed_block(current, body, begin, end)
    return _write_text(path, updated)


def _mcp_entry(tool_name_style: str) -> dict[str, Any]:
    # repository-carried: command name only, no absolute home paths
    return {
        "command": "arthur",
        "args": arthur_argv(tool_name_style),
    }


def _merge_json_mcp(path: Path, server_name: str, entry: dict[str, Any]) -> str:
    data: dict[str, Any] = {}
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{path} is not a JSON object")
        data = raw
    servers = data.get("mcpServers")
    if servers is None:
        data["mcpServers"] = {server_name: entry}
    elif isinstance(servers, dict):
        if servers.get(server_name) == entry:
            return "none"
        servers[server_name] = entry
    else:
        raise ValueError(f"{path} mcpServers must be an object")
    return _write_text(path, json.dumps(data, indent=2) + "\n")


def _upsert_toml_table(path: Path, table: str, mapping: dict[str, Any]) -> str:
    """Replace or append a single TOML table. Not a general TOML parser."""

    lines = [
        f"[{table}]",
        f'command = {json.dumps(mapping["command"])}',
        f"args = {json.dumps(mapping['args'])}",
        "",
    ]
    block = "\n".join(lines)
    if not path.exists():
        return _write_text(path, block)
    current = path.read_text(encoding="utf-8")
    header = f"[{table}]"
    if header in current:
        pattern = re.compile(re.escape(header) + r"(?:\n(?!\[).*)*", re.MULTILINE)
        updated = pattern.sub(block.rstrip(), current, count=1)
        if not updated.endswith("\n"):
            updated += "\n"
        return _write_text(path, updated)
    sep = "" if current.endswith("\n") else "\n"
    return _write_text(path, current + sep + "\n" + block)


def _note_change(result: InstallResult, rel: str, change: str) -> None:
    if change == "create":
        result.written.append(rel)
    elif change == "update":
        result.updated.append(rel)


def install_target(
    root: Path,
    target: str,
    *,
    force: bool = False,
    include_mcp: bool = True,
    trust_folder: bool = True,
) -> InstallResult:
    """Write skill, slash/command, instruction block, and MCP config for one client."""

    if target not in TARGET_SPEC:
        raise ValueError(f"unknown integration target {target!r}")
    spec = TARGET_SPEC[target]
    begin, end = spec.get("markers") or (MANAGED_BEGIN, MANAGED_END)
    result = InstallResult(target=target, status="written")

    skill_dir = root / spec["skill_dir"]
    skill_change = _write_text(skill_dir / "SKILL.md", _skill_body())
    _note_change(result, f"{spec['skill_dir']}/SKILL.md", skill_change)
    for name in ("workflow.md", "control-blocks.md", "prompt-placeholders.md"):
        src = resources.files("arthur_loop") / "seed" / "skill" / "references" / name
        dest = skill_dir / "references" / name
        _note_change(result, dest.relative_to(root).as_posix(), _write_text(dest, src.read_text(encoding="utf-8")))

    command_body = _command_body()
    if spec["command_dir"]:
        cmd_path = root / spec["command_dir"] / "arthur-loop.md"
        _note_change(result, cmd_path.relative_to(root).as_posix(), _write_text(cmd_path, command_body))
    else:
        cmd_path = skill_dir / "commands" / "arthur-loop.md"
        _note_change(result, cmd_path.relative_to(root).as_posix(), _write_text(cmd_path, command_body))

    instruction = root / spec["instruction_file"]
    _note_change(
        result,
        spec["instruction_file"],
        _write_instruction(instruction, _instruction_block(target), begin, end, force=force),
    )

    if include_mcp:
        entry = _mcp_entry(spec["tool_name_style"])
        mcp_path = root / spec["mcp_path"]
        if spec["mcp_format"] == "toml":
            change = _upsert_toml_table(mcp_path, "mcp_servers.arthur-loop", entry)
        else:
            change = _merge_json_mcp(mcp_path, "arthur-loop", entry)
        _note_change(result, spec["mcp_path"], change)
        if spec.get("native_mcp"):
            from arthur_loop.grok_client import grant_folder_trust, register_mcp

            native = register_mcp(root)
            result.notes.append(native.get("note") or json.dumps(native))
            if native.get("trusted"):
                result.status = "trusted"
                result.notes.append("Grok MCP catalog is registered via grok mcp add (not just a written toml).")
            else:
                result.notes.append(
                    "MCP toml written as fallback. Written ≠ trusted until `grok mcp add` succeeds."
                )
            result.notes.append(
                "Folder trust is a second gate: an untrusted Grok workspace will not spawn "
                "project MCP (`arthur_status` stays disconnected). Run `grok --trust` from this folder."
            )
            if trust_folder:
                folder = grant_folder_trust(root)
                result.notes.append(folder.get("note") or json.dumps(folder))
                if folder.get("trusted_folder") and result.status == "trusted":
                    result.status = "trusted"
        else:
            result.notes.append(
                f"MCP entry written to {spec['mcp_path']} (command=arthur {' '.join(entry['args'])}). "
                "Restart the client before tools appear."
            )
        if spec["tool_name_style"] == "portable":
            result.notes.append("Grok registration uses --tool-name-style portable (`arthur_status`).")
    else:
        result.notes.append("MCP registration skipped (--no-mcp).")

    if spec["binary"] and not shutil.which(spec["binary"]):
        result.notes.append(
            f"{spec['display_name']} CLI ({spec['binary']}) is not on PATH; files were still written."
        )
    if not result.written and not result.updated:
        result.status = "unchanged"
    return result


def install_targets(
    root: Path,
    targets: list[str],
    *,
    force: bool = False,
    include_mcp: bool = True,
    trust_folder: bool = True,
) -> list[InstallResult]:
    if not targets:
        raise ValueError("no integration targets given")
    return [
        install_target(
            root, target, force=force, include_mcp=include_mcp, trust_folder=trust_folder
        )
        for target in targets
    ]


def default_install_targets(root: Path) -> list[str]:
    """Detected clients, plus any agent CLI Arthur already knows about."""

    found = set(detected_target_ids(root))
    for item in detect_agents(with_versions=False):
        target = item.agent.integration_target
        if target:
            found.add(target)
    # preserve TARGETS order
    return [target for target in TARGETS if target in found]


def integration_status(root: Path) -> list[dict[str, Any]]:
    rows = []
    for target in TARGETS:
        spec = TARGET_SPEC[target]
        skill = root / spec["skill_dir"] / "SKILL.md"
        mcp = root / spec["mcp_path"]
        state = "written" if skill.is_file() or mcp.is_file() else "missing"
        note = "written — restart the client and check its MCP UI"
        extra: dict[str, Any] = {}
        if target == "grok":
            from arthur_loop.grok_client import native_matches, read_stamp

            stamp = read_stamp(root)
            trusted = bool(stamp and stamp.get("trusted"))
            extra["trusted"] = trusted
            extra["config_matches"] = native_matches(root)
            if trusted:
                state = "trusted"
                note = (
                    "grok mcp add succeeded; prove with grok --trust && grok mcp list / "
                    "grok --always-approve -p"
                )
            elif skill.is_file():
                note = (
                    "skill is at .grok/skills/arthur-loop (the path Grok scans). "
                    "MCP toml is fallback until grok mcp add marks it trusted."
                )
        rows.append(
            {
                "target": target,
                "display_name": spec["display_name"],
                "skill_present": skill.is_file(),
                "mcp_present": mcp.is_file(),
                "skill_path": spec["skill_dir"] + "/SKILL.md",
                "mcp_path": spec["mcp_path"],
                "state": state,
                "note": note,
                **extra,
            }
        )
    return rows


def probe_target(root: Path, target: str, *, live: bool = False) -> dict[str, Any]:
    if target != "grok":
        raise ValueError(
            "probe currently covers grok (live grok --always-approve -p / mcp list). "
            "Other clients: restart and check MCP UI."
        )
    from arthur_loop.grok_client import probe

    return probe(root, live=live)
