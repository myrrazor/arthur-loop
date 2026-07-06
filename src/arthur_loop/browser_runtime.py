from __future__ import annotations

import os
import platform
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


DEFAULT_HEADLESS_SHELL_REVISION = "1208"

# best-effort playwright dir names per platform; unmapped platforms degrade
# to a clean not-ready report instead of a wrong path
SHELL_SUBDIRS = {
    ("darwin", "arm64"): "chrome-headless-shell-mac-arm64",
    ("darwin", "x86_64"): "chrome-headless-shell-mac-x64",
    ("linux", "x86_64"): "chrome-headless-shell-linux64",
}


def shell_subdir() -> str | None:
    """Return the platform's headless-shell directory name, if known."""

    return SHELL_SUBDIRS.get((sys.platform, platform.machine()))


@dataclass(frozen=True)
class BrowserRuntimeReport:
    """Result of checking whether gstack browse can launch its browser runtime."""

    browse_binary: str | None
    browse_binary_exists: bool
    expected_headless_shell: str
    headless_shell_exists: bool
    ready: bool
    fix_hint: str

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable report."""

        return asdict(self)


def candidate_browse_binaries(home: Path | None = None) -> list[Path]:
    """Return browse binaries in the order Arthur Loop should try them."""

    home = home or Path.home()
    candidates: list[Path] = []
    env_path = os.environ.get("GSTACK_BROWSE")
    if env_path:
        candidates.append(Path(env_path).expanduser())

    candidates.extend(
        [
            home / ".agents/skills/gstack/browse/dist/browse",
            home / ".claude/skills/gstack/browse/dist/browse",
        ]
    )
    return candidates


def find_browse_binary(home: Path | None = None) -> Path | None:
    """Find the first executable gstack browse binary on this machine."""

    for candidate in candidate_browse_binaries(home):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def expected_headless_shell_path(
    home: Path | None = None,
    revision: str | None = None,
) -> Path | None:
    """Return the Playwright headless shell path expected by the current gstack build."""

    home = home or Path.home()
    revision = revision or os.environ.get(
        "ARTHUR_HEADLESS_SHELL_REVISION", DEFAULT_HEADLESS_SHELL_REVISION
    )
    subdir = shell_subdir()
    if subdir is None:
        return None
    cache = "Library/Caches/ms-playwright" if sys.platform == "darwin" else ".cache/ms-playwright"
    return home / cache / f"chromium_headless_shell-{revision}" / subdir / "chrome-headless-shell"


def runtime_report(
    home: Path | None = None,
    revision: str | None = None,
) -> BrowserRuntimeReport:
    """Check local browse binary and Playwright headless shell availability."""

    browse_binary = find_browse_binary(home)
    shell_path = expected_headless_shell_path(home, revision)
    browse_exists = browse_binary is not None
    if shell_path is None:
        return BrowserRuntimeReport(
            browse_binary=str(browse_binary) if browse_binary else None,
            browse_binary_exists=browse_exists,
            expected_headless_shell="unsupported-platform",
            headless_shell_exists=False,
            ready=False,
            fix_hint="no headless-shell mapping for this platform; use another browser transport",
        )
    shell_exists = shell_path.is_file() and os.access(shell_path, os.X_OK)
    ready = browse_exists and shell_exists

    if ready:
        hint = "ready"
    elif not browse_exists:
        hint = "Install or build gstack browse first."
    else:
        hint = (
            f"Install matching Playwright headless shell revision {revision}: "
            "node ~/.claude/skills/gstack/node_modules/playwright-core/cli.js "
            "install chromium-headless-shell"
        )

    return BrowserRuntimeReport(
        browse_binary=str(browse_binary) if browse_binary else None,
        browse_binary_exists=browse_exists,
        expected_headless_shell=str(shell_path),
        headless_shell_exists=shell_exists,
        ready=ready,
        fix_hint=hint,
    )
