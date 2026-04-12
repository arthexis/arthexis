"""JSON log formatter for LogQL/Loki-friendly structured log output."""

from __future__ import annotations

import json
import logging
import socket
from datetime import UTC, datetime


class JSONFormatter(logging.Formatter):
    """Serialize log records as stable JSON objects."""

    _hostname = socket.gethostname()

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "app": getattr(record, "app", ""),
            "hostname": getattr(record, "hostname", self._hostname),
            "process": record.process,
            "thread": record.thread,
            "request_id": getattr(record, "request_id", ""),
            "node_id": getattr(record, "node_id", ""),
            "charger_id": getattr(record, "charger_id", ""),
            "session_id": getattr(record, "session_id", ""),
        }

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
