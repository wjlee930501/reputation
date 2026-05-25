#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# Re:putation — GCP Cloud Run 로컬 빌드 & 배포
#
# 사용법:
#   bash scripts/deploy.sh api          # API 서비스만 배포
#   bash scripts/deploy.sh worker       # Worker 서비스만 배포
#   bash scripts/deploy.sh beat         # Beat 서비스만 배포
#   bash scripts/deploy.sh all          # 전체 서비스 배포
#   bash scripts/deploy.sh migrate      # DB 마이그레이션 실행
#
# 사전 준비:
#   1. gcloud CLI 설치 + 로그인 (gcloud auth login)
#   2. GCP 프로젝트 설정 (gcloud config set project PROJECT_ID)
#   3. Artifact Registry 생성 (scripts/setup-gcp.sh 로 자동화)
#   4. .env.production 파일 작성 (API 키 등)
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'
info()  { echo -e "${BLUE}ℹ${RESET}  $1"; }
ok()    { echo -e "${GREEN}✓${RESET} $1"; }
fail()  { echo -e "${RED}✗${RESET} $1"; exit 1; }

# ─── 설정 ─────────────────────────────────────────────────────────
TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  echo "Usage: bash scripts/deploy.sh [api|worker|beat|all|migrate]"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_ROOT}/.env.production"

PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || echo '')}"
REGION="${GCP_REGION:-us-central1}"
REPO="${GCP_ARTIFACT_REPO:-reputation}"
IMAGE_TAG="$(date +%Y%m%d-%H%M%S)"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/reputation"

SERVICE_ACCOUNT="${GCP_SERVICE_ACCOUNT:-reputation-sa@${PROJECT_ID}.iam.gserviceaccount.com}"

# Cloud Run 서비스 설정
API_MEMORY="${API_MEMORY:-512Mi}"
API_CPU="${API_CPU:-1}"
API_MIN="${API_MIN:-0}"
API_MAX="${API_MAX:-10}"
API_CONCURRENCY="${API_CONCURRENCY:-80}"

WORKER_MEMORY="${WORKER_MEMORY:-1Gi}"
WORKER_CPU="${WORKER_CPU:-1}"
WORKER_MIN="${WORKER_MIN:-1}"
WORKER_MAX="${WORKER_MAX:-5}"
WORKER_CONCURRENCY="${WORKER_CONCURRENCY:-1}"  # Celery worker는 1 concurrency 권장

BEAT_MEMORY="${BEAT_MEMORY:-256Mi}"
BEAT_CPU="${BEAT_CPU:-1}"
BEAT_MIN="${BEAT_MIN:-0}"     # beat는 항상 1개만
BEAT_MAX="${BEAT_MAX:-1}"

# ─── 사전 검증 ─────────────────────────────────────────────────────
info "사전 검증 중..."

command -v gcloud >/dev/null 2>&1 || fail "gcloud CLI가 설치되지 않았습니다."
command -v docker >/dev/null 2>&1 || fail "Docker가 설치되지 않았습니다."

if [[ -z "$PROJECT_ID" ]]; then
  fail "GCP_PROJECT_ID 환경변수 또는 gcloud config를 설정해 주세요."
fi

if [[ "$TARGET" != "migrate" ]]; then
  if [[ ! -f "$ENV_FILE" ]]; then
    fail ".env.production 파일이 없습니다. .env.example을 복사해서 작성해 주세요."
  fi
fi

ok "사전 검증 통과 (프로젝트: ${PROJECT_ID}, 리전: ${REGION})"

# ─── Docker 빌드 & 푸시 ────────────────────────────────────────────
build_and_push() {
  local image_url="${IMAGE_BASE}:${IMAGE_TAG}"
  info "Docker 이미지 빌드 중..."
  docker build \
    --platform linux/amd64 \
    -t "$image_url" \
    -f "${PROJECT_ROOT}/backend/Dockerfile" \
    "${PROJECT_ROOT}/backend"

  info "Artifact Registry에 푸시 중..."
  docker push "$image_url"

  ok "이미지 푸시 완료: ${image_url}"
  echo "$image_url"
}

# ─── Cloud Run 배포 ────────────────────────────────────────────────
deploy_api() {
  local image_url="$1"
  info "API 서비스 배포 중..."

  gcloud run deploy reputation-api \
    --image="$image_url" \
    --region="$REGION" \
    --platform=managed \
    --service-account="$SERVICE_ACCOUNT" \
    --memory="$API_MEMORY" \
    --cpu="$API_CPU" \
    --min-instances="$API_MIN" \
    --max-instances="$API_MAX" \
    --concurrency="$API_CONCURRENCY" \
    --allow-unauthenticated \
    --set-env-vars="SERVICE=api,APP_ENV=production" \
    --env-vars-file=<(grep -v '^#' "$ENV_FILE" | grep -v '^$' | sed 's/export //') \
    --port=8000 \
    --timeout=300 \
    --no-cpu-throttling \
    --cpu-boost \
    --execution-environment=gen2

  ok "API 배포 완료"
}

deploy_worker() {
  local image_url="$1"
  info "Worker 서비스 배포 중..."

  gcloud run deploy reputation-worker \
    --image="$image_url" \
    --region="$REGION" \
    --platform=managed \
    --service-account="$SERVICE_ACCOUNT" \
    --memory="$WORKER_MEMORY" \
    --cpu="$WORKER_CPU" \
    --min-instances="$WORKER_MIN" \
    --max-instances="$WORKER_MAX" \
    --concurrency="$WORKER_CONCURRENCY" \
    --no-allow-unauthenticated \
    --set-env-vars="SERVICE=worker,APP_ENV=production" \
    --env-vars-file=<(grep -v '^#' "$ENV_FILE" | grep -v '^$' | sed 's/export //') \
    --timeout=900 \
    --no-cpu-throttling

  ok "Worker 배포 완료"
}

deploy_beat() {
  local image_url="$1"
  info "Beat 서비스 배포 중..."

  gcloud run deploy reputation-beat \
    --image="$image_url" \
    --region="$REGION" \
    --platform=managed \
    --service-account="$SERVICE_ACCOUNT" \
    --memory="$BEAT_MEMORY" \
    --cpu="$BEAT_CPU" \
    --min-instances="$BEAT_MIN" \
    --max-instances="$BEAT_MAX" \
    --concurrency=1 \
    --no-allow-unauthenticated \
    --set-env-vars="SERVICE=beat,APP_ENV=production" \
    --env-vars-file=<(grep -v '^#' "$ENV_FILE" | grep -v '^$' | sed 's/export //') \
    --timeout=86400 \
    --no-cpu-throttling

  ok "Beat 배포 완료"
}

run_migration() {
  info "DB 마이그레이션 실행 중..."

  gcloud run jobs create reputation-migrate \
    --image="${IMAGE_BASE}:${IMAGE_TAG}" \
    --region="$REGION" \
    --service-account="$SERVICE_ACCOUNT" \
    --set-env-vars="SERVICE=migrate,APP_ENV=production" \
    --env-vars-file=<(grep -v '^#' "$ENV_FILE" | grep -v '^$' | sed 's/export //') \
    --task-timeout=300 \
    --max-retries=1 \
    2>/dev/null || gcloud run jobs update reputation-migrate \
    --image="${IMAGE_BASE}:${IMAGE_TAG}" \
    --region="$REGION" \
    --set-env-vars="SERVICE=migrate,APP_ENV=production" \
    --env-vars-file=<(grep -v '^#' "$ENV_FILE" | grep -v '^$' | sed 's/export //')

  gcloud run jobs execute reputation-migrate --region="$REGION" --wait

  ok "마이그레이션 완료"
}

# ─── 메인 ──────────────────────────────────────────────────────────
case "$TARGET" in
  api|worker|beat)
    IMAGE_URL=$(build_and_push)
    "deploy_${TARGET}" "$IMAGE_URL"
    ;;
  all)
    IMAGE_URL=$(build_and_push)
    deploy_api "$IMAGE_URL"
    deploy_worker "$IMAGE_URL"
    deploy_beat "$IMAGE_URL"
    ;;
  migrate)
    IMAGE_URL=$(build_and_push)
    run_migration
    ;;
  *)
    fail "알 수 없는 대상: $TARGET (api, worker, beat, all, migrate 중 하나)"
    ;;
esac

echo ""
echo -e "${GREEN}${BOLD}✅ 배포 완료${RESET}"
echo "   API URL: $(gcloud run services describe reputation-api --region="$REGION" --format='value(status.url)' 2>/dev/null || echo '확인 중...')"
