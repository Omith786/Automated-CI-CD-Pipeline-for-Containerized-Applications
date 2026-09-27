# syntax=docker/dockerfile:1

# Both stages use the same base, pinned by digest so every build starts from
# identical bytes. Dependabot bumps the tag and digest together.

# ---- Build stage: resolve dependencies into an isolated virtualenv --------
FROM python:3.12.14-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Copy only the lock file first so the dependency layer stays cached until
# requirements.txt actually changes.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --require-virtualenv --only-binary=:all: -r /tmp/requirements.txt

# ---- Runtime stage: just the interpreter, the venv and the code -----------
FROM python:3.12.14-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS runtime

# A fixed numeric UID lets Kubernetes verify runAsNonRoot without a passwd lookup.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv
WORKDIR /srv
COPY --chown=root:root app ./app
# Compile bytecode at build time: the root filesystem is read-only at runtime.
RUN python -m compileall -q app

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_PORT=8000

# Build metadata comes last: these values change on every build, and anything
# after their first use is rebuilt, so keeping them here preserves the cache.
ARG APP_VERSION=0.0.0-dev
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="distance-api" \
      org.opencontainers.image.description="Great-circle distance service deployed by a containerised CI/CD pipeline" \
      org.opencontainers.image.source="https://github.com/Omith786/Automated-CI-CD-Pipeline-for-Containerized-Applications" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"

# The version is baked in so a running container can always say what it is;
# the smoke tests compare it with the tag that was deployed.
ENV APP_VERSION=${APP_VERSION}

USER 10001:10001
EXPOSE 8000

# Used by Docker and docker compose; Kubernetes ignores it and uses its own probes.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as r; r.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('APP_PORT', '8000'), timeout=2)"]

CMD ["python", "-m", "app"]
