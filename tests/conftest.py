"""Shared fixtures."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Environment, LogFormat, Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    """Settings independent of the developer's environment and any .env file."""
    return Settings(
        _env_file=None,
        name="distance-api",
        version="1.2.3",
        environment=Environment.CI,
        log_format=LogFormat.JSON,
        instance="test-pod",
        max_route_waypoints=5,
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # Using the client as a context manager runs the lifespan, so the app is "started".
    with TestClient(app) as test_client:
        yield test_client
