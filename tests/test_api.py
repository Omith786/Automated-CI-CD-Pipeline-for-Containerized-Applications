"""Tests for the HTTP API."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

LONDON_TO_NEW_YORK = (
    "/api/v1/distance?from_lat=51.5074&from_lon=-0.1278&to_lat=40.7128&to_lon=-74.006"
)


def test_service_info_reports_release(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {
        "name": "distance-api",
        "version": "1.2.3",
        "environment": "ci",
        "instance": "test-pod",
    }


def test_liveness(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_after_startup(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"startup_complete": True}}


def test_readiness_before_startup_is_503(app: FastAPI) -> None:
    # Without the context manager the lifespan never runs, as before uvicorn finishes starting.
    response = TestClient(app).get("/readyz")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_readiness_is_503_after_shutdown(app: FastAPI) -> None:
    with TestClient(app) as client:
        assert client.get("/readyz").status_code == 200
    assert TestClient(app).get("/readyz").status_code == 503


def test_distance_in_kilometres(client: TestClient) -> None:
    response = client.get(LONDON_TO_NEW_YORK)
    assert response.status_code == 200
    body = response.json()
    assert body["unit"] == "km"
    assert body["distance"] == pytest.approx(5570.2, abs=1.0)
    assert body["initial_bearing_deg"] == pytest.approx(288.3, abs=0.2)
    assert body["origin"] == {"lat": 51.5074, "lon": -0.1278}


@pytest.mark.parametrize(("unit", "expected"), [("mi", 3461.2), ("nmi", 3007.7)])
def test_distance_in_other_units(client: TestClient, unit: str, expected: float) -> None:
    response = client.get(f"{LONDON_TO_NEW_YORK}&unit={unit}")
    assert response.status_code == 200
    assert response.json()["distance"] == pytest.approx(expected, abs=1.0)


@pytest.mark.parametrize(
    "query",
    [
        "from_lat=91&from_lon=0&to_lat=0&to_lon=0",
        "from_lat=0&from_lon=-181&to_lat=0&to_lon=0",
        "from_lat=0&from_lon=0&to_lat=0",
        "from_lat=abc&from_lon=0&to_lat=0&to_lon=0",
        "from_lat=0&from_lon=0&to_lat=0&to_lon=0&unit=furlongs",
    ],
)
def test_distance_rejects_bad_input(client: TestClient, query: str) -> None:
    assert client.get(f"/api/v1/distance?{query}").status_code == 422


def test_route_totals_its_legs(client: TestClient) -> None:
    waypoints = [{"lat": 0, "lon": 0}, {"lat": 0, "lon": 1}, {"lat": 0, "lon": 2}]
    response = client.post("/api/v1/route", json={"waypoints": waypoints})
    assert response.status_code == 200
    body = response.json()
    assert body["unit"] == "km"
    assert len(body["legs"]) == 2
    assert body["legs"][0] == pytest.approx(111.195, abs=0.01)
    assert body["total_distance"] == pytest.approx(sum(body["legs"]), abs=0.01)


def test_route_needs_two_waypoints(client: TestClient) -> None:
    response = client.post("/api/v1/route", json={"waypoints": [{"lat": 0, "lon": 0}]})
    assert response.status_code == 422


def test_route_enforces_waypoint_limit(client: TestClient) -> None:
    # The test settings allow at most 5 waypoints.
    waypoints = [{"lat": 0, "lon": i} for i in range(6)]
    response = client.post("/api/v1/route", json={"waypoints": waypoints})
    assert response.status_code == 422
    assert "at most 5" in response.json()["detail"]


def test_route_rejects_invalid_coordinates(client: TestClient) -> None:
    waypoints = [{"lat": 0, "lon": 0}, {"lat": 100, "lon": 0}]
    assert client.post("/api/v1/route", json={"waypoints": waypoints}).status_code == 422


def test_request_id_is_created_when_absent(client: TestClient) -> None:
    request_id = client.get("/healthz").headers["x-request-id"]
    assert len(request_id) == 32


def test_valid_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert response.headers["x-request-id"] == "abc-123"


def test_malformed_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": "bad id\twith spaces"})
    assert response.headers["x-request-id"] != "bad id\twith spaces"
    assert len(response.headers["x-request-id"]) == 32


def test_unknown_path_is_404(client: TestClient) -> None:
    assert client.get("/nope").status_code == 404


def test_openapi_schema_lists_public_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/", "/healthz", "/readyz", "/api/v1/distance", "/api/v1/route"} <= set(paths)
    assert "/metrics" not in paths
