"""One UTC timestamp format for every record the application writes: `YYYY-MM-DDTHH:MM:SSZ`."""

from __future__ import annotations

import time


def utc_now_iso(ts: float | None = None) -> str:
    """Return the given epoch seconds (default: now) as `YYYY-MM-DDTHH:MM:SSZ`."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
