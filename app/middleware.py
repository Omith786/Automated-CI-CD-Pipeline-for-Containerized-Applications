"""Request middleware: request IDs, access logging and HTTP metrics.

Written as plain ASGI middleware rather than Starlette's ``BaseHTTPMiddleware``,
which wraps responses in an extra task and is known to interfere with context
variables and streaming. Plain ASGI keeps the request ID context var visible to
the route handler.
"""

import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging_config import request_id_var
from app.metrics import Metrics

logger = logging.getLogger("app.access")

REQUEST_ID_HEADER = "x-request-id"
# Accept a caller's ID only if it looks sane, so a client cannot inject
# arbitrary text into our logs.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
# Probe and scrape endpoints are hit every few seconds; logging them at INFO
# would drown out real traffic.
_QUIET_PATHS = frozenset({"/healthz", "/readyz", "/metrics"})


class RequestContextMiddleware:
    """Tag each request with an ID, time it, count it and log it."""

    def __init__(self, app: ASGIApp, metrics: Metrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        status_code = 500
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message["headers"] = headers
            await send(message)

        self.metrics.in_flight.inc()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            self.metrics.in_flight.dec()
            # The router stores the matched route in the scope; its template is
            # a bounded label value, unlike the raw path.
            route = scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            method = scope["method"]
            self.metrics.requests.labels(method, template, str(status_code)).inc()
            self.metrics.latency.labels(method, template).observe(elapsed)

            path = scope["path"]
            level = logging.DEBUG if path in _QUIET_PATHS and status_code < 400 else logging.INFO
            logger.log(
                level,
                "%s %s %d",
                method,
                path,
                status_code,
                extra={
                    "method": method,
                    "path": path,
                    "route": template,
                    "status": status_code,
                    "duration_ms": round(elapsed * 1000, 2),
                },
            )
            request_id_var.reset(token)


def _incoming_request_id(scope: Scope) -> str | None:
    """Return the caller-supplied request ID if present and well formed."""
    for name, value in scope.get("headers", []):
        if name == REQUEST_ID_HEADER.encode():
            candidate: str = value.decode("latin-1")
            return candidate if _VALID_REQUEST_ID.match(candidate) else None
    return None
