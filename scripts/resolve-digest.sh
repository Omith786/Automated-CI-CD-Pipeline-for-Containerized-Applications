#!/usr/bin/env bash
# Print REPO@sha256:... for an image tag, or nothing if the tag does not exist.
#
# The pipeline uses this to find the release currently marked "staging" or
# "production", so it can recreate that environment before rolling the new
# release over it. On the very first run the tag is absent and the output is
# empty, which the caller treats as "nothing to roll back to".
#
# Usage: scripts/resolve-digest.sh ghcr.io/owner/app:staging
set -euo pipefail

ref="${1:?usage: $0 IMAGE:TAG}"
repo="${ref%:*}"

err_file="$(mktemp)"
trap 'rm -f "${err_file}"' EXIT
if ! inspect="$(docker buildx imagetools inspect "${ref}" 2>"${err_file}")"; then
  # A missing tag is expected on the first run; anything else (auth, network)
  # must not be mistaken for it, or the rollback target would silently vanish.
  if grep -qiE 'not found|manifest unknown' "${err_file}"; then
    echo "no image found for ${ref}" >&2
    exit 0
  fi
  cat "${err_file}" >&2
  exit 1
fi

digest="$(awk '$1 == "Digest:" { print $2; exit }' <<<"${inspect}")"
if [[ "${digest}" != sha256:* ]]; then
  echo "could not read a digest for ${ref}" >&2
  exit 1
fi
echo "${repo}@${digest}"
