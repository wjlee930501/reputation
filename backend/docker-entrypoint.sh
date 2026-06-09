#!/usr/bin/env bash
set -euo pipefail

SERVICE="${SERVICE:-api}"

case "$SERVICE" in
  api)
    exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
    ;;
  worker)
    # Cloud Run 서비스는 $PORT 리슨이 필수 — celery는 HTTP가 없으므로
    # 경량 헬스 서버를 사이드 프로세스로 띄운다 (없으면 revision ready 실패).
    python -m app.workers.health_server &
    exec celery -A app.core.celery_app worker \
      --loglevel=info \
      -Q default,content,sov,reports \
      -c "${CELERY_CONCURRENCY:-2}" \
      --max-tasks-per-child="${CELERY_MAX_TASKS_PER_CHILD:-50}"
    ;;
  beat)
    python -m app.workers.health_server &
    exec celery -A app.core.celery_app beat --loglevel=info
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  seed-admin)
    # Admin 콘솔 OWNER 계정 생성/로테이션 (AUTH-4) — Cloud Run Job으로 실행:
    #   gcloud run jobs deploy reputation-seed-admin --image=... \
    #     --set-env-vars=SERVICE=seed-admin,ADMIN_EMAIL=...,ADMIN_NAME=... \
    #     --set-secrets=ADMIN_PASSWORD=...,DB_PASSWORD=DB_PASSWORD:latest ...
    exec python -m app.utils.admin_user create-owner
    ;;
  *)
    echo "Unknown SERVICE: $SERVICE"
    echo "Valid values: api, worker, beat, migrate, seed-admin"
    exit 1
    ;;
esac
