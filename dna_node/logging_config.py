"""Structured logging setup."""
from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any


class JsonFormatter(logging.Formatter):
    def __init__(self, node_id: str, run_id: str):
        super().__init__()
        self.node_id = node_id
        self.run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "node_id": self.node_id,
            "run_id": self.run_id,
            "event": getattr(record, "event", record.name),
            "msg": record.getMessage(),
        }
        for k in ("role", "chunk_id", "chunk_index", "duration_ms",
                  "matches", "mismatches", "total_bases", "error"):
            if hasattr(record, k):
                payload[k] = getattr(record, k)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(node_id: str, run_id: str, level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(node_id, run_id))
    root.addHandler(handler)


def log_event(logger: logging.Logger, level: int, event: str, msg: str = "", **extra: Any) -> None:
    logger.log(level, msg or event, extra={"event": event, **extra})
