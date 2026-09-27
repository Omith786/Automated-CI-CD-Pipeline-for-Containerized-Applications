"""HTTP routes.

``ops`` holds the endpoints the platform relies on (info, probes, metrics);
``geo`` holds the business API. They are separate routers so the probe
endpoints stay unversioned while the public API lives under ``/api/v1``.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app import geo
from app.config import Settings
from app.metrics import Metrics
from app.schemas import (
    Coordinate,
    DistanceResponse,
    HealthStatus,
    RouteRequest,
    RouteResponse,
    ServiceInfo,
)

logger = logging.getLogger(__name__)

ops = APIRouter(tags=["operations"])
v1 = APIRouter(prefix="/api/v1", tags=["geo"])


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _metrics(request: Request) -> Metrics:
    metrics: Metrics = request.app.state.metrics
    return metrics


@ops.get("/", response_model=ServiceInfo)
def service_info(request: Request) -> ServiceInfo:
    """Report the running service's name, version, environment and pod."""
    settings = _settings(request)
    return ServiceInfo(
        name=settings.name,
        version=settings.version,
        environment=settings.environment.value,
        instance=settings.instance,
    )


@ops.get("/healthz", response_model=HealthStatus)
def liveness() -> HealthStatus:
    """Liveness probe: the process is up and the event loop is responsive.

    Deliberately checks nothing external. If a dependency were down, restarting
    this pod would not fix it, and a liveness check that fails for that reason
    would only cause restart storms.
    """
    return HealthStatus(status="ok")


@ops.get(
    "/readyz",
    response_model=HealthStatus,
    responses={503: {"model": HealthStatus, "description": "Not ready to serve traffic."}},
)
def readiness(request: Request, response: Response) -> HealthStatus:
    """Readiness probe: the instance should receive traffic.

    Only startup completion is checked today. Checks for downstream
    dependencies (a database, a cache) belong here, not in the liveness probe.
    """
    checks = {"startup_complete": bool(getattr(request.app.state, "started", False))}
    if all(checks.values()):
        return HealthStatus(status="ready", checks=checks)
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthStatus(status="not_ready", checks=checks)


@ops.get("/metrics", include_in_schema=False)
def metrics(request: Request) -> Response:
    """Expose metrics in the Prometheus text format."""
    return Response(generate_latest(_metrics(request).registry), media_type=CONTENT_TYPE_LATEST)


@v1.get("/distance", response_model=DistanceResponse)
def distance(
    request: Request,
    from_lat: Annotated[float, Query(ge=-90, le=90, description="Origin latitude.")],
    from_lon: Annotated[float, Query(ge=-180, le=180, description="Origin longitude.")],
    to_lat: Annotated[float, Query(ge=-90, le=90, description="Destination latitude.")],
    to_lon: Annotated[float, Query(ge=-180, le=180, description="Destination longitude.")],
    unit: geo.Unit = geo.Unit.KILOMETRES,
) -> DistanceResponse:
    """Return the great-circle distance and initial bearing between two points."""
    origin = geo.Point(from_lat, from_lon)
    destination = geo.Point(to_lat, to_lon)
    km = geo.haversine_km(origin, destination)
    _metrics(request).distance_km.observe(km)
    return DistanceResponse(
        origin=Coordinate(lat=from_lat, lon=from_lon),
        destination=Coordinate(lat=to_lat, lon=to_lon),
        distance=round(geo.convert(km, unit), 3),
        unit=unit,
        initial_bearing_deg=round(geo.initial_bearing(origin, destination), 2),
    )


@v1.post("/route", response_model=RouteResponse)
def route(request: Request, body: RouteRequest) -> RouteResponse:
    """Return the total and per-leg great-circle distances along a route."""
    limit = _settings(request).max_route_waypoints
    if len(body.waypoints) > limit:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"a route may have at most {limit} waypoints",
        )
    points = [geo.Point(w.lat, w.lon) for w in body.waypoints]
    legs_km = geo.route_legs_km(points)
    total_km = sum(legs_km)
    _metrics(request).distance_km.observe(total_km)
    logger.debug("route measured", extra={"waypoints": len(points), "total_km": total_km})
    return RouteResponse(
        total_distance=round(geo.convert(total_km, body.unit), 3),
        unit=body.unit,
        legs=[round(geo.convert(leg, body.unit), 3) for leg in legs_km],
    )
