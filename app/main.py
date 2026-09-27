"""Application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import ops, v1
from app.config import Settings, get_settings
from app.logging_config import configure_logging
from app.metrics import Metrics
from app.middleware import RequestContextMiddleware

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured FastAPI application.

    Tests pass their own ``Settings``; in production the settings come from
    the environment.
    """
    settings = settings or get_settings()
    configure_logging(settings)
    metrics = Metrics()
    metrics.info.labels(settings.version, settings.environment.value).set(1)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.started = True
        logger.info("service started", extra={"port": settings.port})
        yield
        app.state.started = False
        logger.info("service stopping")

    app = FastAPI(
        title="Distance API",
        summary="Great-circle distance service used to exercise the CI/CD pipeline.",
        version=settings.version,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.metrics = metrics
    app.state.started = False
    app.add_middleware(RequestContextMiddleware, metrics=metrics)
    app.include_router(ops)
    app.include_router(v1)
    return app
