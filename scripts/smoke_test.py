#!/usr/bin/env python3
"""Post-deployment smoke tests for the Distance API.

Uses only the standard library so it runs anywhere Python does (a CI runner,
a laptop) without installing the service's dependencies. It checks that the
deployed release is the one we expect, that it is healthy, and that the main
endpoints return correct answers, then exits non-zero if anything is wrong.
That exit code is what triggers an automatic rollback in the pipeline.

Usage:
    python scripts/smoke_test.py --base-url http://127.0.0.1:8080 \
        --expected-version 1.2.3 --expected-environment staging
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# London to New York; the spherical great-circle distance is about 5,570 km.
_LONDON = (51.5074, -0.1278)
_NEW_YORK = (40.7128, -74.0060)
_LONDON_NEW_YORK_KM = 5570.0
_TOLERANCE_KM = 10.0


class SmokeTestError(Exception):
    """A smoke check failed."""


@dataclass
class HttpResult:
    """A minimal HTTP response."""

    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        """Decode the body as JSON."""
        return json.loads(self.body)


class Client:
    """Tiny HTTP client around ``urllib`` that never raises on HTTP errors."""

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, payload: object | None = None) -> HttpResult:
        """Send a request and return the response, whatever its status code."""
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(  # noqa: S310 - URL is supplied by the operator
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "X-Request-ID": "smoke-test"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                return HttpResult(resp.status, _lower(dict(resp.headers)), resp.read())
        except urllib.error.HTTPError as err:
            # The error doubles as the response; close it or its socket leaks.
            with err:
                return HttpResult(err.code, _lower(dict(err.headers)), err.read())


def _lower(headers: dict[str, str]) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeTestError(message)


def wait_until_ready(client: Client, deadline_s: float, interval_s: float = 1.0) -> None:
    """Poll ``/readyz`` until it returns 200 or the deadline passes.

    A freshly rolled-out pod can briefly refuse connections while a port-forward
    or load balancer catches up, so connection errors are retried too.
    """
    deadline = time.monotonic() + deadline_s
    last_error = "no attempt made"
    while time.monotonic() < deadline:
        try:
            result = client.request("GET", "/readyz")
            if result.status == 200:
                return
            last_error = f"/readyz returned {result.status}"
        except OSError as err:  # connection refused, reset, timeout
            last_error = str(err)
        time.sleep(interval_s)
    raise SmokeTestError(f"service not ready after {deadline_s:.0f}s: {last_error}")


def check_liveness(client: Client, _: argparse.Namespace) -> str:
    result = client.request("GET", "/healthz")
    _expect(result.status == 200, f"/healthz returned {result.status}")
    _expect(result.json().get("status") == "ok", "/healthz did not report ok")
    return "liveness probe ok"


def check_release(client: Client, args: argparse.Namespace) -> str:
    result = client.request("GET", "/")
    _expect(result.status == 200, f"/ returned {result.status}")
    info = result.json()
    if args.expected_version:
        _expect(
            info.get("version") == args.expected_version,
            f"expected version {args.expected_version!r}, got {info.get('version')!r}",
        )
    if args.expected_environment:
        _expect(
            info.get("environment") == args.expected_environment,
            f"expected environment {args.expected_environment!r}, got {info.get('environment')!r}",
        )
    _expect(result.headers.get("x-request-id") == "smoke-test", "request ID was not echoed")
    return f"release {info.get('version')} ({info.get('environment')}) on {info.get('instance')}"


def check_distance(client: Client, _: argparse.Namespace) -> str:
    query = (
        f"/api/v1/distance?from_lat={_LONDON[0]}&from_lon={_LONDON[1]}"
        f"&to_lat={_NEW_YORK[0]}&to_lon={_NEW_YORK[1]}"
    )
    result = client.request("GET", query)
    _expect(result.status == 200, f"distance returned {result.status}")
    km = float(result.json()["distance"])
    _expect(
        abs(km - _LONDON_NEW_YORK_KM) <= _TOLERANCE_KM,
        f"London to New York was {km} km, expected about {_LONDON_NEW_YORK_KM}",
    )
    return f"distance endpoint correct ({km:.0f} km)"


def check_route(client: Client, _: argparse.Namespace) -> str:
    waypoints = [
        {"lat": _LONDON[0], "lon": _LONDON[1]},
        {"lat": 53.4808, "lon": -2.2426},  # Manchester
        {"lat": 55.9533, "lon": -3.1883},  # Edinburgh
    ]
    result = client.request("POST", "/api/v1/route", {"waypoints": waypoints, "unit": "mi"})
    _expect(result.status == 200, f"route returned {result.status}")
    body = result.json()
    _expect(len(body["legs"]) == 2, f"expected 2 legs, got {len(body['legs'])}")
    _expect(
        abs(sum(body["legs"]) - body["total_distance"]) < 0.01,
        "route legs do not add up to the total",
    )
    return f"route endpoint correct ({body['total_distance']:.0f} mi)"


def check_validation(client: Client, _: argparse.Namespace) -> str:
    result = client.request("GET", "/api/v1/distance?from_lat=91&from_lon=0&to_lat=0&to_lon=0")
    _expect(result.status == 422, f"invalid latitude returned {result.status}, expected 422")
    return "input validation rejects bad coordinates"


def check_metrics(client: Client, _: argparse.Namespace) -> str:
    result = client.request("GET", "/metrics")
    _expect(result.status == 200, f"/metrics returned {result.status}")
    text = result.body.decode()
    for name in ("app_info{", "http_requests_total{", "http_request_duration_seconds_bucket{"):
        _expect(name in text, f"metric {name.rstrip('{')} missing from /metrics")
    return "prometheus metrics exposed"


CHECKS: list[Callable[[Client, argparse.Namespace], str]] = [
    check_liveness,
    check_release,
    check_distance,
    check_route,
    check_validation,
    check_metrics,
]


# Health and identity only. Used to confirm a release restored by rollback (or
# recreated in a fresh cluster) is up: that release passed the full suite when
# it was first deployed, and newer functional checks may not apply to it.
BASIC_CHECKS = CHECKS[:2]


def run(args: argparse.Namespace) -> int:
    """Run the checks, print a report and return a process exit code."""
    checks = BASIC_CHECKS if args.basic else CHECKS
    client = Client(args.base_url, timeout=args.timeout)
    try:
        wait_until_ready(client, args.wait)
    except SmokeTestError as err:
        print(f"FAIL readiness: {err}")
        return 1
    print("PASS readiness probe ok")

    failures = 0
    for check in checks:
        name = check.__name__.removeprefix("check_")
        try:
            print(f"PASS {check(client, args)}")
        except (SmokeTestError, OSError, ValueError, KeyError) as err:
            failures += 1
            print(f"FAIL {name}: {err}")
    print(f"{len(checks) + 1 - failures}/{len(checks) + 1} smoke checks passed")
    return 1 if failures else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Smoke-test a deployed Distance API.")
    parser.add_argument("--base-url", required=True, help="e.g. http://127.0.0.1:8080")
    parser.add_argument("--expected-version", default="", help="fail unless / reports this")
    parser.add_argument("--expected-environment", default="", help="fail unless / reports this")
    parser.add_argument("--wait", type=float, default=60.0, help="seconds to wait for /readyz")
    parser.add_argument("--timeout", type=float, default=5.0, help="per-request timeout")
    parser.add_argument("--basic", action="store_true", help="health and identity checks only")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run(parse_args()))
