#!/usr/bin/env python3
"""List the ready pods of a Deployment's current revision.

Right after a rolling update, pods from the previous ReplicaSet can still be
terminating (and still answering requests during their preStop delay).
``kubectl port-forward deployment/...`` may pick one of those, so a smoke test
could pass or fail against the wrong release. This helper finds the pods that
belong to the Deployment's *current* revision, and also confirms the Service
is routing to them, before any smoke test runs.

Usage:
    python scripts/current_pods.py --namespace staging --deployment distance-api \
        [--service distance-api] [--wait 30]

Prints one pod name per line. Exits non-zero if there are no ready pods, or if
the Service's ready endpoints do not include every current pod.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from typing import Any

REVISION = "deployment.kubernetes.io/revision"

Obj = dict[str, Any]


def _is_ready(pod: Obj) -> bool:
    conditions = pod.get("status", {}).get("conditions", [])
    return any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)


def current_ready_pods(deployment: Obj, replicasets: list[Obj], pods: list[Obj]) -> list[Obj]:
    """Return ready, non-terminating pods owned by the Deployment's current ReplicaSet."""
    revision = deployment["metadata"].get("annotations", {}).get(REVISION)
    uid = deployment["metadata"]["uid"]
    current = [
        rs
        for rs in replicasets
        if rs["metadata"].get("annotations", {}).get(REVISION) == revision
        and any(o.get("uid") == uid for o in rs["metadata"].get("ownerReferences", []))
    ]
    if not current:
        return []
    rs_uid = current[0]["metadata"]["uid"]
    return sorted(
        (
            p
            for p in pods
            if any(o.get("uid") == rs_uid for o in p["metadata"].get("ownerReferences", []))
            and "deletionTimestamp" not in p["metadata"]
            and _is_ready(p)
        ),
        key=lambda p: p["metadata"]["name"],
    )


def ready_endpoint_ips(slices: list[Obj]) -> set[str]:
    """Return the addresses a Service's EndpointSlices mark as ready."""
    ips: set[str] = set()
    for endpoint_slice in slices:
        for endpoint in endpoint_slice.get("endpoints") or []:
            # A missing "ready" condition means ready, per the EndpointSlice API.
            if endpoint.get("conditions", {}).get("ready", True):
                ips.update(endpoint.get("addresses", []))
    return ips


def _kubectl_get(namespace: str, *args: str) -> Obj:
    # kubectl is resolved from PATH on purpose: it is whichever one the operator uses.
    cmd = ["kubectl", "--namespace", namespace, "get", *args, "-o", "json"]
    out = subprocess.check_output(cmd)  # noqa: S603 - fixed argv, no shell
    result: Obj = json.loads(out)
    return result


def main(argv: list[str] | None = None) -> int:
    """Print the current pods, waiting for the Service to route to all of them."""
    parser = argparse.ArgumentParser(description="List a Deployment's current ready pods.")
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--deployment", default="distance-api")
    parser.add_argument("--service", default="", help="also require these pods behind the Service")
    parser.add_argument("--wait", type=float, default=30.0, help="seconds to wait for agreement")
    args = parser.parse_args(argv)

    deadline = time.monotonic() + args.wait
    problem = ""
    while True:
        deployment = _kubectl_get(args.namespace, "deployment", args.deployment)
        selector = ",".join(
            f"{k}={v}" for k, v in deployment["spec"]["selector"]["matchLabels"].items()
        )
        replicasets = _kubectl_get(args.namespace, "replicaset", "-l", selector)["items"]
        pods = _kubectl_get(args.namespace, "pod", "-l", selector)["items"]
        ready = current_ready_pods(deployment, replicasets, pods)

        if not ready:
            problem = "no ready pods in the current revision"
        elif args.service:
            slices = _kubectl_get(
                args.namespace, "endpointslice", "-l", f"kubernetes.io/service-name={args.service}"
            )["items"]
            missing = {p["status"].get("podIP") for p in ready} - ready_endpoint_ips(slices)
            problem = (
                f"service {args.service} is not routing to {sorted(missing)}" if missing else ""
            )
        else:
            problem = ""

        if not problem:
            for pod in ready:
                print(pod["metadata"]["name"])
            return 0
        if time.monotonic() >= deadline:
            print(f"error: {problem}", file=sys.stderr)
            return 1
        time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
