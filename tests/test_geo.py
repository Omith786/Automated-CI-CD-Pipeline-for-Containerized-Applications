"""Tests for the great-circle maths."""

import math

import pytest

from app import geo
from app.geo import Point, Unit

LONDON = Point(51.5074, -0.1278)
NEW_YORK = Point(40.7128, -74.0060)
SYDNEY = Point(-33.8688, 151.2093)


def test_london_to_new_york_matches_reference() -> None:
    # Reference value for a sphere of radius 6371.0088 km.
    assert geo.haversine_km(LONDON, NEW_YORK) == pytest.approx(5570.2, abs=1.0)


def test_distance_is_symmetric() -> None:
    assert geo.haversine_km(LONDON, SYDNEY) == pytest.approx(geo.haversine_km(SYDNEY, LONDON))


def test_distance_to_self_is_zero() -> None:
    assert geo.haversine_km(LONDON, LONDON) == 0.0


def test_one_degree_of_latitude() -> None:
    expected = 2 * math.pi * geo.EARTH_RADIUS_KM / 360
    assert geo.haversine_km(Point(0, 0), Point(1, 0)) == pytest.approx(expected)


def test_antipodal_points_are_half_the_circumference() -> None:
    half = math.pi * geo.EARTH_RADIUS_KM
    assert geo.haversine_km(Point(0, 0), Point(0, 180)) == pytest.approx(half)
    assert geo.haversine_km(Point(90, 0), Point(-90, 0)) == pytest.approx(half)


def test_crossing_the_antimeridian_takes_the_short_way() -> None:
    # 179E to 179W is 2 degrees of longitude, not 358.
    km = geo.haversine_km(Point(0, 179), Point(0, -179))
    assert km == pytest.approx(geo.haversine_km(Point(0, 0), Point(0, 2)))


@pytest.mark.parametrize(
    ("destination", "bearing"),
    [
        (Point(10, 0), 0.0),
        (Point(0, 10), 90.0),
        (Point(-10, 0), 180.0),
        (Point(0, -10), 270.0),
    ],
)
def test_cardinal_bearings_from_the_origin(destination: Point, bearing: float) -> None:
    assert geo.initial_bearing(Point(0, 0), destination) == pytest.approx(bearing)


def test_bearing_london_to_new_york_heads_west_north_west() -> None:
    assert geo.initial_bearing(LONDON, NEW_YORK) == pytest.approx(288.3, abs=0.2)


def test_bearing_is_always_in_range() -> None:
    for a, b in [(LONDON, NEW_YORK), (NEW_YORK, SYDNEY), (SYDNEY, LONDON)]:
        assert 0.0 <= geo.initial_bearing(a, b) < 360.0


@pytest.mark.parametrize(
    ("unit", "expected"),
    [(Unit.KILOMETRES, 1.852), (Unit.MILES, 1.852 / 1.609344), (Unit.NAUTICAL_MILES, 1.0)],
)
def test_unit_conversion(unit: Unit, expected: float) -> None:
    assert geo.convert(1.852, unit) == pytest.approx(expected)


def test_route_legs() -> None:
    legs = geo.route_legs_km([Point(0, 0), Point(0, 1), Point(0, 3)])
    assert len(legs) == 2
    assert legs[1] == pytest.approx(2 * legs[0])


def test_route_needs_two_points() -> None:
    with pytest.raises(ValueError, match="at least two"):
        geo.route_legs_km([LONDON])


@pytest.mark.parametrize(("lat", "lon"), [(90.1, 0), (-90.1, 0), (0, 180.1), (0, -180.1)])
def test_point_rejects_out_of_range_coordinates(lat: float, lon: float) -> None:
    with pytest.raises(ValueError, match="must be within"):
        Point(lat, lon)
