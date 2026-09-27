"""Tests for structured logging and environment-driven configuration."""

import json
import logging
import sys

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Environment, LogFormat, Settings
from app.logging_config import JsonFormatter, configure_logging, request_id_var
from app.main import create_app


def _record(message: str = "hello", **extra: object) -> logging.LogRecord:
    record = logging.makeLogRecord({"name": "test", "levelno": 20, "levelname": "INFO"})
    record.msg = message
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_one_json_object() -> None:
    line = JsonFormatter({"service": "svc"}).format(_record("hi %s", status=200))
    payload = json.loads(line)
    assert payload["message"] == "hi %s"
    assert payload["level"] == "INFO"
    assert payload["service"] == "svc"
    assert payload["status"] == 200
    assert payload["timestamp"].endswith("+00:00")
    assert "\n" not in line


def test_json_formatter_drops_uvicorn_colour_copy() -> None:
    payload = json.loads(JsonFormatter().format(_record(color_message="\x1b[36mhi\x1b[0m")))
    assert "color_message" not in payload


def test_json_formatter_includes_request_id_from_context() -> None:
    token = request_id_var.set("req-1")
    try:
        payload = json.loads(JsonFormatter().format(_record()))
    finally:
        request_id_var.reset(token)
    assert payload["request_id"] == "req-1"


def test_json_formatter_renders_exceptions() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = logging.makeLogRecord({"msg": "failed", "levelname": "ERROR"})
        record.exc_info = sys.exc_info()
    payload = json.loads(JsonFormatter().format(record))
    assert "RuntimeError: boom" in payload["exception"]


def test_access_log_is_structured(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    with TestClient(create_app(settings)) as client:
        client.get("/api/v1/distance?from_lat=0&from_lon=0&to_lat=0&to_lon=1")
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    access = next(line for line in lines if line["logger"] == "app.access")
    assert access["route"] == "/api/v1/distance"
    assert access["status"] == 200
    assert access["version"] == "1.2.3"
    assert access["instance"] == "test-pod"
    assert len(access["request_id"]) == 32
    assert access["duration_ms"] >= 0


def test_probe_requests_log_at_debug_only(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    with TestClient(create_app(settings)) as client:
        client.get("/healthz")
    out = capsys.readouterr().out
    assert '"logger": "app.access"' not in out


def test_text_log_format(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings.model_copy(update={"log_format": LogFormat.TEXT}))
    logging.getLogger("demo").warning("plain text")
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "demo: plain text" in out


def test_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_VERSION", "9.9.9")
    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    monkeypatch.setenv("APP_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("APP_PORT", "9000")
    monkeypatch.setenv("POD_NAME", "distance-api-abc123")
    settings = Settings(_env_file=None)
    assert settings.version == "9.9.9"
    assert settings.environment is Environment.PRODUCTION
    assert settings.log_level == "DEBUG"
    assert settings.port == 9000
    assert settings.instance == "distance-api-abc123"


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("APP_VERSION", "APP_ENVIRONMENT", "POD_NAME", "APP_INSTANCE"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.environment is Environment.LOCAL
    assert settings.version == "0.0.0-dev"
    assert settings.instance == "local"


@pytest.mark.parametrize(
    ("name", "value"),
    [("APP_LOG_LEVEL", "LOUD"), ("APP_PORT", "70000"), ("APP_ENVIRONMENT", "qa")],
)
def test_invalid_settings_fail_fast(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
