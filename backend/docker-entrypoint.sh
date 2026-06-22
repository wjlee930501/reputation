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
  flower)
    exec celery -A app.core.celery_app flower \
      --port="${PORT:-5555}" \
      --basic-auth="${FLOWER_USER:-admin}:${FLOWER_PASSWORD:-changeme}"
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
  backfill-images)
    # 이미지 없이 seed된 발행 콘텐츠(image_url IS NULL)에 Imagen 대표 이미지 backfill —
    # Cloud Run Job으로 실행 (migrate Job에 --update-env-vars SERVICE=backfill-images).
    exec python -m app.utils.backfill_content_images
    ;;
  seed-colon-cluster)
    # 대장내시경 deep-format 콘텐츠 클러스터 seed (장편한외과) — Cloud Run Job.
    exec python -m app.utils.seed_colon_cluster
    ;;
  unpublish-flagged)
    # 품질 하네스 confirmed 위반 콘텐츠를 라이브에서 내림(→DRAFT) — Cloud Run Job.
    exec python -m app.utils.unpublish_items
    ;;
  fix-director-credential)
    # 원장 프로파일의 비존재 자격('대장내시경 세부전문의') 제거 — Cloud Run Job.
    exec python -m app.utils.fix_director_credential
    ;;
  *)
    echo "Unknown SERVICE: $SERVICE"
    echo "Valid values: api, worker, beat, flower, migrate, seed-admin, backfill-images, seed-colon-cluster, unpublish-flagged, fix-director-credential"
    exit 1
    ;;
esac
