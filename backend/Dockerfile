# syntax=docker/dockerfile:1
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /app

# curl is used by the compose healthcheck; libpq is pulled in by psycopg[binary].
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# The harvest directory must exist *in the image*, owned by appuser. Docker
# copies ownership from the image path only when it first initialises an empty
# named volume; if the path is missing at that moment the volume is created
# root-owned and the container — which runs unprivileged — cannot write a single
# mirrored document. That failure surfaces as every fetch reporting
# "unreachable", which reads like a dead corpus rather than a permission bug.
RUN chmod +x /app/entrypoint.sh \
    && adduser --disabled-password --gecos "" --uid 1000 appuser \
    && mkdir -p /app/staticfiles /app/data/harvest \
    && chown -R appuser:appuser /app

USER appuser

# The commit this image was built from, carried inside it.
#
# It exists because "did this container actually rebuild" had no answer here.
# The worker and beat images are built from this same context by their own
# compose services, so `docker compose build backend` leaves them untouched —
# and on 2026-08-09 they were found serving PROMPT_VERSION v1 against a v3
# backend, weeks late, with nothing reporting the drift. deploy/deploy.sh reads
# this variable back out of every running container and fails the deploy when
# one of them disagrees with the commit it just built.
#
# Deliberately the last layer that changes: everything above it stays cached
# across deploys, and what follows is metadata.
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--threads", "2", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
