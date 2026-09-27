"""Request and response models for the public API."""

from pydantic import BaseModel, Field

from app.geo import Unit


class Coordinate(BaseModel):
    """A latitude/longitude pair in decimal degrees."""

    lat: float = Field(ge=-90, le=90, examples=[51.5074])
    lon: float = Field(ge=-180, le=180, examples=[-0.1278])


class DistanceResponse(BaseModel):
    """Great-circle distance between two coordinates."""

    origin: Coordinate
    destination: Coordinate
    distance: float = Field(description="Great-circle distance in the requested unit.")
    unit: Unit
    initial_bearing_deg: float = Field(description="Heading at departure, clockwise from north.")


class RouteRequest(BaseModel):
    """An ordered list of waypoints to measure."""

    waypoints: list[Coordinate] = Field(min_length=2)
    unit: Unit = Unit.KILOMETRES


class RouteResponse(BaseModel):
    """Total and per-leg distances along a route."""

    total_distance: float
    unit: Unit
    legs: list[float]


class ServiceInfo(BaseModel):
    """What is running: returned by ``GET /`` and used by the smoke tests."""

    name: str
    version: str
    environment: str
    instance: str


class HealthStatus(BaseModel):
    """Result of a liveness or readiness probe."""

    status: str
    checks: dict[str, bool] = Field(default_factory=dict)
