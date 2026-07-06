from __future__ import annotations

import re
from dataclasses import asdict, dataclass


PERCENT_RE = re.compile(
    r"^(?P<provider>Codex|Claude|Gemini)\s+(?P<label>[^:]+):\s+"
    r"(?P<percent>\d+(?:\.\d+)?)%\s+(?P<kind>left|remaining|in reserve)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class UsageMetric:
    """One parsed percentage from `codexbar usage` output."""

    provider: str
    label: str
    percent: float
    kind: str
    status: str
    can_start_new_work: bool

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable metric."""

        return asdict(self)


@dataclass(frozen=True)
class UsageSnapshot:
    """Parsed quota state plus warnings from a `codexbar usage` run."""

    metrics: list[UsageMetric]
    warnings: list[str]
    returncode: int | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable snapshot."""

        return {
            "metrics": [metric.to_dict() for metric in self.metrics],
            "warnings": self.warnings,
            "returncode": self.returncode,
        }


def classify_remaining(percent: float, reserve_percent: float = 5.0) -> tuple[str, bool]:
    """Classify whether a quota percentage can start new work."""

    if percent < reserve_percent:
        return "RED", False
    if percent <= reserve_percent:
        return "YELLOW", False
    return "GREEN", True


def parse_codexbar_usage(
    output: str,
    *,
    returncode: int | None = None,
    reserve_percent: float = 5.0,
) -> UsageSnapshot:
    """Parse useful quota metrics even when `codexbar usage` exits nonzero."""

    metrics: list[UsageMetric] = []
    warnings: list[str] = []

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        match = PERCENT_RE.match(line)
        if match:
            provider = match.group("provider").title()
            label = " ".join(match.group("label").split()).lower()
            percent = float(match.group("percent"))
            status, can_start = classify_remaining(percent, reserve_percent)
            metrics.append(
                UsageMetric(
                    provider=provider,
                    label=label,
                    percent=percent,
                    kind=match.group("kind").lower(),
                    status=status,
                    can_start_new_work=can_start,
                )
            )
            continue

        lowered = line.lower()
        if "error" in lowered or "not logged in" in lowered or "auth" in lowered:
            warnings.append(line)

    if returncode not in (None, 0) and metrics:
        warnings.append(
            f"codexbar exited {returncode}, but parsed {len(metrics)} useful quota metric(s)"
        )
    elif returncode not in (None, 0):
        warnings.append(f"codexbar exited {returncode} and no quota metrics were parsed")

    return UsageSnapshot(metrics=metrics, warnings=warnings, returncode=returncode)

