"""Native Grok Build skill + MCP registration.

Grok Build (docs.x.ai) discovers project skills from `./.grok/skills/` (walked
to the repo root) and `~/.grok/skills/`. It does **not** scan
`.arthur/integrations/`. Dotted MCP tool names are skipped; Arthur registers
with `--tool-name-style portable` so the live tools are `arthur_status`,
`arthur_loop_create`, `arthur_follow_run`.

`grok mcp add --scope project <name> -- <command…>` writes `.grok/config.toml`
and marks the server trusted. A hand-written toml is a fallback when `grok` is
not on PATH — that is written, not connected.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from arthur_loop.queue_ledger import isoformat


SERVER_NAME = "arthur-loop"
SKILL_DIR = ".grok/skills/arthur-loop"
CONFIG_REL = ".grok/config.toml"
TRUST_STAMP = ".arthur/integrations/grok-mcp-add.json"

Runner = Callable[..., subprocess.CompletedProcess]


def mcp_serve_argv() -> list[str]:
    return ["arthur", "mcp", "serve", "--tool-name-style", "portable"]


def mcp_add_argv() -> list[str]:
    """`grok mcp add` argv after the binary — Atlas-shaped, portable tools."""

    return ["mcp", "add", "--scope", "project", SERVER_NAME, "--", *mcp_serve_argv()]


def mcp_add_command() -> list[str]:
    return ["grok", *mcp_add_argv()]


def skill_path(root: Path) -> Path:
    return root / SKILL_DIR / "SKILL.md"


def grok_binary() -> str | None:
    return shutil.which("grok")


def parse_toml_server(text: str, name: str = SERVER_NAME) -> dict[str, Any] | None:
    """Read one `[mcp_servers.<name>]` table. Not a general TOML parser."""

    header = f"[mcp_servers.{name}]"
    if header not in text:
        return None
    start = text.index(header) + len(header)
    rest = text[start:]
    end = rest.find("\n[")
    block = rest if end < 0 else rest[:end]
    command = None
    args: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("command"):
            _, _, raw = stripped.partition("=")
            command = json.loads(raw.strip())
        elif stripped.startswith("args"):
            _, _, raw = stripped.partition("=")
            parsed = json.loads(raw.strip())
            if isinstance(parsed, list):
                args = [str(item) for item in parsed]
    if command is None:
        return None
    return {"command": command, "args": args}


def native_matches(root: Path) -> bool:
    path = root / CONFIG_REL
    if not path.is_file():
        return False
    entry = parse_toml_server(path.read_text(encoding="utf-8"))
    if not entry:
        return False
    expected = mcp_serve_argv()
    return entry["command"] == expected[0] and entry["args"] == expected[1:]


def _run(
    argv: list[str],
    *,
    cwd: Path,
    runner: Runner | None = None,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess:
    run = runner or subprocess.run
    try:
        return run(argv, text=True, capture_output=True, check=False, cwd=str(cwd), timeout=timeout)
    except TypeError:
        return run(argv, text=True, capture_output=True, check=False, cwd=str(cwd))


def _write_stamp(root: Path, payload: dict[str, Any]) -> None:
    path = root / TRUST_STAMP
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_stamp(root: Path) -> dict[str, Any] | None:
    path = root / TRUST_STAMP
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def register_mcp(root: Path, *, runner: Runner | None = None) -> dict[str, Any]:
    """Run `grok mcp add` when the CLI is present. Record whether MCP is trusted."""

    binary = grok_binary()
    argv = [binary or "grok", *mcp_add_argv()]
    result: dict[str, Any] = {
        "target": "grok",
        "skill_path": SKILL_DIR + "/SKILL.md",
        "skill_present": skill_path(root).is_file(),
        "argv": argv if binary else mcp_add_command(),
        "trusted": False,
        "connected": False,
        "method": None,
    }
    if not binary:
        result["status"] = "deferred"
        result["note"] = (
            "grok CLI is not on PATH. Skill was written to .grok/skills/arthur-loop "
            "(the path Grok Build actually scans). MCP is written to .grok/config.toml "
            "as a fallback. After installing grok, run: "
            + " ".join(mcp_add_command())
            + "   then: grok mcp list && grok -p --always-approve "
            "'List arthur_* MCP tools and call arthur_status'"
        )
        return result

    if native_matches(root) and (read_stamp(root) or {}).get("trusted"):
        result.update(
            {
                "status": "already_trusted",
                "trusted": True,
                "connected": True,
                "method": "grok mcp add (existing)",
                "note": "skipping grok mcp add; .grok/config.toml already matches and a prior add succeeded",
            }
        )
        return result

    try:
        proc = _run(argv, cwd=root, runner=runner)
    except OSError as exc:
        result.update({"status": "error", "note": str(exc), "method": "grok mcp add"})
        _write_stamp(root, {**result, "at": isoformat(), "returncode": None})
        return result

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    result["returncode"] = proc.returncode
    result["stdout"] = stdout
    result["stderr"] = stderr
    result["method"] = "grok mcp add"
    if proc.returncode == 0:
        result["status"] = "trusted"
        result["trusted"] = True
        result["connected"] = True
        result["note"] = (
            "grok mcp add registered arthur-loop in this project. "
            "Prove with: grok mcp list && grok inspect --json && "
            "grok -p --always-approve 'List arthur_* tools and call arthur_status'"
        )
    else:
        result["status"] = "error"
        result["note"] = (
            f"grok mcp add failed (exit {proc.returncode}): {stderr or stdout or 'no output'}. "
            "Wrote .grok/config.toml as a fallback; that is not the same as a trusted add."
        )
    _write_stamp(root, {**result, "at": isoformat()})
    return result


def _json_from_proc(proc: subprocess.CompletedProcess) -> Any:
    raw = (proc.stdout or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def probe(root: Path, *, live: bool = False, runner: Runner | None = None) -> dict[str, Any]:
    """Read-only-ish proof: skill path, toml, grok mcp list/doctor/inspect, optional -p."""

    binary = grok_binary()
    skill = skill_path(root)
    toml_path = root / CONFIG_REL
    entry = parse_toml_server(toml_path.read_text(encoding="utf-8")) if toml_path.is_file() else None
    stamp = read_stamp(root)
    report: dict[str, Any] = {
        "skill_path": SKILL_DIR + "/SKILL.md",
        "skill_present": skill.is_file(),
        "config_path": CONFIG_REL,
        "config_matches": native_matches(root),
        "mcp_entry": entry,
        "grok_binary": binary,
        "trusted": bool(stamp and stamp.get("trusted")),
        "stamp": stamp,
        "commands": {
            "mcp_add": mcp_add_command(),
            "mcp_list": ["grok", "mcp", "list"],
            "mcp_doctor": ["grok", "mcp", "doctor", "--json"],
            "inspect": ["grok", "inspect", "--json"],
            "prompt": [
                "grok",
                "-p",
                "--always-approve",
                "List MCP tools named arthur_* then call arthur_status. "
                "If arthur_loop_create exists, say so.",
            ],
        },
        "probes": {},
    }
    if not skill.is_file():
        report["note"] = "skill missing — run arthur integrations install --targets grok from the instance root"
        return report
    if not binary:
        report["note"] = (
            "skill is on the path Grok scans, but grok is not on PATH so this machine "
            "cannot prove a live grok -p session. Install Grok Build, then run "
            "arthur integrations probe --target grok --live"
        )
        return report

    for name, argv in (
        ("mcp_list", [binary, "mcp", "list"]),
        ("mcp_doctor", [binary, "mcp", "doctor", "--json"]),
        ("inspect", [binary, "inspect", "--json"]),
    ):
        try:
            proc = _run(argv, cwd=root, runner=runner, timeout=20.0)
        except OSError as exc:
            report["probes"][name] = {"ok": False, "error": str(exc)}
            continue
        payload = _json_from_proc(proc)
        text = (proc.stdout or "") + (proc.stderr or "")
        mentions = "arthur" in text.lower() and (
            "arthur_status" in text or "arthur-loop" in text or "arthur_loop" in text
        )
        report["probes"][name] = {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "mentions_arthur": mentions,
            "payload": payload,
        }
        if mentions:
            report["connected"] = True

    if live:
        prompt = report["commands"]["prompt"][-1]
        live_argv = [binary, "-p", "--always-approve", prompt]
        env_ok = bool(os.environ.get("GROK_API_KEY") or os.environ.get("XAI_API_KEY"))
        try:
            proc = _run(live_argv, cwd=root, runner=runner, timeout=90.0)
            text = (proc.stdout or "") + (proc.stderr or "")
            report["probes"]["prompt"] = {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "mentions_arthur_status": "arthur_status" in text,
                "stdout_head": (proc.stdout or "")[:2000],
                "stderr_head": (proc.stderr or "")[:500],
                "logged_in": env_ok,
            }
            if "arthur_status" in text:
                report["connected"] = True
                report["trusted"] = True
        except OSError as exc:
            report["probes"]["prompt"] = {"ok": False, "error": str(exc), "logged_in": env_ok}

    if report.get("connected"):
        report["note"] = "Grok can see Arthur (mcp list/inspect/prompt mentioned arthur_* tools)"
    elif report["trusted"]:
        report["note"] = "grok mcp add succeeded earlier; run grok -p to prove tools in a session"
    else:
        report["note"] = "files are in place; live grok session did not confirm arthur_* tools"
    return report
