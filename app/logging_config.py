"""Structured logging.

In a cluster, logs are collected from stdout by the node's log agent, so each
record is written as one JSON object per line. That keeps fields such as the
request ID queryable without regex parsing. ``APP_LOG_FORMAT=text`` switches to
a human-friendly format for local development.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from app.config import LogFormat, Settings

# Set per request by the middleware, so any log call made while handling that
# request is tagged with its ID without threading it through every function.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes every LogRecord has; anything else was passed via ``extra=``.
# Uvicorn also attaches an ANSI-coloured copy of its message, which is noise in JSON.
_STANDARD_ATTRS = frozenset(vars(logging.makeLogRecord({}))) | {
    "message",
    "asctime",
    "taskName",
    "color_message",
}


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def __init__(self, static_fields: dict[str, str] | None = None) -> None:
        super().__init__()
        self._static = static_fields or {}

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **self._static,
        }
        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(settings: Settings) -> None:
    """Install a single stdout handler on the root logger.

    Uvicorn's own loggers are routed through the same handler so that server
    messages and application messages share one format. Uvicorn's access log
    is silenced because the request middleware emits a richer one.
    """
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_format is LogFormat.JSON:
        handler.setFormatter(
            JsonFormatter(
                {
                    "service": settings.name,
                    "version": settings.version,
                    "environment": settings.environment.value,
                    "instance": settings.instance,
                }
            )
        )
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)

    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
