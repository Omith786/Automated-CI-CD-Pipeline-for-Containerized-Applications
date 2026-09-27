#!/usr/bin/env bash
# Deploy one image to one environment, verify it, and roll back if it is bad.
#
#   1. Render the environment's Kustomize overlay with the image pinned to the
#      exact reference given (ideally a digest, so what was tested is what runs).
#   2. Apply it and wait for the rolling update to finish.
#   3. Smoke-test every new pod.
#   4. If step 2 or 3 fails and the Deployment had an earlier revision,
#      `kubectl rollout undo` to it, wait for that to finish and smoke-test the
#      restored release. The script still exits non-zero, so the pipeline run
#      is marked failed and nothing downstream (promotion, tagging) happens.
#
# Usage:
#   scripts/deploy.sh --overlay k8s/overlays/staging --image REF [options]
# Options:
#   --namespace NS         defaults to the overlay directory name
#   --expect-version V     smoke tests fail unless the pods report version V
#   --pull-secret NAME     add an imagePullSecret (needed for private registries)
#   --timeout DURATION     rollout timeout, default 180s
#   --basic-checks         only health and identity smoke checks (used when
#                          recreating an already-verified release)
#   --no-rollback          report the failure but leave the bad release running
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOYMENT="distance-api"
# Overridable so the decision logic can be tested without a cluster.
SMOKE="${SMOKE_SCRIPT:-${ROOT}/scripts/smoke.sh}"

overlay=""
image=""
namespace=""
expect_version=""
pull_secret=""
timeout="180s"
rollback=true
basic_checks=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --overlay) overlay="$2"; shift 2 ;;
    --image) image="$2"; shift 2 ;;
    --namespace) namespace="$2"; shift 2 ;;
    --expect-version) expect_version="$2"; shift 2 ;;
    --pull-secret) pull_secret="$2"; shift 2 ;;
    --timeout) timeout="$2"; shift 2 ;;
    --basic-checks) basic_checks=true; shift ;;
    --no-rollback) rollback=false; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [[ -z "${overlay}" || -z "${image}" ]]; then
  echo "usage: $0 --overlay DIR --image REF [--namespace NS] [--expect-version V]" >&2
  exit 2
fi

overlay_dir="$(cd "${overlay}" && pwd -P)"
environment="$(basename "${overlay_dir}")"
namespace="${namespace:-${environment}}"

log() { printf '\n==> %s\n' "$*"; }

# Split REF into repository and tag/digest. The tag test only looks after the
# last "/" so that a registry port (localhost:5000/app) is not taken for a tag.
split_image() {
  local ref="$1" last
  if [[ "${ref}" == *@* ]]; then
    image_repo="${ref%@*}"; image_tag=""; image_digest="${ref#*@}"
  else
    last="${ref##*/}"
    if [[ "${last}" == *:* ]]; then
      image_repo="${ref%:*}"; image_tag="${ref##*:}"
    else
      image_repo="${ref}"; image_tag="latest"
    fi
    image_digest=""
  fi
}

# --- 1. Render --------------------------------------------------------------
# A throwaway kustomization layered over the overlay pins the image and records
# a change-cause, without editing the committed manifests.
render_dir="${RENDER_ROOT:-${ROOT}/.rendered}/${environment}"
rm -rf "${render_dir}"
mkdir -p "${render_dir}"
# Physical paths on both sides: kustomize resolves symlinks (macOS /var is one),
# so a relative path computed from logical paths could point nowhere.
render_dir="$(cd "${render_dir}" && pwd -P)"
overlay_rel="$(python3 -c 'import os, sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' "${overlay_dir}" "${render_dir}")"

# Kustomize matches images by the name they have *after* the overlay's own
# image mapping, so read that name from the overlay rather than assuming it.
overlay_image="$(kubectl kustomize "${overlay_dir}" | awk '$1 == "image:" { print $2; exit }')"
split_image "${overlay_image}"
overlay_repo="${image_repo}"
split_image "${image}"

{
  echo "apiVersion: kustomize.config.k8s.io/v1beta1"
  echo "kind: Kustomization"
  echo "resources:"
  echo "  - ${overlay_rel}"
  echo "images:"
  echo "  - name: ${overlay_repo}"
  echo "    newName: ${image_repo}"
  if [[ -n "${image_digest}" ]]; then
    echo "    digest: ${image_digest}"
  else
    echo "    newTag: ${image_tag}"
  fi
  echo "patches:"
  echo "  - target: {kind: Deployment, name: ${DEPLOYMENT}}"
  echo "    patch: |-"
  echo "      - op: add"
  echo "        path: /metadata/annotations"
  echo "        value: {kubernetes.io/change-cause: \"deploy ${image}\"}"
  if [[ -n "${pull_secret}" ]]; then
    echo "      - op: add"
    echo "        path: /spec/template/spec/imagePullSecrets"
    echo "        value: [{name: ${pull_secret}}]"
  fi
} >"${render_dir}/kustomization.yaml"

manifest="${render_dir}/manifest.yaml"
kubectl kustomize "${render_dir}" >"${manifest}"
rendered_image="$(awk '$1 == "image:" { print $2; exit }' "${manifest}")"
if [[ "${image}" != *@* && "${image##*/}" != *:* ]]; then
  expected_image="${image}:latest"
else
  expected_image="${image}"
fi
if [[ "${rendered_image}" != "${expected_image}" ]]; then
  echo "rendered image ${rendered_image} does not match requested ${expected_image}" >&2
  exit 1
fi

# --- 2. Apply and wait ------------------------------------------------------
revision() {
  kubectl --namespace "${namespace}" get deployment "${DEPLOYMENT}" \
    -o jsonpath='{.metadata.annotations.deployment\.kubernetes\.io/revision}' 2>/dev/null || true
}

previous_revision="$(revision)"
if [[ -n "${previous_revision}" ]]; then
  previous_image="$(kubectl --namespace "${namespace}" get deployment "${DEPLOYMENT}" \
    -o jsonpath='{.spec.template.spec.containers[0].image}')"
  log "${namespace}: currently at revision ${previous_revision} (${previous_image})"
else
  log "${namespace}: first deployment, nothing to roll back to"
fi

log "applying ${environment} overlay with ${image}"
kubectl apply -f "${manifest}"

# Changed pods get a new revision; an unchanged spec keeps the old one, in
# which case there is nothing new to verify and nothing to undo.
verify_release() {
  log "waiting for rollout (timeout ${timeout})"
  kubectl --namespace "${namespace}" rollout status "deployment/${DEPLOYMENT}" --timeout "${timeout}" || return 1
  log "running smoke tests"
  local args=(--namespace "${namespace}" --deployment "${DEPLOYMENT}" --expected-environment "${environment}")
  if [[ -n "${expect_version}" ]]; then args+=(--expected-version "${expect_version}"); fi
  if [[ "${basic_checks}" == true ]]; then args+=(--basic); fi
  "${SMOKE}" "${args[@]}"
}

if verify_release; then
  kubectl --namespace "${namespace}" rollout history "deployment/${DEPLOYMENT}"
  log "${environment} is running ${image}"
  exit 0
fi

# --- 3. Roll back -----------------------------------------------------------
echo "::error::release ${image} failed verification in ${namespace}"
kubectl --namespace "${namespace}" get pods -o wide || true

new_revision="$(revision)"
if [[ "${rollback}" != true ]]; then
  echo "rollback disabled; leaving the failed release in place" >&2
elif [[ -z "${previous_revision}" ]]; then
  echo "no earlier revision exists, so there is nothing to roll back to" >&2
elif [[ "${new_revision}" == "${previous_revision}" ]]; then
  echo "the running revision was not changed by this deploy; not rolling back" >&2
else
  "${ROOT}/scripts/rollback.sh" --namespace "${namespace}" --expected-environment "${environment}"
fi
exit 1
