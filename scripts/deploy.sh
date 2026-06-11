#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# Re:putation — GCP Cloud Run 로컬 빌드 & 배포
#
# 사용법:
#   bash scripts/deploy.sh api          # API 서비스만 배포
#   bash scripts/deploy.sh worker       # Worker 서비스만 배포
#   bash scripts/deploy.sh beat         # Beat 서비스만 배포
#   bash scripts/deploy.sh site         # 공개 site (Next.js) 배포
#   bash scripts/deploy.sh admin       # admin 콘솔 (Next.js) 배포
#   bash scripts/deploy.sh all          # 마이그레이션 → backend 3 → site → admin
#   bash scripts/deploy.sh migrate      # DB 마이그레이션 실행
#
# site/admin 배포에는 도메인 env가 필요하다 (NEXT_PUBLIC_* 빌드 인라인용):
#   PUBLIC_DOMAIN=reputation.co.kr ADMIN_DOMAIN=admin.reputation.co.kr \
#     bash scripts/deploy.sh site
#
# 사전 준비:
#   1. gcloud CLI 설치 + 로그인 (gcloud auth login)
#   2. GCP 프로젝트 설정 (gcloud config set project PROJECT_ID)
#   3. Artifact Registry 생성 (scripts/setup-gcp.sh 로 자동화)
#   4. .env.production 파일 작성 (API 키 등)
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'
# 로그는 전부 stderr로 — build_and_push 류 함수가 command substitution으로
# 캡처될 때 이미지 URL(stdout) 외의 출력이 섞이지 않도록 한다.
info()  { echo -e "${BLUE}ℹ${RESET}  $1" >&2; }
ok()    { echo -e "${GREEN}✓${RESET} $1" >&2; }
fail()  { echo -e "${RED}✗${RESET} $1" >&2; exit 1; }

# ─── 설정 ─────────────────────────────────────────────────────────
TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  echo "Usage: bash scripts/deploy.sh [api|worker|beat|site|admin|all|migrate]"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_ROOT}/.env.production"
NON_SECRET_ENV_FILE=""
SERVICE_ENV_FILE=""
TEMP_ENV_FILES=()

PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || echo '')}"
REGION="${GCP_REGION:-us-central1}"
REPO="${GCP_ARTIFACT_REPO:-reputation}"
IMAGE_TAG="$(date +%Y%m%d-%H%M%S)"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/reputation"

SERVICE_ACCOUNT="${GCP_SERVICE_ACCOUNT:-reputation-sa@${PROJECT_ID}.iam.gserviceaccount.com}"
ALLOW_PLAINTEXT_ENV_SECRETS="${ALLOW_PLAINTEXT_ENV_SECRETS:-0}"

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
BEAT_MIN="${BEAT_MIN:-1}"     # beat는 항상 1개만
BEAT_MAX="${BEAT_MAX:-1}"

# Frontend (Next.js) — terraform/cloudrun_frontend.tf 기본값과 동일하게 유지
FRONTEND_SERVICE_ACCOUNT="${FRONTEND_SERVICE_ACCOUNT:-reputation-frontend-sa@${PROJECT_ID}.iam.gserviceaccount.com}"
PUBLIC_DOMAIN="${PUBLIC_DOMAIN:-}"   # 예: reputation.co.kr
ADMIN_DOMAIN="${ADMIN_DOMAIN:-}"     # 예: admin.reputation.co.kr
SITE_MEMORY="${SITE_MEMORY:-512Mi}"
SITE_MIN="${SITE_MIN:-0}"
# site max=1 기본: on-demand ISR revalidate가 단일 인스턴스 캐시만 비우기 때문
# (terraform variables.tf site_max_instances 설명 참조).
SITE_MAX="${SITE_MAX:-1}"
ADMIN_MEMORY="${ADMIN_MEMORY:-512Mi}"
ADMIN_MIN="${ADMIN_MIN:-0}"
ADMIN_MAX="${ADMIN_MAX:-2}"

REQUIRED_SECRET_NAMES=(
  "ANTHROPIC_API_KEY"
  "OPENAI_API_KEY"
  "GEMINI_API_KEY"
  "SLACK_WEBHOOK_URL"
  "ADMIN_SECRET_KEY"
  "ADMIN_SESSION_SECRET"
  "DB_PASSWORD"
)

OPTIONAL_SECRET_NAMES=(
  "SITE_REVALIDATE_SECRET"
)

cleanup() {
  local f
  for f in "${TEMP_ENV_FILES[@]:-}"; do
    [[ -n "$f" && -f "$f" ]] && rm -f "$f"
  done
}
trap cleanup EXIT

# ─── 사전 검증 ─────────────────────────────────────────────────────
info "사전 검증 중..."

command -v gcloud >/dev/null 2>&1 || fail "gcloud CLI가 설치되지 않았습니다."
command -v docker >/dev/null 2>&1 || fail "Docker가 설치되지 않았습니다."

if [[ -z "$PROJECT_ID" ]]; then
  fail "GCP_PROJECT_ID 환경변수 또는 gcloud config를 설정해 주세요."
fi

if [[ ! -f "$ENV_FILE" ]]; then
  fail ".env.production 파일이 없습니다. .env.example을 복사해서 작성해 주세요."
fi

ok "사전 검증 통과 (프로젝트: ${PROJECT_ID}, 리전: ${REGION})"

# Cloud SQL 연결명 — migrate Job이 /cloudsql/<conn> unix socket으로 DB에 접근하려면
# --set-cloudsql-instances가 필수다 (없으면 마이그레이션이 DB에 닿지 못한다).
# 우선순위: 환경변수 → .env.production.
read_env_file_value() {
  local key="$1"
  grep -E "^(export )?${key}=" "$ENV_FILE" 2>/dev/null | tail -n1 \
    | sed -e 's/^export //' -e "s/^${key}=//" -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'\$//"
}
CLOUDSQL_CONNECTION="${CLOUD_SQL_CONNECTION_NAME:-$(read_env_file_value CLOUD_SQL_CONNECTION_NAME || true)}"

is_managed_secret() {
  local key="$1"
  local name
  for name in "${REQUIRED_SECRET_NAMES[@]}" "${OPTIONAL_SECRET_NAMES[@]}"; do
    [[ "$key" == "$name" ]] && return 0
  done
  return 1
}

# 이름이 unsafe 패턴(WEBHOOK 등)에 걸리지만 실제로는 비밀이 아닌 설정 키.
SAFE_ENV_KEYS=("SLACK_WEBHOOK_ALLOWED_HOSTS")
is_safe_env_key() {
  local key="$1" name
  for name in "${SAFE_ENV_KEYS[@]}"; do
    [[ "$key" == "$name" ]] && return 0
  done
  return 1
}

# dotenv KEY=value 한 쌍을 gcloud --env-vars-file이 요구하는 YAML(KEY: "value")
# 형식으로 변환해 append. 값의 둘러싼 따옴표는 벗기고, YAML 문자열로 안전하게
# 백슬래시/쌍따옴표를 이스케이프한다.
append_env_yaml() {
  local file="$1" key="$2" value="$3"
  if [[ "$value" == \"*\" && "$value" == *\" && ${#value} -ge 2 ]]; then
    value="${value#\"}"; value="${value%\"}"
  elif [[ "$value" == \'*\' && "$value" == *\' && ${#value} -ge 2 ]]; then
    value="${value#\'}"; value="${value%\'}"
  fi
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '%s: "%s"\n' "$key" "$value" >> "$file"
}

prepare_non_secret_env_file() {
  NON_SECRET_ENV_FILE="$(mktemp)"
  TEMP_ENV_FILES+=("$NON_SECRET_ENV_FILE")
  local unsafe_keys=()
  local unsafe_values=()
  local line key value

  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    line="${line#export }"
    [[ "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"

    if is_managed_secret "$key"; then
      continue
    fi

    # SERVICE/APP_ENV는 서비스별로 따로 주입한다 (YAML 중복 키 방지).
    if [[ "$key" == "SERVICE" || "$key" == "APP_ENV" ]]; then
      continue
    fi

    # 빈 값은 전달하지 않는다 (config.py 기본값 사용) — SITE_BFF_SECRET= 같은
    # 빈 placeholder가 unsafe 검사에 걸리는 것도 막는다.
    if [[ -z "$value" ]]; then
      continue
    fi

    if [[ "$key" =~ (SECRET|PASSWORD|TOKEN|PRIVATE_KEY|API_KEY|WEBHOOK) ]] && ! is_safe_env_key "$key"; then
      unsafe_keys+=("$key")
      unsafe_values+=("$value")
      continue
    fi

    append_env_yaml "$NON_SECRET_ENV_FILE" "$key" "$value"
  done < "$ENV_FILE"

  if (( ${#unsafe_keys[@]} > 0 )) && [[ "$ALLOW_PLAINTEXT_ENV_SECRETS" != "1" ]]; then
    fail "Unsafe plaintext env secrets in .env.production: ${unsafe_keys[*]}. Store them in Secret Manager or set ALLOW_PLAINTEXT_ENV_SECRETS=1 to accept Cloud Run plaintext env storage."
  fi

  if (( ${#unsafe_keys[@]} > 0 )); then
    info "ALLOW_PLAINTEXT_ENV_SECRETS=1 set; passing unsafe plaintext env keys: ${unsafe_keys[*]}"
    local i
    for i in "${!unsafe_keys[@]}"; do
      append_env_yaml "$NON_SECRET_ENV_FILE" "${unsafe_keys[$i]}" "${unsafe_values[$i]}"
    done
  fi
}

# 서비스별 env-vars-file 생성 — 공통 non-secret YAML + SERVICE/APP_ENV.
# (gcloud는 --set-env-vars와 --env-vars-file을 동시에 받지 않으므로
#  모든 env를 단일 YAML 파일로 합쳐 전달한다.)
make_service_env_file() {
  local service="$1"
  SERVICE_ENV_FILE="$(mktemp)"
  TEMP_ENV_FILES+=("$SERVICE_ENV_FILE")
  cat "$NON_SECRET_ENV_FILE" > "$SERVICE_ENV_FILE"
  printf 'SERVICE: "%s"\nAPP_ENV: "production"\n' "$service" >> "$SERVICE_ENV_FILE"
}

build_secret_args() {
  SECRET_ARGS=()
  local name

  for name in "${REQUIRED_SECRET_NAMES[@]}"; do
    if ! gcloud secrets describe "$name" --project="$PROJECT_ID" >/dev/null 2>&1; then
      fail "Secret Manager secret ${name} is missing. Create it before deploying so secrets are not passed as plaintext env vars."
    fi
    SECRET_ARGS+=("--set-secrets=${name}=${name}:latest")
  done

  for name in "${OPTIONAL_SECRET_NAMES[@]}"; do
    if gcloud secrets describe "$name" --project="$PROJECT_ID" >/dev/null 2>&1; then
      SECRET_ARGS+=("--set-secrets=${name}=${name}:latest")
    fi
  done
}

prepare_non_secret_env_file
build_secret_args

# ─── Docker 빌드 & 푸시 ────────────────────────────────────────────
# 주의: command substitution으로 호출되므로 stdout에는 이미지 URL만 출력한다.
build_and_push() {
  local image_url="${IMAGE_BASE}:${IMAGE_TAG}"
  info "Docker 이미지 빌드 중..."
  docker build \
    --platform linux/amd64 \
    -t "$image_url" \
    -f "${PROJECT_ROOT}/backend/Dockerfile" \
    "${PROJECT_ROOT}/backend" >&2

  info "Artifact Registry에 푸시 중..."
  docker push "$image_url" >&2

  ok "이미지 푸시 완료: ${image_url}"
  echo "$image_url"
}

# ─── Cloud Run 배포 ────────────────────────────────────────────────
deploy_api() {
  local image_url="$1"
  info "API 서비스 배포 중..."
  make_service_env_file "api"

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
    --ingress=internal-and-cloud-load-balancing \
    --allow-unauthenticated \
    --env-vars-file="$SERVICE_ENV_FILE" \
    "${SECRET_ARGS[@]}" \
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
  make_service_env_file "worker"

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
    --ingress=internal \
    --no-allow-unauthenticated \
    --env-vars-file="$SERVICE_ENV_FILE" \
    "${SECRET_ARGS[@]}" \
    --timeout=900 \
    --no-cpu-throttling

  ok "Worker 배포 완료"
}

deploy_beat() {
  local image_url="$1"
  info "Beat 서비스 배포 중..."
  make_service_env_file "beat"

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
    --ingress=internal \
    --no-allow-unauthenticated \
    --env-vars-file="$SERVICE_ENV_FILE" \
    "${SECRET_ARGS[@]}" \
    --timeout=86400 \
    --no-cpu-throttling

  ok "Beat 배포 완료"
}

require_public_domain() {
  if [[ -z "$PUBLIC_DOMAIN" ]]; then
    fail "PUBLIC_DOMAIN 환경변수가 필요합니다 (예: PUBLIC_DOMAIN=reputation.co.kr). NEXT_PUBLIC_* 값이 빌드 시점에 번들로 인라인되기 때문입니다."
  fi
}

build_and_push_site() {
  require_public_domain
  local image_url="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/site:${IMAGE_TAG}"
  info "Site 이미지 빌드 중 (NEXT_PUBLIC_* 인라인: https://${PUBLIC_DOMAIN})..."
  docker build \
    --platform linux/amd64 \
    --build-arg "NEXT_PUBLIC_API_URL=https://${PUBLIC_DOMAIN}/api/v1/public" \
    --build-arg "NEXT_PUBLIC_SITE_URL=https://${PUBLIC_DOMAIN}" \
    --build-arg "NEXT_PUBLIC_BACKEND_URL=https://${PUBLIC_DOMAIN}" \
    -t "$image_url" \
    -f "${PROJECT_ROOT}/site/Dockerfile" \
    "${PROJECT_ROOT}/site" >&2
  docker push "$image_url" >&2
  ok "Site 이미지 푸시 완료: ${image_url}"
  echo "$image_url"
}

build_and_push_admin() {
  require_public_domain
  local image_url="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/admin:${IMAGE_TAG}"
  info "Admin 이미지 빌드 중..."
  docker build \
    --platform linux/amd64 \
    --build-arg "NEXT_PUBLIC_BACKEND_URL=https://${PUBLIC_DOMAIN}" \
    -t "$image_url" \
    -f "${PROJECT_ROOT}/admin/Dockerfile" \
    "${PROJECT_ROOT}/admin" >&2
  docker push "$image_url" >&2
  ok "Admin 이미지 푸시 완료: ${image_url}"
  echo "$image_url"
}

deploy_site() {
  local image_url="$1"
  info "Site 서비스 배포 중..."

  gcloud run deploy reputation-site \
    --image="$image_url" \
    --region="$REGION" \
    --platform=managed \
    --service-account="$FRONTEND_SERVICE_ACCOUNT" \
    --memory="$SITE_MEMORY" \
    --min-instances="$SITE_MIN" \
    --max-instances="$SITE_MAX" \
    --ingress=internal-and-cloud-load-balancing \
    --allow-unauthenticated \
    --set-env-vars="NEXT_PUBLIC_API_URL=https://${PUBLIC_DOMAIN}/api/v1/public,NEXT_PUBLIC_SITE_URL=https://${PUBLIC_DOMAIN},NEXT_PUBLIC_BACKEND_URL=https://${PUBLIC_DOMAIN},BACKEND_URL=https://${PUBLIC_DOMAIN}" \
    --set-secrets="SITE_REVALIDATE_SECRET=SITE_REVALIDATE_SECRET:latest,SITE_BFF_SECRET=SITE_BFF_SECRET:latest" \
    --port=8080 \
    --timeout=60 \
    --cpu-boost

  ok "Site 배포 완료"
}

deploy_admin() {
  local image_url="$1"
  if [[ -z "$ADMIN_DOMAIN" ]]; then
    info "ADMIN_DOMAIN 미설정 — admin은 LB 호스트 라우팅 없이 배포됩니다."
  fi
  info "Admin 서비스 배포 중..."

  gcloud run deploy reputation-admin \
    --image="$image_url" \
    --region="$REGION" \
    --platform=managed \
    --service-account="$FRONTEND_SERVICE_ACCOUNT" \
    --memory="$ADMIN_MEMORY" \
    --min-instances="$ADMIN_MIN" \
    --max-instances="$ADMIN_MAX" \
    --ingress=internal-and-cloud-load-balancing \
    --allow-unauthenticated \
    --set-env-vars="BACKEND_URL=https://${PUBLIC_DOMAIN},NEXT_PUBLIC_BACKEND_URL=https://${PUBLIC_DOMAIN}" \
    --set-secrets="ADMIN_SESSION_SECRET=ADMIN_SESSION_SECRET:latest,ADMIN_SECRET_KEY=ADMIN_SECRET_KEY:latest" \
    --port=8080 \
    --timeout=60 \
    --cpu-boost

  ok "Admin 배포 완료"
}

run_migration() {
  local image_url="$1"
  info "DB 마이그레이션 실행 중..."

  # 프로덕션 DATABASE_URL은 /cloudsql/<connection_name> unix socket을 쓰므로
  # Cloud SQL 연결을 Job에 붙이지 않으면 마이그레이션이 DB에 접근할 수 없다.
  if [[ -z "$CLOUDSQL_CONNECTION" ]]; then
    fail "CLOUD_SQL_CONNECTION_NAME이 필요합니다 (.env.production 또는 환경변수). 예: my-project:us-central1:reputation-db"
  fi

  make_service_env_file "migrate"

  gcloud run jobs create reputation-migrate \
    --image="$image_url" \
    --region="$REGION" \
    --service-account="$SERVICE_ACCOUNT" \
    --env-vars-file="$SERVICE_ENV_FILE" \
    "${SECRET_ARGS[@]}" \
    --set-cloudsql-instances="$CLOUDSQL_CONNECTION" \
    --task-timeout=300 \
    --max-retries=1 \
    2>/dev/null || gcloud run jobs update reputation-migrate \
    --image="$image_url" \
    --region="$REGION" \
    --env-vars-file="$SERVICE_ENV_FILE" \
    "${SECRET_ARGS[@]}" \
    --set-cloudsql-instances="$CLOUDSQL_CONNECTION"

  gcloud run jobs execute reputation-migrate --region="$REGION" --wait

  ok "마이그레이션 완료"
}

# ─── 메인 ──────────────────────────────────────────────────────────
case "$TARGET" in
  api|worker|beat)
    IMAGE_URL=$(build_and_push)
    "deploy_${TARGET}" "$IMAGE_URL"
    ;;
  site)
    SITE_IMAGE_URL=$(build_and_push_site)
    deploy_site "$SITE_IMAGE_URL"
    ;;
  admin)
    ADMIN_IMAGE_URL=$(build_and_push_admin)
    deploy_admin "$ADMIN_IMAGE_URL"
    ;;
  all)
    IMAGE_URL=$(build_and_push)
    # 마이그레이션을 새 코드 배포보다 먼저 실행 — 새 리비전이 옛 스키마 위에서
    # 기동하는 시간을 없앤다 (additive migration 전제).
    run_migration "$IMAGE_URL"
    deploy_api "$IMAGE_URL"
    deploy_worker "$IMAGE_URL"
    deploy_beat "$IMAGE_URL"
    SITE_IMAGE_URL=$(build_and_push_site)
    deploy_site "$SITE_IMAGE_URL"
    ADMIN_IMAGE_URL=$(build_and_push_admin)
    deploy_admin "$ADMIN_IMAGE_URL"
    ;;
  migrate)
    IMAGE_URL=$(build_and_push)
    run_migration "$IMAGE_URL"
    ;;
  *)
    fail "알 수 없는 대상: $TARGET (api, worker, beat, site, admin, all, migrate 중 하나)"
    ;;
esac

echo ""
echo -e "${GREEN}${BOLD}✅ 배포 완료${RESET}"
echo "   API ingress: internal-and-cloud-load-balancing (direct Cloud Run URL is not the public entrypoint)"
