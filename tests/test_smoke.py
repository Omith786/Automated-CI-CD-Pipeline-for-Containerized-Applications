"""Run the post-deployment smoke tests against a real, locally served instance.

This proves the smoke script (which gates every deployment and triggers
rollbacks) passes against a healthy service and fails when it should.
"""

import socket
import threading
import time
from collections.abc import Iterator

import pytest
import smoke_test
import uvicorn

from app import __main__ as entrypoint
from app.config import Environment, Settings, get_settings
from app.main import create_app


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    settings = Settings(
        _env_file=None,
        version="2.0.0",
        environment=Environment.STAGING,
        instance="smoke-pod",
    )
    port = _free_port()
    config = uvicorn.Config(
        create_app(settings), host="127.0.0.1", port=port, log_config=None, access_log=False
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def _run(*argv: str) -> int:
    return smoke_test.run(smoke_test.parse_args(list(argv)))


def test_smoke_passes_against_healthy_release(
    base_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _run(
        "--base-url", base_url, "--expected-version", "2.0.0", "--expected-environment", "staging"
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "7/7 smoke checks passed" in out
    assert "release 2.0.0 (staging) on smoke-pod" in out


def test_smoke_fails_on_wrong_version(base_url: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run("--base-url", base_url, "--expected-version", "3.0.0") == 1
    out = capsys.readouterr().out
    assert "FAIL release: expected version '3.0.0', got '2.0.0'" in out
    assert "6/7 smoke checks passed" in out


def test_basic_mode_runs_only_health_and_identity(
    base_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run("--base-url", base_url, "--basic", "--expected-environment", "staging") == 0
    out = capsys.readouterr().out
    assert "3/3 smoke checks passed" in out
    assert "distance endpoint" not in out


def test_smoke_fails_on_wrong_environment(base_url: str) -> None:
    assert _run("--base-url", base_url, "--expected-environment", "production") == 1


def test_smoke_fails_when_nothing_is_listening(capsys: pytest.CaptureFixture[str]) -> None:
    url = f"http://127.0.0.1:{_free_port()}"
    assert _run("--base-url", url, "--wait", "0.5") == 1
    assert "FAIL readiness" in capsys.readouterr().out


class _FakeClient(smoke_test.Client):
    """Returns canned responses so individual checks can be driven to failure."""

    def __init__(self, responses: dict[str, smoke_test.HttpResult]) -> None:
        super().__init__("http://fake")
        self.responses = responses

    def request(
        self, method: str, path: str, payload: object | None = None
    ) -> smoke_test.HttpResult:
        for prefix, result in self.responses.items():
            if path.startswith(prefix):
                return result
        return smoke_test.HttpResult(404, {}, b"{}")


def test_readiness_wait_reports_last_status() -> None:
    client = _FakeClient({"/readyz": smoke_test.HttpResult(503, {}, b"{}")})
    with pytest.raises(smoke_test.SmokeTestError, match="returned 503"):
        smoke_test.wait_until_ready(client, deadline_s=0.2, interval_s=0.05)


def test_distance_check_catches_wrong_answer() -> None:
    client = _FakeClient({"/api/v1/distance": smoke_test.HttpResult(200, {}, b'{"distance": 1.0}')})
    with pytest.raises(smoke_test.SmokeTestError, match="London to New York"):
        smoke_test.check_distance(client, smoke_test.parse_args(["--base-url", "x"]))


def test_metrics_check_catches_missing_metric() -> None:
    client = _FakeClient({"/metrics": smoke_test.HttpResult(200, {}, b"app_info{} 1\n")})
    with pytest.raises(smoke_test.SmokeTestError, match="http_requests_total missing"):
        smoke_test.check_metrics(client, smoke_test.parse_args(["--base-url", "x"]))


def test_entrypoint_runs_uvicorn_with_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        captured.update(kwargs, app=app)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("APP_PORT", "8123")
    get_settings.cache_clear()
    try:
        entrypoint.main()
    finally:
        get_settings.cache_clear()
    assert captured["port"] == 8123
    assert captured["log_config"] is None
    assert captured["app"] is not None
