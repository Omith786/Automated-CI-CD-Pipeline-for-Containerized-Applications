"""Tests for the deploy and rollback decision logic in scripts/deploy.sh.

A real cluster is only available inside the CD workflow, so these tests put a
fake ``kubectl`` first on PATH. It renders manifests with the real kustomize
binary (so the image pinning is genuinely exercised) and simulates the
Deployment's revision history, which is enough to check when the script rolls
back and when it must not.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIGEST_REF = (
    "ghcr.io/example/distance-api@sha256:" + "a" * 64  # a well-formed but fake digest
)

KUSTOMIZE = shutil.which("kustomize") or (
    str(ROOT / ".tools/bin/kustomize") if (ROOT / ".tools/bin/kustomize").exists() else None
)
pytestmark = [
    pytest.mark.skipif(KUSTOMIZE is None, reason="kustomize binary not available"),
    pytest.mark.skipif(sys.platform == "win32", reason="bash scripts"),
]

FAKE_KUBECTL = r'''#!/usr/bin/env python3
"""Minimal stand-in for kubectl, driven by a JSON state file."""
import json, os, re, subprocess, sys

state_path = os.environ["FAKE_KUBE_STATE"]
with open(state_path) as fh:
    state = json.load(fh)
args = sys.argv[1:]
if args[:1] == ["--namespace"]:
    args = args[2:]
state["calls"].append(" ".join(args))

def save():
    with open(state_path, "w") as fh:
        json.dump(state, fh)

code = 0
if args[0] == "kustomize":
    save()
    sys.exit(subprocess.call([os.environ["KUSTOMIZE_BIN"], "build", args[1]]))
elif args[:2] == ["get", "deployment"]:
    if not state["exists"]:
        print("Error from server (NotFound)", file=sys.stderr)
        code = 1
    elif "revision" in args[-1]:
        print(state["history"][-1]["revision"], end="")
    else:
        print(state["history"][-1]["image"], end="")
elif args[0] == "apply":
    manifest = open(args[2]).read()
    image = re.search(r"^\s+image: (\S+)", manifest, re.M).group(1)
    state["applied"].append(manifest)
    if not state["exists"] or state["history"][-1]["image"] != image:
        rev = state["history"][-1]["revision"] + 1 if state["exists"] else 1
        state["history"].append({"revision": rev, "image": image})
    state["exists"] = True
elif args[:2] == ["rollout", "status"]:
    code = 1 if state["rollout_fails"] and not state["undone"] else 0
elif args[:2] == ["rollout", "undo"]:
    previous = state["history"][-2]
    state["history"].append({"revision": state["history"][-1]["revision"] + 1,
                             "image": previous["image"]})
    state["undone"] = True
save()
sys.exit(code)
'''

# Fails when asked to verify the version the test marks as bad.
FAKE_SMOKE = r"""#!/usr/bin/env bash
echo "smoke $*" >> "$FAKE_SMOKE_LOG"
for arg in "$@"; do [[ "$arg" == "bad" ]] && exit 1; done
exit 0
"""


class FakeCluster:
    """Runs deploy.sh against the fake kubectl and exposes what happened."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        kubectl = bin_dir / "kubectl"
        kubectl.write_text(FAKE_KUBECTL)
        kubectl.chmod(0o755)
        self.smoke = tmp_path / "smoke.sh"
        self.smoke.write_text(FAKE_SMOKE)
        self.smoke.chmod(0o755)
        self.state_file = tmp_path / "state.json"
        self.smoke_log = tmp_path / "smoke.log"
        self.smoke_log.touch()
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_KUBE_STATE": str(self.state_file),
            "FAKE_SMOKE_LOG": str(self.smoke_log),
            "SMOKE_SCRIPT": str(self.smoke),
            "KUSTOMIZE_BIN": str(KUSTOMIZE),
            "RENDER_ROOT": str(tmp_path / "rendered"),
        }
        self.set_state(existing_image=None)

    def set_state(self, existing_image: str | None, rollout_fails: bool = False) -> None:
        history = [{"revision": 1, "image": existing_image}] if existing_image else []
        self.state_file.write_text(
            json.dumps(
                {
                    "exists": existing_image is not None,
                    "history": history,
                    "rollout_fails": rollout_fails,
                    "undone": False,
                    "calls": [],
                    "applied": [],
                }
            )
        )

    @property
    def state(self) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(self.state_file.read_text())
        return result

    def deploy(self, *extra: str, image: str = DIGEST_REF) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(ROOT / "scripts/deploy.sh"),
                "--overlay",
                str(ROOT / "k8s/overlays/staging"),
                "--image",
                image,
                *extra,
            ],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )


@pytest.fixture
def cluster(tmp_path: Path) -> FakeCluster:
    return FakeCluster(tmp_path)


def test_first_deploy_pins_digest_and_succeeds(cluster: FakeCluster) -> None:
    result = cluster.deploy("--expect-version", "1.0.0", "--pull-secret", "ghcr-pull")
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = cluster.state["applied"][0]
    assert f"image: {DIGEST_REF}" in manifest
    assert "namespace: staging" in manifest
    assert "kubernetes.io/change-cause: deploy " + DIGEST_REF in manifest
    assert "- name: ghcr-pull" in manifest
    smoke = cluster.smoke_log.read_text()
    assert "--expected-environment staging --expected-version 1.0.0" in smoke


def test_tag_reference_is_accepted(cluster: FakeCluster) -> None:
    result = cluster.deploy(image="localhost:5000/distance-api:dev")
    assert result.returncode == 0, result.stderr
    assert "image: localhost:5000/distance-api:dev" in cluster.state["applied"][0]


def test_failed_smoke_test_rolls_back_to_previous_release(cluster: FakeCluster) -> None:
    cluster.set_state(existing_image="ghcr.io/example/distance-api:good")
    result = cluster.deploy("--expect-version", "bad")
    assert result.returncode == 1
    state = cluster.state
    assert any(call.startswith("rollout undo") for call in state["calls"])
    assert state["history"][-1]["image"] == "ghcr.io/example/distance-api:good"
    assert "rolled back staging to ghcr.io/example/distance-api:good" in result.stdout
    # The restored release is smoke-tested too, without the new version expectation.
    last_smoke = cluster.smoke_log.read_text().splitlines()[-1]
    assert "--expected-version" not in last_smoke
    assert last_smoke.endswith("--basic --expected-environment staging")


def test_failed_rollout_rolls_back(cluster: FakeCluster) -> None:
    cluster.set_state(existing_image="ghcr.io/example/distance-api:good", rollout_fails=True)
    result = cluster.deploy()
    assert result.returncode == 1
    assert any(call.startswith("rollout undo") for call in cluster.state["calls"])


def test_first_deploy_failure_has_nothing_to_roll_back(cluster: FakeCluster) -> None:
    result = cluster.deploy("--expect-version", "bad")
    assert result.returncode == 1
    assert "nothing to roll back to" in result.stderr
    assert not any(call.startswith("rollout undo") for call in cluster.state["calls"])


def test_redeploying_the_same_image_never_undoes_an_older_revision(cluster: FakeCluster) -> None:
    cluster.set_state(existing_image=DIGEST_REF)
    result = cluster.deploy("--expect-version", "bad")
    assert result.returncode == 1
    assert "not rolling back" in result.stderr
    assert not any(call.startswith("rollout undo") for call in cluster.state["calls"])


def test_basic_checks_flag_is_passed_to_smoke_tests(cluster: FakeCluster) -> None:
    assert cluster.deploy("--basic-checks").returncode == 0
    assert "--basic" in cluster.smoke_log.read_text()


def test_rollback_can_be_disabled(cluster: FakeCluster) -> None:
    cluster.set_state(existing_image="ghcr.io/example/distance-api:good")
    result = cluster.deploy("--expect-version", "bad", "--no-rollback")
    assert result.returncode == 1
    assert "rollback disabled" in result.stderr
    assert not any(call.startswith("rollout undo") for call in cluster.state["calls"])


def test_missing_arguments_are_rejected(cluster: FakeCluster) -> None:
    result = subprocess.run(
        [str(ROOT / "scripts/deploy.sh"), "--overlay", "k8s/overlays/staging"],
        env=cluster.env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "usage" in result.stderr
