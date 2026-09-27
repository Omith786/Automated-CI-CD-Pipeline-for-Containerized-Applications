"""Tests for the Prometheus metrics endpoint and middleware instrumentation."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _sample(text: str, prefix: str) -> float:
    """Return the value of the first exposition line starting with ``prefix``."""
    for line in text.splitlines():
        if line.startswith(prefix):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"no sample starting with {prefix!r}")


def test_metrics_use_prometheus_content_type(client: TestClient) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")


def test_app_info_carries_version_and_environment(client: TestClient) -> None:
    text = client.get("/metrics").text
    assert _sample(text, 'app_info{environment="ci",version="1.2.3"}') == 1.0


def test_requests_are_counted_by_route_template(client: TestClient) -> None:
    for lat in (1, 2, 3):
        client.get(f"/api/v1/distance?from_lat={lat}&from_lon=0&to_lat=0&to_lon=0")
    text = client.get("/metrics").text
    prefix = 'http_requests_total{method="GET",route="/api/v1/distance",status="200"}'
    assert _sample(text, prefix) == 3.0
    # The raw query string must never become a label.
    assert "from_lat=" not in text


def test_errors_are_counted_with_their_status(client: TestClient) -> None:
    client.get("/api/v1/distance?from_lat=95&from_lon=0&to_lat=0&to_lon=0")
    text = client.get("/metrics").text
    prefix = 'http_requests_total{method="GET",route="/api/v1/distance",status="422"}'
    assert _sample(text, prefix) == 1.0


def test_unmatched_paths_share_one_label(client: TestClient) -> None:
    client.get("/wp-admin")
    client.get("/.env")
    text = client.get("/metrics").text
    assert _sample(text, 'http_requests_total{method="GET",route="unmatched",status="404"}') == 2.0
    assert "wp-admin" not in text


def test_latency_histogram_and_distance_histogram_are_recorded(client: TestClient) -> None:
    client.get("/api/v1/distance?from_lat=0&from_lon=0&to_lat=0&to_lon=1")
    text = client.get("/metrics").text
    count = _sample(
        text, 'http_request_duration_seconds_count{method="GET",route="/api/v1/distance"}'
    )
    assert count == 1.0
    assert _sample(text, "distance_calculated_km_count") == 1.0
    assert _sample(text, 'distance_calculated_km_bucket{le="500.0"}') == 1.0


def test_each_app_has_its_own_registry(settings: Settings) -> None:
    # Two apps in one process must not share (or clash over) metrics.
    first, second = create_app(settings), create_app(settings)
    with TestClient(first) as a, TestClient(second) as b:
        a.get("/healthz")
        a.get("/healthz")
        b.get("/healthz")
        prefix = 'http_requests_total{method="GET",route="/healthz",status="200"}'
        assert _sample(a.get("/metrics").text, prefix) == 2.0
        assert _sample(b.get("/metrics").text, prefix) == 1.0
