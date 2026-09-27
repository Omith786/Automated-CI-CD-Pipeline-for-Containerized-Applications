"""Great-circle geometry on a spherical Earth.

The haversine formula is used rather than the spherical law of cosines because
it stays numerically stable for very short distances. Treating the Earth as a
sphere puts results within about 0.5% of the WGS-84 ellipsoid, which is plenty
for a demo service and keeps the maths dependency-free.
"""

import itertools
import math
from dataclasses import dataclass
from enum import StrEnum

# IUGG mean Earth radius.
EARTH_RADIUS_KM = 6371.0088


class Unit(StrEnum):
    """Supported distance units."""

    KILOMETRES = "km"
    MILES = "mi"
    NAUTICAL_MILES = "nmi"


_KM_PER_UNIT: dict[Unit, float] = {
    Unit.KILOMETRES: 1.0,
    Unit.MILES: 1.609344,
    Unit.NAUTICAL_MILES: 1.852,
}


@dataclass(frozen=True, slots=True)
class Point:
    """A position in decimal degrees (WGS-84 latitude and longitude)."""

    lat: float
    lon: float

    def __post_init__(self) -> None:
        if not -90.0 <= self.lat <= 90.0:
            raise ValueError(f"latitude must be within [-90, 90], got {self.lat}")
        if not -180.0 <= self.lon <= 180.0:
            raise ValueError(f"longitude must be within [-180, 180], got {self.lon}")


def convert(km: float, unit: Unit) -> float:
    """Convert a distance in kilometres to ``unit``."""
    return km / _KM_PER_UNIT[unit]


def haversine_km(a: Point, b: Point) -> float:
    """Return the great-circle distance between two points in kilometres."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    # Clamp guards against h creeping just above 1 through rounding for antipodal points.
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, h)))


def initial_bearing(a: Point, b: Point) -> float:
    """Return the initial compass bearing from ``a`` to ``b`` in degrees [0, 360).

    Along a great circle the heading changes continuously, so this is the
    heading at departure only.
    """
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def route_legs_km(points: list[Point]) -> list[float]:
    """Return the length of each consecutive leg of a route in kilometres."""
    if len(points) < 2:
        raise ValueError("a route needs at least two points")
    return [haversine_km(p, q) for p, q in itertools.pairwise(points)]
