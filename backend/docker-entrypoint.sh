#!/usr/bin/env bash
set -euo pipefail

SERVICE="${SERVICE:-api}"

case "$SERVICE" in
  api)
    exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*"
    ;;
  worker)
    exec celery -A app.core.celery_app worker \
      --loglevel=info \
      -Q default,content,sov,reports \
      -c "${CELERY_CONCURRENCY:-2}" \
      --max-tasks-per-child="${CELERY_MAX_TASKS_PER_CHILD:-50}"
    ;;
  beat)
    exec celery -A app.core.celery_app beat --loglevel=info
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  *)
    echo "Unknown SERVICE: $SERVICE"
    echo "Valid values: api, worker, beat, migrate"
    exit 1
    ;;
esac
