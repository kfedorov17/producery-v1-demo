"""Trace-delivery detection.

The LangSmith SDK ships runs from a background queue; when ingestion fails (rate limit on the free tier, network,
5xx) it logs a warning and drops the batch. Nothing else notices: the audit row already carries a trace id that
will never exist. This module turns those warnings into a counter the application can report — `/ready` shows it,
online scoring alerts on it — so "track agent traces" is verified, not assumed.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from producery.timeutil import utc_now_iso

_INGEST_MARKERS = ("Failed to multipart ingest", "Failed to batch ingest", "Failed to send compressed")


class TraceDeliveryMonitor(logging.Handler):
    """Counts the SDK's ingest-failure warnings. Attached once to the `langsmith.client` logger."""

    def __init__(self) -> None:
        """Create the handler at WARNING level with no failures recorded."""
        super().__init__(level=logging.WARNING)
        self._lock = threading.Lock()
        self.ingest_failures = 0
        self.last_failure_at: float | None = None
        self.last_error: str | None = None

    def emit(self, record: logging.LogRecord) -> None:
        """Count the record when its message carries one of the SDK's ingest-failure markers.

        Args:
            record: The log record from ``langsmith.client``.
        """
        try:
            msg = record.getMessage()
        except Exception:
            return
        if not any(m in msg for m in _INGEST_MARKERS):
            return
        with self._lock:
            self.ingest_failures += 1
            self.last_failure_at = time.time()
            self.last_error = msg[:300]

    def snapshot(self) -> dict[str, Any]:
        """Return the delivery state for ``/ready``: delivered flag, failure count, last failure and error."""
        with self._lock:
            return {
                "delivered": self.ingest_failures == 0,
                "ingest_failures": self.ingest_failures,
                "last_failure_at": (utc_now_iso(self.last_failure_at) if self.last_failure_at else None),
                "last_error": self.last_error,
            }


_MONITOR: TraceDeliveryMonitor | None = None


def install() -> TraceDeliveryMonitor:
    """Idempotent: one monitor per process, attached to the SDK's client logger."""
    global _MONITOR
    if _MONITOR is None:
        _MONITOR = TraceDeliveryMonitor()
        logging.getLogger("langsmith.client").addHandler(_MONITOR)
    return _MONITOR


def monitor() -> TraceDeliveryMonitor | None:
    """Return the process-wide monitor, or None when ``install`` has not been called."""
    return _MONITOR
