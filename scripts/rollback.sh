#!/usr/bin/env bash
# Roll a Deployment back to its previous revision and verify the result.
#
# Called automatically by deploy.sh when a release fails verification, and
# usable by hand (`make rollback ENV=staging`) for a manual rollback.
#
# Usage: scripts/rollback.sh --namespace NS [--deployment NAME] [--expected-environment ENV]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Overridable so the decision logic can be tested without a cluster.
SMOKE="${SMOKE_SCRIPT:-${ROOT}/scripts/smoke.sh}"
namespace=""
deployment="distance-api"
expected_environment=""
timeout="180s"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace) namespace="$2"; shift 2 ;;
    --deployment) deployment="$2"; shift 2 ;;
    --expected-environment) expected_environment="$2"; shift 2 ;;
    --timeout) timeout="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "${namespace}" ]] || { echo "usage: $0 --namespace NS" >&2; exit 2; }

image_of() {
  kubectl --namespace "${namespace}" get deployment "${deployment}" \
    -o jsonpath='{.spec.template.spec.containers[0].image}'
}

printf '\n==> rolling back %s/%s from %s\n' "${namespace}" "${deployment}" "$(image_of)"
kubectl --namespace "${namespace}" rollout undo "deployment/${deployment}"
kubectl --namespace "${namespace}" rollout status "deployment/${deployment}" --timeout "${timeout}"

restored="$(image_of)"
printf '\n==> verifying restored release %s\n' "${restored}"
# Health and identity checks only: the restored release passed the full suite
# when it was first deployed, and newer functional checks may not apply to it.
args=(--namespace "${namespace}" --deployment "${deployment}" --basic)
if [[ -n "${expected_environment}" ]]; then args+=(--expected-environment "${expected_environment}"); fi
if "${SMOKE}" "${args[@]}"; then
  echo "::notice::rolled back ${namespace} to ${restored}"
else
  echo "::error::rollback of ${namespace} completed but the restored release also failed its smoke tests" >&2
  exit 1
fi
