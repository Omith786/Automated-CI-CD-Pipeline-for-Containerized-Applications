# Local mirror of the CI/CD pipeline. `make help` lists the targets.
#
# Targets in the first group need only Python and the downloaded tools
# (`make tools`); the rest need Docker, and the cluster targets also need kind
# and kubectl (`make tools-cluster` downloads both into .tools/bin).

SHELL := bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

# Use the project virtualenv when it exists; CI installs into its own Python.
PY := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
BOOTSTRAP_PYTHON ?= python3.12
TOOLS := .tools/bin
export PATH := $(CURDIR)/$(TOOLS):$(PATH)
# Keep Trivy's vulnerability database inside the (gitignored) tools directory.
export TRIVY_CACHE_DIR ?= $(CURDIR)/.tools/trivy-cache

IMAGE ?= distance-api
TAG ?= dev
ENV ?= staging
KIND_CLUSTER ?= pipeline
K8S_VERSION ?= 1.37.0
OVERLAYS := k8s/overlays/staging k8s/overlays/production

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ---- Setup -------------------------------------------------------------------

.venv/bin/python:
	$(BOOTSTRAP_PYTHON) -m venv .venv
	.venv/bin/pip install --upgrade pip

.PHONY: install
install: .venv/bin/python ## Create .venv and install pinned dev dependencies
	.venv/bin/pip install -r requirements-dev.txt

.PHONY: tools
tools: ## Download actionlint, hadolint, kubeconform, kustomize, shellcheck, trivy
	scripts/install-tools.sh

.PHONY: tools-cluster
tools-cluster: ## Download kind and kubectl
	scripts/install-tools.sh kind kubectl

# ---- CI stages (no Docker needed) ------------------------------------------------

.PHONY: lint
lint: ## Ruff lint and format check
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

.PHONY: format
format: ## Apply ruff formatting and safe fixes
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

.PHONY: typecheck
typecheck: ## mypy in strict mode
	$(PY) -m mypy

.PHONY: test
test: ## Unit tests with branch coverage (fails under 90%)
	$(PY) -m pytest --cov --cov-report=term-missing --cov-report=xml --cov-fail-under=90 \
		--junitxml=junit.xml

.PHONY: lint-dockerfile
lint-dockerfile: ## hadolint the Dockerfile
	hadolint Dockerfile

.PHONY: lint-shell
lint-shell: ## shellcheck the deployment scripts
	shellcheck scripts/*.sh

.PHONY: lint-workflows
lint-workflows: ## actionlint the GitHub Actions workflows
	actionlint .github/workflows/*.yml

.PHONY: validate-manifests
validate-manifests: ## Render each Kustomize overlay and validate it with kubeconform
	@for dir in k8s/base $(OVERLAYS); do \
		echo "--- $$dir"; \
		kustomize build "$$dir" | kubeconform -strict -summary -kubernetes-version $(K8S_VERSION) -; \
	done

.PHONY: scan-config
scan-config: ## Trivy misconfiguration scan of the rendered manifests and Dockerfile
	@rm -rf .rendered/iac && mkdir -p .rendered/iac
	@for dir in $(OVERLAYS); do kustomize build "$$dir" > ".rendered/iac/$$(basename $$dir).yaml"; done
	@cp Dockerfile .rendered/iac/
	trivy config --exit-code 1 --severity HIGH,CRITICAL .rendered/iac

.PHONY: ci
ci: lint typecheck test lint-dockerfile lint-shell lint-workflows validate-manifests scan-config ## Every CI check that runs without Docker

# ---- Container (needs Docker) --------------------------------------------------

.PHONY: image
image: ## Build the image as $(IMAGE):$(TAG)
	docker build --build-arg APP_VERSION=$(TAG) --build-arg VCS_REF=$$(git rev-parse --short HEAD 2>/dev/null || echo local) \
		-t $(IMAGE):$(TAG) .

.PHONY: container-smoke
container-smoke: ## Run the image hardened as in Kubernetes and smoke-test it
	docker rm -f distance-api-smoke >/dev/null 2>&1 || true
	docker run -d --name distance-api-smoke -p 8000:8000 --read-only --tmpfs /tmp \
		--cap-drop ALL --security-opt no-new-privileges -e APP_ENVIRONMENT=ci $(IMAGE):$(TAG)
	$(PY) scripts/smoke_test.py --base-url http://127.0.0.1:8000 --expected-version $(TAG) \
		--expected-environment ci || { docker logs distance-api-smoke; docker rm -f distance-api-smoke; exit 1; }
	docker rm -f distance-api-smoke

.PHONY: scan-image
scan-image: ## Trivy vulnerability scan (fails on fixable HIGH/CRITICAL)
	trivy image --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed $(IMAGE):$(TAG)

.PHONY: sbom
sbom: ## Write an SPDX SBOM for the image to sbom.spdx.json
	trivy image --format spdx-json --output sbom.spdx.json $(IMAGE):$(TAG)

.PHONY: compose-up
compose-up: ## Start the API (and Prometheus with PROFILE=observability) via docker compose
	docker compose $(if $(PROFILE),--profile $(PROFILE)) up --build -d

.PHONY: compose-down
compose-down: ## Stop the docker compose stack
	docker compose --profile observability down

# ---- Kubernetes with kind (needs Docker, kind, kubectl) ----------------------------

.PHONY: kind-up
kind-up: ## Create the local kind cluster
	kind get clusters | grep -qx $(KIND_CLUSTER) || kind create cluster --name $(KIND_CLUSTER) --config kind/cluster.yaml

.PHONY: kind-load
kind-load: ## Copy $(IMAGE):$(TAG) into the kind node (no registry needed)
	kind load docker-image $(IMAGE):$(TAG) --name $(KIND_CLUSTER)

.PHONY: deploy
deploy: ## Deploy $(IMAGE):$(TAG) to overlay $(ENV), smoke test, roll back on failure
	scripts/deploy.sh --overlay k8s/overlays/$(ENV) --image $(IMAGE):$(TAG) --expect-version $(TAG)

.PHONY: smoke
smoke: ## Smoke-test the pods currently running in $(ENV)
	scripts/smoke.sh --namespace $(ENV) --expected-environment $(ENV)

.PHONY: rollback
rollback: ## Roll $(ENV) back to its previous revision
	scripts/rollback.sh --namespace $(ENV) --expected-environment $(ENV)

.PHONY: kind-down
kind-down: ## Delete the local kind cluster
	kind delete cluster --name $(KIND_CLUSTER)

.PHONY: pipeline
pipeline: ci image container-smoke scan-image kind-up kind-load deploy ## The whole staging pipeline, locally

# ---- Housekeeping -----------------------------------------------------------------

.PHONY: clean
clean: ## Remove caches, reports and rendered manifests (keeps .venv and .tools)
	rm -rf .pytest_cache .mypy_cache .ruff_cache .rendered htmlcov .coverage coverage.xml junit.xml sbom.spdx.json
	find . -path ./.venv -prune -o -name __pycache__ -type d -exec rm -rf {} +
