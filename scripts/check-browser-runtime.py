#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

from arthur_loop.browser_runtime import runtime_report


def main() -> int:
    """Print browser runtime readiness as JSON."""

    report = runtime_report()
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0 if report.ready else 2


if __name__ == "__main__":
    raise SystemExit(main())

