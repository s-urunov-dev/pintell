#!/usr/bin/env bash
# Container start-up: wait for the database, migrate, prepare static files,
# optionally create the admin user, queue a first sync, then exec the CMD.
set -euo pipefail

ROLE="${CONTAINER_ROLE:-web}"

echo "[entrypoint] role=${ROLE}"
python manage.py wait_for_db --timeout 90

if [ "${ROLE}" = "web" ]; then
  echo "[entrypoint] applying migrations…"
  python manage.py migrate --noinput

  echo "[entrypoint] collecting static files…"
  python manage.py collectstatic --noinput --clear >/dev/null

  # Never fatal: the admin user is a convenience, not a requirement.
  python manage.py create_admin || echo "[entrypoint] admin bootstrap skipped"

  # Both Qdrant collections, so "empty" and "absent" stop looking the same
  # from the console. The command already swallows an unreachable store and
  # exits 0 — the `||` is belt and braces, because the semantic index is a
  # cache (D43) and a container that serves tenders must start without it.
  python manage.py init_qdrant_collections || echo "[entrypoint] qdrant init skipped"

  # Fire the first sync immediately so the UI is not empty on first boot.
  python manage.py bootstrap_sync || echo "[entrypoint] initial sync not queued"
fi

echo "[entrypoint] starting: $*"
exec "$@"
