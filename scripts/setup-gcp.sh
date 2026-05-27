#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# Re:putation — GCP 인프라 최초 셋업 (1회 실행)
#
# 실행 전:
#   1. gcloud auth login
#   2. gcloud config set project PROJECT_ID
#   3. GCP_PROJECT_ID 환경변수 설정 또는 gcloud 기본 프로젝트 지정
#
# 사용법: bash scripts/setup-gcp.sh
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'
info()  { echo -e "${BLUE}ℹ${RESET}  $1"; }
ok()    { echo -e "${GREEN}✓${RESET} $1"; }
fail()  { echo -e "${RED}✗${RESET} $1"; exit 1; }

PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || echo '')}"
REGION="${GCP_REGION:-us-central1}"
REPO="${GCP_ARTIFACT_REPO:-reputation}"
SA_NAME="${GCP_SERVICE_ACCOUNT_NAME:-reputation-sa}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

if [[ -z "$PROJECT_ID" ]]; then
  fail "GCP_PROJECT_ID 환경변수 또는 gcloud config set project를 설정해 주세요."
fi

info "프로젝트: ${PROJECT_ID} / 리전: ${REGION}"

# ─── 1. 필수 API 활성화 ────────────────────────────────────────────
info "API 활성화 중..."
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  sqladmin.googleapis.com \
  redis.googleapis.com \
  secretmanager.googleapis.com \
  cloudresourcemanager.googleapis.com \
  iamcredentials.googleapis.com \
  --project="$PROJECT_ID"
ok "API 활성화 완료"

# ─── 2. Artifact Registry 생성 ─────────────────────────────────────
info "Artifact Registry 저장소 생성 중..."
if gcloud artifacts repositories describe "$REPO" \
  --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
  ok "Artifact Registry 저장소 이미 존재: ${REPO}"
else
  gcloud artifacts repositories create "$REPO" \
    --repository-format=docker \
    --location="$REGION" \
    --project="$PROJECT_ID"
  ok "Artifact Registry 저장소 생성 완료: ${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}"
fi

# ─── 3. 서비스 계정 생성 ───────────────────────────────────────────
info "서비스 계정 생성 중..."
if gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" &>/dev/null; then
  ok "서비스 계정 이미 존재: ${SA_EMAIL}"
else
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name="Re:putation Service Account" \
    --project="$PROJECT_ID"
  ok "서비스 계정 생성 완료: ${SA_EMAIL}"
fi

# ─── 4. IAM 권한 부여 ──────────────────────────────────────────────
info "IAM 권한 부여 중..."

ROLES=(
  "roles/run.invoker"
  "roles/cloudsql.client"
  "roles/redis.editor"
  "roles/secretmanager.secretAccessor"
  "roles/storage.objectAdmin"
  "roles/aiplatform.user"
  "roles/logging.logWriter"
  "roles/monitoring.metricWriter"
  "roles/cloudtrace.agent"
  "roles/errorreporting.writer"
)

for role in "${ROLES[@]}"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$role" \
    --condition=None \
    --quiet
done
ok "IAM 권한 부여 완료"

# ─── 5. Secret Manager 시크릿 생성 (API 키) ───────────────────────
info "Secret Manager 설정 중..."

declare -A SECRETS=(
  ["ANTHROPIC_API_KEY"]="Anthropic API 키"
  ["OPENAI_API_KEY"]="OpenAI API 키"
  ["GEMINI_API_KEY"]="Gemini API 키"
  ["ADMIN_SECRET_KEY"]="Admin API 인증 키"
  ["SLACK_WEBHOOK_URL"]="Slack 웹훅 URL"
  ["ADMIN_LOGIN_PASSWORD"]="Admin 로그인 비밀번호"
  ["ADMIN_SESSION_SECRET"]="Admin 세션 서명키"
)

for secret_name in "${!SECRETS[@]}"; do
  if gcloud secrets describe "$secret_name" --project="$PROJECT_ID" &>/dev/null; then
    info "  ${secret_name}: 이미 존재"
  else
    gcloud secrets create "$secret_name" \
      --replication-policy="automatic" \
      --project="$PROJECT_ID"
    info "  ${secret_name}: 생성 완료 — 수동으로 값 입력 필요"
    echo "    gcloud secrets versions add ${secret_name} --data-file=- --project=${PROJECT_ID}"
  fi

  gcloud secrets add-iam-policy-binding "$secret_name" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="$PROJECT_ID" \
    --quiet 2>/dev/null || true
done
ok "Secret Manager 설정 완료"

# ─── 6. GCS 버킷 생성 (이미지 + 리포트) ────────────────────────────
info "Cloud Storage 버킷 생성 중..."
for bucket in "reputation-images" "reputation-reports"; do
  bucket_name="${bucket}-${PROJECT_ID}"
  if gsutil ls -b "gs://${bucket_name}" &>/dev/null 2>&1; then
    ok "  버킷 이미 존재: ${bucket_name}"
  else
    gcloud storage buckets create "gs://${bucket_name}" \
      --location="$REGION" \
      --uniform-bucket-level-access \
      --project="$PROJECT_ID"
    ok "  버킷 생성 완료: ${bucket_name}"
  fi
done

echo ""
echo -e "${GREEN}${BOLD}✅ GCP 인프라 셋업 완료${RESET}"
echo ""
echo "다음 단계:"
echo "  1. Secret Manager에 API 키 값 입력:"
for secret_name in "${!SECRETS[@]}"; do
  echo "     gcloud secrets versions add ${secret_name} --data-file=- --project=${PROJECT_ID} <<< 'YOUR_VALUE'"
done
echo ""
echo "  2. Cloud SQL 인스턴스 생성 (수동):"
echo "     https://console.cloud.google.com/sql"
echo "     → PostgreSQL 16, 리전: ${REGION}"
echo ""
echo "  3. Memorystore Redis 인스턴스 생성 (수동):"
echo "     https://console.cloud.google.com/memorystore"
echo "     → Redis 7, 리전: ${REGION}"
echo ""
echo "  4. .env.production 파일 작성:"
echo "     cp .env.example .env.production"
echo "     # DATABASE_URL, REDIS_URL, GCP_PROJECT_ID 등 채우기"
echo ""
echo "  5. 배포 실행:"
echo "     bash scripts/deploy.sh all"
