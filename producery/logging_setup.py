"""Structured JSON logs to stdout (12-factor). One line per event; containers and k8s collectors read stdout."""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects with a UTC timestamp and selected extra fields."""

    def format(self, record: logging.LogRecord) -> str:
        """Render one record as a JSON line.

        Args:
            record: The log record to format.

        Returns:
            JSON with ts, level, logger, msg, any of thread_id/agent/trace_id/job/tool set on the record, and
            the last 2000 characters of the exception text when ``exc_info`` is present.
        """
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("thread_id", "agent", "trace_id", "job", "tool"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)[-2000:]
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(service: str, level: str = "INFO") -> logging.Logger:
    """Configure the root logger to emit JSON lines to stdout and return the service logger.

    Replaces any existing root handlers and raises the httpx, httpcore, uvicorn.access and langsmith.client
    loggers to WARNING.

    Args:
        service: Name of the logger returned to the caller.
        level: Root log level name.

    Returns:
        The logger named after ``service``.
    """
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "uvicorn.access", "langsmith.client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logging.getLogger(service)
