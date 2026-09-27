"""Prometheus metrics.

Each application instance owns its own ``CollectorRegistry`` instead of using
the library's global default. That keeps tests independent (building a second
app would otherwise fail with duplicate metric names) and makes it explicit
which metrics ``/metrics`` exposes.
"""

from dataclasses import dataclass, field

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    PlatformCollector,
    ProcessCollector,
)

# Buckets tuned for a fast CPU-only API: most requests should land well under 50 ms.
_LATENCY_BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)


@dataclass
class Metrics:
    """The service's metric families, bound to one registry."""

    registry: CollectorRegistry = field(default_factory=CollectorRegistry)

    def __post_init__(self) -> None:
        ProcessCollector(registry=self.registry)
        PlatformCollector(registry=self.registry)
        # Labelled by route template (e.g. /api/v1/distance), never the raw URL,
        # so arbitrary paths from scanners cannot explode label cardinality.
        self.requests = Counter(
            "http_requests_total",
            "HTTP requests handled, by method, route template and status code.",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "http_request_duration_seconds",
            "Time spent handling HTTP requests.",
            ["method", "route"],
            buckets=_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.in_flight = Gauge(
            "http_requests_in_flight",
            "HTTP requests currently being handled.",
            registry=self.registry,
        )
        self.info = Gauge(
            "app_info",
            "Build and deployment information; the value is always 1.",
            ["version", "environment"],
            registry=self.registry,
        )
        self.distance_km = Histogram(
            "distance_calculated_km",
            "Great-circle distances returned by the API, in kilometres.",
            buckets=(1, 10, 100, 500, 1_000, 5_000, 10_000, 20_050),
            registry=self.registry,
        )
