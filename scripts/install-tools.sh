#!/usr/bin/env bash
# Download pinned versions of the pipeline's command-line tools into .tools/bin.
#
# Everything lands inside the repository (gitignored), so nothing is installed
# system-wide and the versions match the ones the CI workflows use.
#
# Usage: scripts/install-tools.sh [tool ...]
#   With no arguments, installs the validators needed by `make ci`:
#   actionlint hadolint kubeconform kustomize shellcheck trivy
#   Cluster tools (kind, kubectl) are installed only when named explicitly.
set -euo pipefail

ACTIONLINT_VERSION="1.7.12"
HADOLINT_VERSION="2.15.1"
KUBECONFORM_VERSION="0.8.0"
KUSTOMIZE_VERSION="5.8.1"
SHELLCHECK_VERSION="0.11.0"
TRIVY_VERSION="0.74.0"
KIND_VERSION="0.33.0"
KUBECTL_VERSION="1.37.1"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="${ROOT}/.tools/bin"
mkdir -p "${BIN}"

os="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "$(uname -m)" in
  x86_64 | amd64) arch="amd64" ;;
  arm64 | aarch64) arch="arm64" ;;
  *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

fetch() {
  # fetch <url> <destination>
  echo "  downloading $1"
  curl --fail --silent --show-error --location --retry 3 --output "$2" "$1"
}

install_actionlint() {
  fetch "https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_${os}_${arch}.tar.gz" "${tmp}/actionlint.tgz"
  tar -xzf "${tmp}/actionlint.tgz" -C "${tmp}" actionlint
  install -m 0755 "${tmp}/actionlint" "${BIN}/actionlint"
}

install_hadolint() {
  local h_os h_arch
  case "${os}" in
    darwin) h_os="macos" ;;
    linux) h_os="linux" ;;
  esac
  case "${arch}" in
    amd64) h_arch="x86_64" ;;
    arm64) h_arch="arm64" ;;
  esac
  fetch "https://github.com/hadolint/hadolint/releases/download/v${HADOLINT_VERSION}/hadolint-${h_os}-${h_arch}" "${tmp}/hadolint"
  install -m 0755 "${tmp}/hadolint" "${BIN}/hadolint"
}

install_kubeconform() {
  fetch "https://github.com/yannh/kubeconform/releases/download/v${KUBECONFORM_VERSION}/kubeconform-${os}-${arch}.tar.gz" "${tmp}/kubeconform.tgz"
  tar -xzf "${tmp}/kubeconform.tgz" -C "${tmp}" kubeconform
  install -m 0755 "${tmp}/kubeconform" "${BIN}/kubeconform"
}

install_kustomize() {
  fetch "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_${os}_${arch}.tar.gz" "${tmp}/kustomize.tgz"
  tar -xzf "${tmp}/kustomize.tgz" -C "${tmp}" kustomize
  install -m 0755 "${tmp}/kustomize" "${BIN}/kustomize"
}

install_shellcheck() {
  local sc_arch
  case "${arch}" in
    amd64) sc_arch="x86_64" ;;
    arm64) sc_arch="aarch64" ;;
  esac
  fetch "https://github.com/koalaman/shellcheck/releases/download/v${SHELLCHECK_VERSION}/shellcheck-v${SHELLCHECK_VERSION}.${os}.${sc_arch}.tar.xz" "${tmp}/shellcheck.txz"
  tar -xJf "${tmp}/shellcheck.txz" -C "${tmp}"
  install -m 0755 "${tmp}/shellcheck-v${SHELLCHECK_VERSION}/shellcheck" "${BIN}/shellcheck"
}

install_trivy() {
  local t_os t_arch
  case "${os}" in
    darwin) t_os="macOS" ;;
    linux) t_os="Linux" ;;
  esac
  case "${arch}" in
    amd64) t_arch="64bit" ;;
    arm64) t_arch="ARM64" ;;
  esac
  fetch "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_${t_os}-${t_arch}.tar.gz" "${tmp}/trivy.tgz"
  tar -xzf "${tmp}/trivy.tgz" -C "${tmp}" trivy
  install -m 0755 "${tmp}/trivy" "${BIN}/trivy"
}

install_kind() {
  fetch "https://github.com/kubernetes-sigs/kind/releases/download/v${KIND_VERSION}/kind-${os}-${arch}" "${tmp}/kind"
  install -m 0755 "${tmp}/kind" "${BIN}/kind"
}

install_kubectl() {
  fetch "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/${os}/${arch}/kubectl" "${tmp}/kubectl"
  install -m 0755 "${tmp}/kubectl" "${BIN}/kubectl"
}

tools=("$@")
if [[ ${#tools[@]} -eq 0 ]]; then
  tools=(actionlint hadolint kubeconform kustomize shellcheck trivy)
fi

for tool in "${tools[@]}"; do
  case "${tool}" in
    actionlint | hadolint | kubeconform | kustomize | shellcheck | trivy | kind | kubectl)
      echo "installing ${tool}"
      "install_${tool}"
      ;;
    *)
      echo "unknown tool: ${tool}" >&2
      exit 1
      ;;
  esac
done

echo "tools installed in ${BIN}"
