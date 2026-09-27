"""Tests for picking the pods of a Deployment's current revision."""

from typing import Any

import current_pods
import pytest

Obj = dict[str, Any]

DEPLOYMENT: Obj = {
    "metadata": {"uid": "dep-1", "annotations": {current_pods.REVISION: "3"}},
    "spec": {"selector": {"matchLabels": {"app.kubernetes.io/name": "distance-api"}}},
}


def _rs(uid: str, revision: str, owner: str = "dep-1") -> Obj:
    return {
        "metadata": {
            "uid": uid,
            "annotations": {current_pods.REVISION: revision},
            "ownerReferences": [{"uid": owner}],
        }
    }


def _pod(
    name: str, rs_uid: str, ready: bool = True, terminating: bool = False, ip: str = ""
) -> Obj:
    metadata: Obj = {"name": name, "ownerReferences": [{"uid": rs_uid}]}
    if terminating:
        metadata["deletionTimestamp"] = "2026-01-01T00:00:00Z"
    return {
        "metadata": metadata,
        "status": {
            "podIP": ip or f"10.0.0.{len(name)}",
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
        },
    }


REPLICASETS = [_rs("rs-old", "2"), _rs("rs-new", "3"), _rs("rs-foreign", "3", owner="other")]


def test_only_current_revision_pods_are_selected() -> None:
    pods = [
        _pod("new-b", "rs-new"),
        _pod("old-a", "rs-old"),  # previous release, still running
        _pod("new-a", "rs-new"),
        _pod("foreign", "rs-foreign"),  # same revision number, different Deployment
    ]
    names = [
        p["metadata"]["name"]
        for p in current_pods.current_ready_pods(DEPLOYMENT, REPLICASETS, pods)
    ]
    assert names == ["new-a", "new-b"]


def test_unready_and_terminating_pods_are_skipped() -> None:
    pods = [
        _pod("new-a", "rs-new", ready=False),
        _pod("new-b", "rs-new", terminating=True),
        _pod("new-c", "rs-new"),
    ]
    selected = current_pods.current_ready_pods(DEPLOYMENT, REPLICASETS, pods)
    assert [p["metadata"]["name"] for p in selected] == ["new-c"]


def test_no_current_replicaset_means_no_pods() -> None:
    assert current_pods.current_ready_pods(DEPLOYMENT, [_rs("rs-old", "2")], []) == []


def test_ready_endpoint_ips() -> None:
    slices: list[Obj] = [
        {
            "endpoints": [
                {"addresses": ["10.0.0.1"], "conditions": {"ready": True}},
                {"addresses": ["10.0.0.2"], "conditions": {"ready": False, "terminating": True}},
                {"addresses": ["10.0.0.3"]},  # no conditions: treated as ready
            ]
        },
        {"endpoints": None},  # an empty slice
    ]
    assert current_pods.ready_endpoint_ips(slices) == {"10.0.0.1", "10.0.0.3"}


def _fake_cluster(monkeypatch: pytest.MonkeyPatch, pods: list[Obj], slices: list[Obj]) -> None:
    def fake_get(namespace: str, *args: str) -> Obj:
        kind = args[0]
        if kind == "deployment":
            return DEPLOYMENT
        return {
            "replicaset": {"items": REPLICASETS},
            "pod": {"items": pods},
            "endpointslice": {"items": slices},
        }[kind]

    monkeypatch.setattr(current_pods, "_kubectl_get", fake_get)


def test_main_prints_pods_when_service_routes_to_them(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pods = [_pod("new-a", "rs-new", ip="10.0.0.5")]
    slices = [{"endpoints": [{"addresses": ["10.0.0.5"], "conditions": {"ready": True}}]}]
    _fake_cluster(monkeypatch, pods, slices)
    code = current_pods.main(["--namespace", "staging", "--service", "distance-api"])
    assert code == 0
    assert capsys.readouterr().out == "new-a\n"


def test_main_fails_when_service_is_not_routing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_cluster(monkeypatch, [_pod("new-a", "rs-new", ip="10.0.0.5")], [])
    code = current_pods.main(["--namespace", "staging", "--service", "distance-api", "--wait", "0"])
    assert code == 1
    assert "not routing to ['10.0.0.5']" in capsys.readouterr().err


def test_main_fails_without_ready_pods(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_cluster(monkeypatch, [_pod("new-a", "rs-new", ready=False)], [])
    assert current_pods.main(["--namespace", "staging", "--wait", "0"]) == 1
    assert "no ready pods" in capsys.readouterr().err


def test_main_without_service_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_cluster(monkeypatch, [_pod("new-a", "rs-new")], [])
    assert current_pods.main(["--namespace", "staging"]) == 0
    assert capsys.readouterr().out == "new-a\n"
