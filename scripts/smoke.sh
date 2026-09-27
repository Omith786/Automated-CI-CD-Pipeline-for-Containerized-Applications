#!/usr/bin/env bash
# Smoke-test every pod of a Deployment's current revision.
#
# Each pod is reached through its own `kubectl port-forward`, so the check runs
# against exactly the release that was just rolled out (see current_pods.py
# for why the Service or Deployment cannot be used as the target).
#
# Usage: scripts/smoke.sh --namespace NS [--deployment NAME] [smoke_test.py args...]
# Example: scripts/smoke.sh --namespace staging --expected-version 1.4.0 --expected-environment staging
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
namespace=""
deployment="distance-api"
container_port=8000
smoke_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace) namespace="$2"; shift 2 ;;
    --deployment) deployment="$2"; shift 2 ;;
    *) smoke_args+=("$1"); shift ;;
  esac
done
[[ -n "${namespace}" ]] || { echo "usage: $0 --namespace NS [smoke args...]" >&2; exit 2; }

pods=()
while IFS= read -r pod; do
  pods+=("${pod}")
done < <(python3 "${ROOT}/scripts/current_pods.py" \
  --namespace "${namespace}" --deployment "${deployment}" --service "${deployment}")
[[ ${#pods[@]} -gt 0 ]] || { echo "no pods to test" >&2; exit 1; }

pf_pid=""
pf_log="$(mktemp)"
cleanup() {
  if [[ -n "${pf_pid}" ]]; then kill "${pf_pid}" 2>/dev/null || true; fi
  rm -f "${pf_log}"
}
trap cleanup EXIT

failed=0
for pod in "${pods[@]}"; do
  echo "--- smoke testing ${namespace}/${pod}"
  : >"${pf_log}"
  # Port 0 lets kubectl pick a free local port; it prints the one it chose.
  kubectl --namespace "${namespace}" port-forward "pod/${pod}" ":${container_port}" >"${pf_log}" 2>&1 &
  pf_pid=$!

  local_port=""
  for _ in $(seq 1 50); do
    local_port="$(sed -n 's/^Forwarding from 127\.0\.0\.1:\([0-9]*\) .*/\1/p' "${pf_log}" | head -n 1)"
    [[ -n "${local_port}" ]] && break
    sleep 0.2
  done
  if [[ -z "${local_port}" ]]; then
    echo "port-forward to ${pod} did not start:" >&2
    cat "${pf_log}" >&2
    failed=1
  elif ! python3 "${ROOT}/scripts/smoke_test.py" --base-url "http://127.0.0.1:${local_port}" \
    --wait 30 ${smoke_args[@]+"${smoke_args[@]}"}; then
    failed=1
  fi

  kill "${pf_pid}" 2>/dev/null || true
  wait "${pf_pid}" 2>/dev/null || true
  pf_pid=""
done

if [[ ${failed} -ne 0 ]]; then
  echo "smoke tests FAILED in ${namespace}" >&2
  exit 1
fi
echo "smoke tests passed on ${#pods[@]} pod(s) in ${namespace}"
