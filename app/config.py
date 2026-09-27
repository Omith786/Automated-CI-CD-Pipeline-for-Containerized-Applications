"""Runtime configuration, read from environment variables.

Every setting has a safe default so the service starts with no configuration at
all. In Kubernetes the values come from the overlay's ConfigMap, and
``APP_VERSION`` is baked into the image at build time so a running pod can
always report exactly which release it is.
"""

from enum import StrEnum
from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environments the service knows about."""

    LOCAL = "local"
    CI = "ci"
    STAGING = "staging"
    PRODUCTION = "production"


class LogFormat(StrEnum):
    """Output format for log records."""

    JSON = "json"
    TEXT = "text"


class Settings(BaseSettings):
    """Service settings. Each field maps to an ``APP_``-prefixed variable."""

    model_config = SettingsConfigDict(
        env_prefix="APP_", env_file=".env", extra="ignore", populate_by_name=True
    )

    name: str = "distance-api"
    version: str = "0.0.0-dev"
    environment: Environment = Environment.LOCAL
    host: str = "0.0.0.0"  # noqa: S104 - the container must listen on all interfaces
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    log_format: LogFormat = LogFormat.JSON
    # Upper bound on waypoints per route request, to cap the work one call can cause.
    max_route_waypoints: int = Field(default=100, ge=2, le=10_000)
    # Pod name from the Kubernetes Downward API; handy for seeing load balancing.
    instance: str = Field(
        default="local", validation_alias=AliasChoices("APP_INSTANCE", "POD_NAME")
    )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, parsed once on first use."""
    return Settings()
