#!/usr/bin/env bash
# allow: SIZE_OK — single release deployment orchestrator; split after launch to avoid changing operator entrypoints today.
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
#   bash scripts/deploy.sh rollback     # 직전 배포 시작 시점의 리비전으로 트래픽 복귀
#
# 모든 배포 대상은 첫 mutation 전에 각 서비스의 현재 리비전을 .deploy-rollback에
# 기록한다. 중간에 실패해 서비스 버전이 섞이면 `rollback`으로 한 번에 되돌린다
# (트래픽만 되돌린다 — 이미 적용된 DB 마이그레이션은 별도 판단이 필요하다).
#
# site/admin 배포에는 도메인 env가 필요하다 (NEXT_PUBLIC_* 빌드 인라인용):
#   PUBLIC_DOMAIN=reputation.motionlabs.kr ADMIN_DOMAIN=admin.reputation.motionlabs.kr \
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
  echo "Usage: bash scripts/deploy.sh [backend|api|worker|beat|site|admin|all|migrate|rollback]"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_ROOT}/.env.production"
NON_SECRET_ENV_FILE=""
SERVICE_ENV_FILE=""
TEMP_ENV_FILES=()

PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || echo '')}"
REGION="${GCP_REGION:-asia-northeast3}"
REPO="${GCP_ARTIFACT_REPO:-reputation}"
IMAGE_TAG="$(date +%Y%m%d-%H%M%S)"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/reputation"

SERVICE_ACCOUNT="${GCP_SERVICE_ACCOUNT:-reputation-sa@${PROJECT_ID}.iam.gserviceaccount.com}"
ALLOW_PLAINTEXT_ENV_SECRETS="${ALLOW_PLAINTEXT_ENV_SECRETS:-0}"
VPC_CONNECTOR="${VPC_CONNECTOR:-${SERVERLESS_VPC_CONNECTOR:-reputation-vpc-connector}}"
VPC_EGRESS="${VPC_EGRESS:-private-ranges-only}"

# Cloud Run 서비스 설정
API_MEMORY="${API_MEMORY:-512Mi}"
API_CPU="${API_CPU:-1}"
API_MIN="${API_MIN:-1}"
API_MAX="${API_MAX:-7}"
API_CONCURRENCY="${API_CONCURRENCY:-80}"

WORKER_MEMORY="${WORKER_MEMORY:-1Gi}"
WORKER_CPU="${WORKER_CPU:-1}"
WORKER_MIN="${WORKER_MIN:-1}"
WORKER_MAX="${WORKER_MAX:-5}"
WORKER_CONCURRENCY="${WORKER_CONCURRENCY:-1}"  # Celery worker는 1 concurrency 권장

BEAT_MEMORY="${BEAT_MEMORY:-512Mi}"
BEAT_CPU="${BEAT_CPU:-1}"
BEAT_MIN="${BEAT_MIN:-1}"     # beat는 항상 1개만
BEAT_MAX="${BEAT_MAX:-1}"

# Frontend (Next.js) — terraform/cloudrun_frontend.tf 기본값과 동일하게 유지
FRONTEND_SERVICE_ACCOUNT="${FRONTEND_SERVICE_ACCOUNT:-reputation-frontend-sa@${PROJECT_ID}.iam.gserviceaccount.com}"
PUBLIC_DOMAIN="${PUBLIC_DOMAIN:-}"   # 예: reputation.motionlabs.kr
ADMIN_DOMAIN="${ADMIN_DOMAIN:-}"     # 예: admin.reputation.motionlabs.kr
SITE_MEMORY="${SITE_MEMORY:-512Mi}"
SITE_MIN="${SITE_MIN:-1}"
# site max=1 기본: on-demand ISR revalidate가 단일 인스턴스 캐시만 비우기 때문
# (terraform variables.tf site_max_instances 설명 참조).
SITE_MAX="${SITE_MAX:-1}"
ADMIN_MEMORY="${ADMIN_MEMORY:-512Mi}"
ADMIN_MIN="${ADMIN_MIN:-0}"
ADMIN_MAX="${ADMIN_MAX:-2}"

BACKEND_BASE_REQUIRED_SECRET_NAMES=(
  "ANTHROPIC_API_KEY"
  "OPENAI_API_KEY"
  "GEMINI_API_KEY"
  "SLACK_WEBHOOK_URL"
  "ADMIN_SECRET_KEY"
  "ADMIN_SESSION_SECRET"
  "SITE_BFF_SECRET"
  "REDIS_URL"
  # 아침 자동 발행은 캐시 무효화 경로가 없으면 프로덕션에서 배치 전체를 중단한다
  # (site_revalidate.ensure_site_revalidate_configured). 배포는 선택으로 두고 런타임은
  # 필수로 요구하면 "배포는 통과하는데 매일 아침 발행만 0건"이 된다 — 필수로 맞춘다.
  "SITE_REVALIDATE_SECRET"
  # 무료 진단 1회 제한의 해시 pepper. **로테이션 금지** — 값이 바뀌면 기존 잠금이 전부
  # 풀려 이미 신청한 병원이 다시 신청할 수 있게 된다. config.py가 dev 기본값을 프로덕션에서
  # 거부하므로 미설정이면 부팅 자체가 실패한다.
  "LEAD_LOCK_HASH_PEPPER"
  # 리포트/상태 페이지 열람 토큰의 해시 pepper. 없으면 부팅 실패(같은 게이트).
  "LEAD_REPORT_TOKEN_SECRET"
)

BACKEND_OPTIONAL_SECRET_NAMES=(
  # IndexNow 키. backend가 제출하고 site가 같은 값을 키 파일로 응답해야 소유가 증명된다 —
  # 두 서비스에 반드시 같은 시크릿을 주입할 것. 미설정이면 제출만 건너뛰고 발행은 정상 동작.
  "INDEXNOW_KEY"
)

SITE_REQUIRED_SECRET_NAMES=(
  "SITE_REVALIDATE_SECRET"
  "SITE_BFF_SECRET"
)

# IndexNow 키는 backend(제출)와 site(키 파일 응답) 양쪽에 같은 값이 있어야 소유가 증명된다.
# 어느 쪽도 필수는 아니다 — 미설정이면 backend는 is_configured()로 제출을 건너뛰고
# site는 /indexnow-key.txt에 404를 준다. 즉 부재는 정상 동작이므로 배포를 막지 않는다.
# (backend의 BACKEND_OPTIONAL_SECRET_NAMES와 같은 취급이어야 하며, 한쪽만 required로
#  두면 신규 환경이 선택 기능 때문에 배포 자체를 못 한다 — 회귀 방지용 주석.)
SITE_OPTIONAL_SECRET_NAMES=(
  "INDEXNOW_KEY"
)

ADMIN_REQUIRED_SECRET_NAMES=(
  "ADMIN_SESSION_SECRET"
  "ADMIN_SECRET_KEY"
  "SITE_BFF_SECRET"
)

ALL_MANAGED_SECRET_NAMES=(
  "${BACKEND_BASE_REQUIRED_SECRET_NAMES[@]}"
  "${BACKEND_OPTIONAL_SECRET_NAMES[@]}"
  "${SITE_REQUIRED_SECRET_NAMES[@]}"
  "${SITE_OPTIONAL_SECRET_NAMES[@]}"
  "${ADMIN_REQUIRED_SECRET_NAMES[@]}"
  "DB_PASSWORD"
  "DATABASE_URL"
  "SYNC_DATABASE_URL"
)

BACKEND_REQUIRED_SECRET_NAMES=("${BACKEND_BASE_REQUIRED_SECRET_NAMES[@]}")
REQUIRED_SECRET_NAMES=()
OPTIONAL_SECRET_NAMES=()
SECRET_ARGS=()

cleanup() {
  local f
  for f in "${TEMP_ENV_FILES[@]:-}"; do
    # if/then으로 쓴다 — `[[ ... ]] && rm`은 조건이 거짓이면 트랩의 마지막 명령이
    # 1을 반환하고, 그 값이 스크립트 종료 코드가 된다(임시 파일이 하나도 없는
    # 경로에서 성공 배포가 실패로 보고된다).
    if [[ -n "$f" && -f "$f" ]]; then
      rm -f "$f"
    fi
  done
}
trap cleanup EXIT

# ─── 사전 검증 ─────────────────────────────────────────────────────
info "사전 검증 중..."

command -v gcloud >/dev/null 2>&1 || fail "gcloud CLI가 설치되지 않았습니다."

# rollback은 장애 복구 경로다. 이미지 빌드도 env 조립도 하지 않으므로 Docker나
# .env.production을 요구해 복구를 막지 않는다.
if [[ "$TARGET" != "rollback" ]]; then
  command -v docker >/dev/null 2>&1 || fail "Docker가 설치되지 않았습니다."
fi

if [[ -z "$PROJECT_ID" ]]; then
  fail "GCP_PROJECT_ID 환경변수 또는 gcloud config를 설정해 주세요."
fi

if [[ "$TARGET" != "rollback" && ! -f "$ENV_FILE" ]]; then
  fail ".env.production 파일이 없습니다. .env.production.example을 복사해서 작성해 주세요."
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
DB_USER_NAME="${DB_USER:-$(read_env_file_value DB_USER || true)}"
DB_USER_NAME="${DB_USER_NAME:-reputation}"
ASSET_GCS_BUCKET="${NEXT_PUBLIC_GCP_STORAGE_BUCKET:-${GCP_STORAGE_BUCKET:-$(read_env_file_value GCP_STORAGE_BUCKET || true)}}"
GOOGLE_SITE_VERIFICATION="${NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION:-$(read_env_file_value NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION || true)}"
GA_MEASUREMENT_ID="${NEXT_PUBLIC_GA_MEASUREMENT_ID:-$(read_env_file_value NEXT_PUBLIC_GA_MEASUREMENT_ID || true)}"
DB_CONNECTION_MODE="${DB_CONNECTION_MODE:-$(read_env_file_value DB_CONNECTION_MODE || true)}"
CNAME_TARGET="${CNAME_TARGET:-$(read_env_file_value CNAME_TARGET || true)}"
OPENAI_CHATGPT_USE_WEB_SEARCH_VALUE="${OPENAI_CHATGPT_USE_WEB_SEARCH:-$(read_env_file_value OPENAI_CHATGPT_USE_WEB_SEARCH || true)}"
# 측정 모델 3종 — .env.production이 config.py 기본값을 덮어쓴다. 여기 값이 부동 별칭이면
# 코드에 아무리 고정 스냅샷을 적어도 런타임은 별칭을 쓴다(require_pinned_measurement_models).
OPENAI_MODEL_QUERY_VALUE="${OPENAI_MODEL_QUERY:-$(read_env_file_value OPENAI_MODEL_QUERY || true)}"
OPENAI_MODEL_PARSE_VALUE="${OPENAI_MODEL_PARSE:-$(read_env_file_value OPENAI_MODEL_PARSE || true)}"
GEMINI_MODEL_VALUE="${GEMINI_MODEL:-$(read_env_file_value GEMINI_MODEL || true)}"
CERTIFICATE_MANAGER_AUTO_PROVISION_VALUE="${CERTIFICATE_MANAGER_AUTO_PROVISION:-$(read_env_file_value CERTIFICATE_MANAGER_AUTO_PROVISION || true)}"
WILDCARD_PUBLIC_DOMAIN_CHECK="${WILDCARD_PUBLIC_DOMAIN_CHECK:-}"
if [[ -z "$DB_CONNECTION_MODE" ]]; then
  if [[ -n "$CLOUDSQL_CONNECTION" ]]; then
    DB_CONNECTION_MODE="cloudsql"
  else
    DB_CONNECTION_MODE="supabase"
  fi
fi
GCP_ATTACH_VPC_CONNECTOR="${GCP_ATTACH_VPC_CONNECTOR:-$(read_env_file_value GCP_ATTACH_VPC_CONNECTOR || true)}"
GCP_ATTACH_VPC_CONNECTOR="${GCP_ATTACH_VPC_CONNECTOR:-1}"

case "$DB_CONNECTION_MODE" in
  cloudsql)
    BACKEND_REQUIRED_SECRET_NAMES+=("DB_PASSWORD")
    ;;
  supabase|external)
    BACKEND_REQUIRED_SECRET_NAMES+=("DATABASE_URL" "SYNC_DATABASE_URL")
    ;;
  *)
    fail "DB_CONNECTION_MODE은 cloudsql, supabase, external 중 하나여야 합니다: ${DB_CONNECTION_MODE}"
    ;;
esac

BACKEND_RUNTIME_ARGS=()

is_cloudsql_mode() {
  [[ "$DB_CONNECTION_MODE" == "cloudsql" ]]
}

should_attach_vpc_connector() {
  [[ "$GCP_ATTACH_VPC_CONNECTOR" != "0" && "$GCP_ATTACH_VPC_CONNECTOR" != "false" ]]
}

is_managed_secret() {
  local key="$1"
  local name
  for name in "${ALL_MANAGED_SECRET_NAMES[@]}"; do
    [[ "$key" == "$name" ]] && return 0
  done
  return 1
}

# unsafe(평문 비밀 의심) 키 판정 — 접미사 앵커 기준 (R10). 느슨한 부분 문자열 매칭은
# SLACK_WEBHOOK_ALLOWED_HOSTS 같은 비-비밀 설정 키까지 잡아 일회성 safelist를 강요했다.
# *_ALLOWED_HOSTS / *_TIMEOUT 류는 자연 통과하고, 진짜 비밀 접미사만 잡는다.
# DATABASE_URL/SYNC_DATABASE_URL은 접속 비밀번호를 포함하므로 명시적으로 포함.
is_unsafe_secret_key() {
  local key="$1"
  [[ "$key" =~ (_SECRET|_SECRET_KEY|_PASSWORD|_TOKEN|_PRIVATE_KEY|_API_KEY|_WEBHOOK_URL)$ ]] && return 0
  [[ "$key" =~ ^(SYNC_)?DATABASE_URL$ ]] && return 0
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

    # SERVICE/APP_ENV와 배포 시 명시적으로 재정의할 수 있는 운영 필수 플래그는
    # 서비스별 env 파일에서 한 번만 주입한다 (YAML 중복 키 방지).
    if [[ "$key" == "SERVICE" || "$key" == "APP_ENV" \
      || "$key" == "OPENAI_CHATGPT_USE_WEB_SEARCH" \
      || "$key" == "CERTIFICATE_MANAGER_AUTO_PROVISION" ]]; then
      continue
    fi

    # 빈 값은 전달하지 않는다 (config.py 기본값 사용) — SITE_BFF_SECRET= 같은
    # 빈 placeholder가 unsafe 검사에 걸리는 것도 막는다.
    if [[ -z "$value" ]]; then
      continue
    fi

    if is_unsafe_secret_key "$key"; then
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
  printf 'SERVICE: "%s"\nAPP_ENV: "production"\nOPENAI_CHATGPT_USE_WEB_SEARCH: "%s"\nCERTIFICATE_MANAGER_AUTO_PROVISION: "%s"\n' \
    "$service" \
    "$OPENAI_CHATGPT_USE_WEB_SEARCH_VALUE" \
    "$CERTIFICATE_MANAGER_AUTO_PROVISION_VALUE" \
    >> "$SERVICE_ENV_FILE"
}

require_secret_versions() {
  local name status

  for name in "$@"; do
    if ! gcloud secrets describe "$name" --project="$PROJECT_ID" >/dev/null 2>&1; then
      fail "Secret Manager secret ${name} is missing. Create it before deploying so secrets are not passed as plaintext env vars."
    fi
    status="$(gcloud secrets versions describe latest --secret="$name" --project="$PROJECT_ID" --format='value(state)' 2>/dev/null || true)"
    if [[ "$status" != "ENABLED" ]]; then
      fail "Secret Manager secret ${name} latest version must be ENABLED before deploy."
    fi
  done
}

# gcloud의 --set-secrets는 `KEY=VALUE,...` dict 플래그이고 "기존 시크릿을 먼저 전부 제거"한다.
# 플래그를 여러 번 지정했을 때 누적되는지 마지막 것만 남는지는 문서가 보장하지 않으므로,
# 반드시 쉼표로 합친 **단일 플래그**로 넘긴다. 나눠서 넘기면 덮어쓰기 의미일 경우
# 마지막 시크릿 하나만 주입되어 서비스가 부팅에 실패한다.
build_secret_args() {
  SECRET_ARGS=()
  local name status
  local pairs=""

  for name in "${REQUIRED_SECRET_NAMES[@]}"; do
    require_secret_versions "$name"
    pairs+="${pairs:+,}${name}=${name}:latest"
  done

  for name in "${OPTIONAL_SECRET_NAMES[@]}"; do
    if gcloud secrets describe "$name" --project="$PROJECT_ID" >/dev/null 2>&1; then
      status="$(gcloud secrets versions describe latest --secret="$name" --project="$PROJECT_ID" --format='value(state)' 2>/dev/null || true)"
      if [[ "$status" != "ENABLED" ]]; then
        fail "Secret Manager secret ${name} latest version must be ENABLED before deploy."
      fi
      pairs+="${pairs:+,}${name}=${name}:latest"
    fi
  done

  if [[ -n "$pairs" ]]; then
    SECRET_ARGS=("--set-secrets=${pairs}")
  fi
}

if [[ "$TARGET" != "rollback" ]]; then
  prepare_non_secret_env_file
fi

prepare_backend_secret_args() {
  REQUIRED_SECRET_NAMES=("${BACKEND_REQUIRED_SECRET_NAMES[@]}")
  OPTIONAL_SECRET_NAMES=("${BACKEND_OPTIONAL_SECRET_NAMES[@]}")
  build_secret_args
}

prepare_site_secret_args() {
  REQUIRED_SECRET_NAMES=("${SITE_REQUIRED_SECRET_NAMES[@]}")
  OPTIONAL_SECRET_NAMES=("${SITE_OPTIONAL_SECRET_NAMES[@]}")
  build_secret_args
}

# ─── Docker 빌드 & 푸시 ────────────────────────────────────────────
# 주의: command substitution으로 호출되므로 stdout에는 이미지 URL만 출력한다.
build_and_push() {
  local image_url="${IMAGE_BASE}:${IMAGE_TAG}"
  info "Docker 이미지 빌드 중..."
  docker build \
    --platform linux/amd64 \
    -t "$image_url" \
    -f "${PROJECT_ROOT}/backend/Dockerfile" \
    "${PROJECT_ROOT}/backend" >&2 \
    || fail "이미지 빌드 실패(backend). Docker 데몬이 떠 있는지 확인하세요."

  info "Artifact Registry에 푸시 중..."
  docker push "$image_url" >&2 \
    || fail "이미지 푸시 실패: ${image_url}"

  ok "이미지 푸시 완료: ${image_url}"
  echo "$image_url"
}

# ─── Cloud Run 배포 ────────────────────────────────────────────────
deploy_api() {
  local image_url="$1"
  info "API 서비스 배포 중..."
  require_backend_runtime_shape
  build_backend_runtime_args
  make_service_env_file "api"

  set +u
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
    "${BACKEND_RUNTIME_ARGS[@]}" \
    --port=8000 \
    --timeout=300 \
    --no-cpu-throttling \
    --cpu-boost \
    --execution-environment=gen2
  set -u

  ok "API 배포 완료"
}

deploy_worker() {
  local image_url="$1"
  info "Worker 서비스 배포 중..."
  require_backend_runtime_shape
  build_backend_runtime_args
  make_service_env_file "worker"

  set +u
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
    "${BACKEND_RUNTIME_ARGS[@]}" \
    --timeout=900 \
    --no-cpu-throttling
  set -u

  ok "Worker 배포 완료"
}

deploy_beat() {
  local image_url="$1"
  info "Beat 서비스 배포 중..."
  require_backend_runtime_shape
  build_backend_runtime_args
  make_service_env_file "beat"

  set +u
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
    "${BACKEND_RUNTIME_ARGS[@]}" \
    --timeout=3600 \
    --no-cpu-throttling
  set -u

  ok "Beat 배포 완료"
}

require_public_domain() {
  if [[ -z "$PUBLIC_DOMAIN" ]]; then
    fail "PUBLIC_DOMAIN 환경변수가 필요합니다 (예: PUBLIC_DOMAIN=reputation.motionlabs.kr). NEXT_PUBLIC_* 값이 빌드 시점에 번들로 인라인되기 때문입니다."
  fi
}

require_admin_domain() {
  if [[ -z "$ADMIN_DOMAIN" ]]; then
    fail "ADMIN_DOMAIN 환경변수가 필요합니다 (예: ADMIN_DOMAIN=admin.reputation.motionlabs.kr). Admin Cloud Run 서비스는 LB 호스트 라우팅 뒤에서만 고객 제공 상태로 간주합니다."
  fi
}

require_public_dns() {
  if [[ "${SKIP_PUBLIC_DNS_PREFLIGHT:-0}" == "1" ]]; then
    info "SKIP_PUBLIC_DNS_PREFLIGHT=1 — 공개 DNS preflight를 건너뜁니다."
    return
  fi

  local domains=("$PUBLIC_DOMAIN")
  if [[ -n "$ADMIN_DOMAIN" ]]; then
    domains+=("$ADMIN_DOMAIN")
  fi
  if [[ -n "$CNAME_TARGET" ]]; then
    domains+=("$CNAME_TARGET")
  fi
  if [[ -z "$WILDCARD_PUBLIC_DOMAIN_CHECK" && -n "$PUBLIC_DOMAIN" ]]; then
    WILDCARD_PUBLIC_DOMAIN_CHECK="dns-preflight.${PUBLIC_DOMAIN}"
  fi
  if [[ -n "$WILDCARD_PUBLIC_DOMAIN_CHECK" ]]; then
    domains+=("$WILDCARD_PUBLIC_DOMAIN_CHECK")
  fi

  local expected_addresses="${PUBLIC_DNS_EXPECTED_ADDRESSES:-${CUSTOM_DOMAIN_IP_TARGETS:-}}"
  if [[ -z "$expected_addresses" ]]; then
    expected_addresses="$(read_env_file_value CUSTOM_DOMAIN_IP_TARGETS || true)"
  fi

  local dns_check=(python3 "${PROJECT_ROOT}/scripts/check_public_dns.py")
  if [[ -n "$expected_addresses" ]]; then
    dns_check+=("--expected-addresses" "$expected_addresses")
  fi
  dns_check+=("${domains[@]}")

  # stdout(>&2)으로 보낸다 — build_and_push_site/admin이 마지막에 image_url을 stdout으로
  # echo하고 main이 그걸 캡처하므로, preflight의 stdout이 새면 image_url을 오염시켜
  # gcloud run deploy의 --image가 깨진다.
  "${dns_check[@]}" >&2 \
    || fail "공개 도메인 DNS가 고객 제공 가능한 주소를 가리키지 않습니다. DNS를 먼저 수정하거나, 초기 인프라 부트스트랩이면 SKIP_PUBLIC_DNS_PREFLIGHT=1로 명시적으로 우회하세요."
}

build_and_push_site() {
  require_public_domain
  require_public_dns
  local image_url="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/site:${IMAGE_TAG}"
  info "Site 이미지 빌드 중 (NEXT_PUBLIC_* 인라인: https://${PUBLIC_DOMAIN})..."
  docker build \
    --platform linux/amd64 \
    --build-arg "NEXT_PUBLIC_API_URL=https://${PUBLIC_DOMAIN}/api/v1/public" \
    --build-arg "NEXT_PUBLIC_SITE_URL=https://${PUBLIC_DOMAIN}" \
    --build-arg "NEXT_PUBLIC_BACKEND_URL=https://${PUBLIC_DOMAIN}" \
    --build-arg "NEXT_PUBLIC_GCP_STORAGE_BUCKET=${ASSET_GCS_BUCKET}" \
    --build-arg "NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION=${GOOGLE_SITE_VERIFICATION}" \
    --build-arg "NEXT_PUBLIC_GA_MEASUREMENT_ID=${GA_MEASUREMENT_ID}" \
    -t "$image_url" \
    -f "${PROJECT_ROOT}/site/Dockerfile" \
    "${PROJECT_ROOT}/site" >&2 \
    || fail "이미지 빌드 실패(site). Docker 데몬이 떠 있는지 확인하세요."
  docker push "$image_url" >&2 \
    || fail "이미지 푸시 실패: ${image_url}"
  ok "Site 이미지 푸시 완료: ${image_url}"
  echo "$image_url"
}

build_and_push_admin() {
  require_public_domain
  require_public_dns
  local image_url="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/admin:${IMAGE_TAG}"
  info "Admin 이미지 빌드 중..."
  docker build \
    --platform linux/amd64 \
    --build-arg "NEXT_PUBLIC_BACKEND_URL=https://${PUBLIC_DOMAIN}" \
    -t "$image_url" \
    -f "${PROJECT_ROOT}/admin/Dockerfile" \
    "${PROJECT_ROOT}/admin" >&2 \
    || fail "이미지 빌드 실패(admin). Docker 데몬이 떠 있는지 확인하세요."
  docker push "$image_url" >&2 \
    || fail "이미지 푸시 실패: ${image_url}"
  ok "Admin 이미지 푸시 완료: ${image_url}"
  echo "$image_url"
}

deploy_site() {
  local image_url="$1"
  info "Site 서비스 배포 중..."

  # 필수/선택 시크릿을 backend와 동일한 규칙으로 조립한다. 하드코딩된 --set-secrets를
  # 쓰면 선택 시크릿(INDEXNOW_KEY)이 없는 환경에서 Cloud Run 배포가 실패한다.
  prepare_site_secret_args

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
    --set-env-vars="NEXT_PUBLIC_API_URL=https://${PUBLIC_DOMAIN}/api/v1/public,NEXT_PUBLIC_SITE_URL=https://${PUBLIC_DOMAIN},NEXT_PUBLIC_BACKEND_URL=https://${PUBLIC_DOMAIN},NEXT_PUBLIC_GCP_STORAGE_BUCKET=${ASSET_GCS_BUCKET},BACKEND_URL=https://${PUBLIC_DOMAIN}" \
    "${SECRET_ARGS[@]}" \
    --port=8080 \
    --timeout=60 \
    --cpu-boost

  ok "Site 배포 완료"
}

deploy_admin() {
  local image_url="$1"
  require_admin_domain
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
    --set-secrets="ADMIN_SESSION_SECRET=ADMIN_SESSION_SECRET:latest,ADMIN_SECRET_KEY=ADMIN_SECRET_KEY:latest,SITE_BFF_SECRET=SITE_BFF_SECRET:latest" \
    --port=8080 \
    --timeout=60 \
    --cpu-boost

  ok "Admin 배포 완료"
}

require_cloudsql_connection() {
  # 프로덕션 DATABASE_URL은 /cloudsql/<connection_name> unix socket을 쓰므로
  # Cloud SQL 연결을 Job에 붙이지 않으면 마이그레이션이 DB에 접근할 수 없다.
  if [[ -z "$CLOUDSQL_CONNECTION" ]]; then
    fail "CLOUD_SQL_CONNECTION_NAME이 필요합니다 (.env.production 또는 환경변수). 예: my-project:asia-northeast3:reputation-db"
  fi
}

cloudsql_instance_name() {
  local instance="${CLOUDSQL_CONNECTION##*:}"
  [[ -n "$instance" && "$instance" != "$CLOUDSQL_CONNECTION" ]] || fail "CLOUD_SQL_CONNECTION_NAME은 project:region:instance 형식이어야 합니다."
  echo "$instance"
}

require_cloudsql_app_user() {
  require_cloudsql_connection
  [[ -n "$DB_USER_NAME" ]] || fail "DB_USER가 필요합니다 (.env.production 또는 환경변수)."
  local instance
  instance="$(cloudsql_instance_name)"
  gcloud sql users list \
    --instance="$instance" \
    --project="$PROJECT_ID" \
    --format='value(name)' \
    | grep -Fx "$DB_USER_NAME" >/dev/null \
    || fail "Cloud SQL 사용자 ${DB_USER_NAME}가 ${instance}에 없습니다. 먼저 앱 DB user를 생성하고 DB_PASSWORD secret과 일치시켜 주세요."
}

require_asset_bucket() {
  # 콘텐츠 대표 이미지가 저장/서빙되는 GCS 버킷이 실제로 존재하는지 확인한다.
  # 버킷명은 전역 유일 제약 때문에 placeholder 기본값('reputation-images')일 수 없다 —
  # 규칙은 'reputation-images-<GCP_PROJECT_ID>' (terraform storage.tf / setup-gcp.sh).
  if [[ "${SKIP_ASSET_BUCKET_PREFLIGHT:-0}" == "1" ]]; then
    info "SKIP_ASSET_BUCKET_PREFLIGHT=1 — GCS 자산 버킷 preflight를 건너뜁니다."
    return
  fi
  [[ -n "$ASSET_GCS_BUCKET" ]] \
    || fail "GCP_STORAGE_BUCKET(자산 버킷)이 설정되지 않았습니다 (.env.production)."
  if [[ "$ASSET_GCS_BUCKET" == "reputation-images" || "$ASSET_GCS_BUCKET" == "reputation-reports" ]]; then
    fail "GCP_STORAGE_BUCKET가 placeholder 기본값 '${ASSET_GCS_BUCKET}'입니다 — 전역 유일 제약상 실제 버킷일 수 없습니다. '${ASSET_GCS_BUCKET}-${PROJECT_ID}' 규칙으로 설정하세요."
  fi
  command -v gsutil >/dev/null 2>&1 \
    || fail "gsutil이 설치되지 않았습니다 (GCS 자산 버킷 preflight에 필요). SKIP_ASSET_BUCKET_PREFLIGHT=1로 우회 가능."
  gsutil ls -b "gs://${ASSET_GCS_BUCKET}" >/dev/null 2>&1 \
    || fail "GCS 자산 버킷 gs://${ASSET_GCS_BUCKET}이 존재하지 않습니다. scripts/setup-gcp.sh로 먼저 생성하거나 SKIP_ASSET_BUCKET_PREFLIGHT=1로 우회하세요."
}

require_backend_runtime_shape() {
  if is_cloudsql_mode; then
    require_cloudsql_connection
  fi
  if should_attach_vpc_connector; then
    [[ -n "$VPC_CONNECTOR" ]] || fail "VPC_CONNECTOR 또는 SERVERLESS_VPC_CONNECTOR가 필요합니다."
  fi
}

require_production_feature_flags() {
  # 모델 recall을 'ChatGPT Search'로 잘못 측정하거나, 신규 도메인이 수동 인증서
  # 단계에서 멈춘 채 backend를 배포하는 반쪽 구성을 mutation 전에 차단한다.
  [[ "$OPENAI_CHATGPT_USE_WEB_SEARCH_VALUE" == "true" ]] \
    || fail "OPENAI_CHATGPT_USE_WEB_SEARCH=true가 필요합니다. 프로덕션 SoV는 실제 web_search만 허용합니다."
  [[ "$CERTIFICATE_MANAGER_AUTO_PROVISION_VALUE" == "true" ]] \
    || fail "CERTIFICATE_MANAGER_AUTO_PROVISION=true가 필요합니다. 신규 커스텀 도메인 자동 온보딩을 비활성화한 배포는 허용하지 않습니다."
  require_pinned_measurement_models
}

# AI 언급률 측정 모델은 날짜/버전으로 고정되어야 한다.
#
# 부동 별칭(gpt-4o-mini, gemini-flash-latest, chat-latest)은 공급자가 갱신하면 측정
# 기준선을 조용히 이동시킨다. 그러면 언급률이 떨어졌을 때 플랫폼이 바뀐 탓인지 우리
# 측정 도구가 바뀐 탓인지 영구히 구분할 수 없다 — 월별 비교를 파는 제품에서 치명적이다.
#
# config.py 기본값은 이미 고정돼 있지만 .env.production이 그것을 덮어쓴다. 이 파일은
# 저장소에 없어 CI가 못 보고 테스트도 기본값만 검사하므로, 배포 시점이 유일한 검문소다.
# (2026-07-29: 실제로 코드만 고치고 배포했다면 런타임은 gpt-4o 그대로였다.)
require_pinned_measurement_models() {
  local name value
  for name in OPENAI_MODEL_QUERY OPENAI_MODEL_PARSE GEMINI_MODEL; do
    case "$name" in
      OPENAI_MODEL_QUERY) value="$OPENAI_MODEL_QUERY_VALUE" ;;
      OPENAI_MODEL_PARSE) value="$OPENAI_MODEL_PARSE_VALUE" ;;
      GEMINI_MODEL)       value="$GEMINI_MODEL_VALUE" ;;
    esac

    # 미설정이면 config.py의 고정 기본값이 쓰인다 — 통과.
    [[ -z "$value" ]] && continue

    # 판정 기준은 "이름 모양"이 아니라 **공급자가 그 ID를 조용히 재해석하는가**다.
    #
    # 과거엔 OpenAI에 `-YYYY-MM-DD` 접미사를 요구했는데, 그건 고정 여부를 증명하지 못하면서
    # (gpt-5-mini-2099-99-99도 통과) 정작 최신 정식 모델은 막았다 — gpt-5.6-luna/sol/terra는
    # 날짜 접미사가 없어서 이 게이트 때문에 배포가 불가능했다. 무결성을 지키려던 검사가
    # 무결성 개선을 막은 셈이다.
    #
    # 실제로 재해석되는 형태는 두 가지뿐이다:
    #   (a) `-latest` 계열 — 공급자가 명시적으로 "항상 최신"이라고 선언한 별칭
    #   (b) 버전/변종 식별자가 없는 맨 계열명(gpt-5, gpt-5-mini, gemini-flash) — 새 스냅샷으로 옮겨간다
    # 그 둘만 거부하고 나머지는 통과시킨다.
    [[ "$value" == *-latest ]] \
      && fail "${name}=${value} 는 부동 별칭입니다. 고정 모델을 쓰세요 (예: gpt-5.6-luna, gpt-4o-mini-2024-07-18, gemini-3.6-flash)."

    case "$name" in
      OPENAI_MODEL_*)
        # 맨 계열명 거부: gpt-5 / gpt-5-mini / gpt-4o / gpt-4o-mini 처럼
        # 뒤에 날짜 스냅샷도 변종 이름도 없는 형태.
        if [[ "$value" =~ ^gpt-[0-9]+(\.[0-9]+)?o?(-(mini|nano|pro|chat|codex))*$ ]]; then
          fail "${name}=${value} 는 공급자가 새 스냅샷으로 옮기는 계열명입니다. 날짜 스냅샷(gpt-4o-mini-2024-07-18)이나 변종 고정명(gpt-5.6-luna)을 쓰세요."
        fi
        ;;
      GEMINI_MODEL)
        # Gemini는 버전 번호가 고정 식별자다(gemini-3.6-flash). 버전 없는 계열명은 거부.
        [[ "$value" =~ ^gemini-[0-9]+(\.[0-9]+)?- ]] \
          || fail "${name}=${value} 에 버전이 없습니다. 예: gemini-3.6-flash"
        ;;
    esac
  done
}

# Terraform이 선언한 env 키가 배포 때 사라지는 것을 mutation 전에 막는다.
#
# `gcloud run deploy --env-vars-file`은 **기존 env를 전부 제거한 뒤** 파일 내용만
# 적용한다. 그래서 terraform/cloudrun.tf가 심어 둔 키(SENTRY_DSN,
# CERTIFICATE_MAP_NAME, CELERY_* 등)가 .env.production에 없으면 조용히 사라진다.
# cloudrun.tf의 lifecycle ignore_changes(...containers[0].env)가 그 상태를 drift로
# 보지 않으므로 terraform apply로도 되돌아오지 않는다 — 즉 되돌릴 방법이 없다.
#
# 규칙: cloudrun.tf가 선언한 env 키는 (a) deploy.sh가 직접 주입하거나,
# (b) Secret Manager로 주입되거나, (c) .env.production에 `KEY=` 줄이 있어야 한다.
# 빈 값(`SENTRY_DSN=`)은 "의도적으로 설정 안 함"으로 취급한다 — terraform도 값이
# 없으면 해당 env를 렌더링하지 않으므로 결과가 일치한다.
TERRAFORM_ENV_SOURCE="${PROJECT_ROOT}/terraform/cloudrun.tf"

# make_service_env_file이 서비스별로 직접 써 넣는 키 — .env.production에 없어도 된다.
DEPLOY_INJECTED_ENV_KEYS=(
  "SERVICE"
  "APP_ENV"
  "OPENAI_CHATGPT_USE_WEB_SEARCH"
  "CERTIFICATE_MANAGER_AUTO_PROVISION"
)

is_deploy_injected_env_key() {
  local key="$1"
  local name
  for name in "${DEPLOY_INJECTED_ENV_KEYS[@]}"; do
    [[ "$key" == "$name" ]] && return 0
  done
  return 1
}

env_file_declares_key() {
  local key="$1"
  grep -qE "^[[:space:]]*(export )?${key}=" "$ENV_FILE"
}

require_no_dropped_terraform_env() {
  if [[ "${SKIP_TERRAFORM_ENV_PREFLIGHT:-0}" == "1" ]]; then
    info "SKIP_TERRAFORM_ENV_PREFLIGHT=1 — Terraform env 드롭 preflight를 건너뜁니다."
    return
  fi
  if [[ ! -f "$TERRAFORM_ENV_SOURCE" ]]; then
    info "terraform/cloudrun.tf가 없어 Terraform env 드롭 preflight를 건너뜁니다."
    return
  fi

  local missing=()
  local key
  while IFS= read -r key; do
    [[ -z "$key" ]] && continue
    if is_managed_secret "$key"; then
      continue
    fi
    if is_deploy_injected_env_key "$key"; then
      continue
    fi
    if env_file_declares_key "$key"; then
      continue
    fi
    missing+=("$key")
  done < <(grep -oE '^[[:space:]]*name[[:space:]]+= "[A-Z][A-Z0-9_]*"' "$TERRAFORM_ENV_SOURCE" \
    | grep -oE '"[A-Z][A-Z0-9_]*"' | tr -d '"' | sort -u)

  if (( ${#missing[@]} > 0 )); then
    fail "Terraform이 선언한 env 키가 .env.production에 없습니다: ${missing[*]}. --env-vars-file 배포는 기존 env를 전부 교체하므로 이 키들이 Cloud Run에서 사라지고, cloudrun.tf의 ignore_changes 때문에 terraform apply로도 복구되지 않습니다. .env.production.example처럼 값을 채우거나(설정하지 않을 키는 'KEY=' 빈 값으로) 명시하세요."
  fi
}

build_backend_runtime_args() {
  BACKEND_RUNTIME_ARGS=()
  if is_cloudsql_mode; then
    BACKEND_RUNTIME_ARGS+=("--set-cloudsql-instances=$CLOUDSQL_CONNECTION")
  fi
  if should_attach_vpc_connector; then
    BACKEND_RUNTIME_ARGS+=("--vpc-connector=$VPC_CONNECTOR" "--vpc-egress=$VPC_EGRESS")
  fi
}

run_migration() {
  local image_url="$1"
  info "DB 마이그레이션 실행 중..."

  require_backend_runtime_shape
  if is_cloudsql_mode; then
    require_cloudsql_app_user
  fi
  build_backend_runtime_args

  make_service_env_file "migrate"

  # --max-retries=0 — alembic 재실행은 half-applied 스키마를 만들 수 있으므로 실패를
  # 한 번만 표면화하고 롤아웃을 멈춘다. terraform/cloudrun.tf(max_retries = 0)와
  # 반드시 같은 값이어야 하며, 기존 Job을 갱신하는 update 폴백 분기에도 넣어야 한다
  # (빠지면 예전에 1로 만들어진 Job이 그대로 재시도한다).
  set +u
  gcloud run jobs create reputation-migrate \
    --image="$image_url" \
    --region="$REGION" \
    --service-account="$SERVICE_ACCOUNT" \
    --env-vars-file="$SERVICE_ENV_FILE" \
    "${SECRET_ARGS[@]}" \
    "${BACKEND_RUNTIME_ARGS[@]}" \
    --task-timeout=300 \
    --max-retries=0 \
    2>/dev/null || gcloud run jobs update reputation-migrate \
    --image="$image_url" \
    --region="$REGION" \
    --env-vars-file="$SERVICE_ENV_FILE" \
    "${SECRET_ARGS[@]}" \
    "${BACKEND_RUNTIME_ARGS[@]}" \
    --task-timeout=300 \
    --max-retries=0
  set -u

  gcloud run jobs execute reputation-migrate --region="$REGION" --wait

  ok "마이그레이션 완료"
}

run_redbeat_reconcile() {
  local image_url="$1"
  info "RedBeat 저장 스케줄 정합성 복구 중..."

  require_backend_runtime_shape
  build_backend_runtime_args
  make_service_env_file "beat"

  # 새 이미지가 선언한 allowlist만 남기고 과거 app.workers.tasks.* 정적/고아
  # entry를 제거한다. 별도 애플리케이션의 동적 RedBeat entry는 도구가 보존한다.
  set +u
  gcloud run jobs create reputation-redbeat-reconcile \
    --image="$image_url" \
    --region="$REGION" \
    --service-account="$SERVICE_ACCOUNT" \
    --env-vars-file="$SERVICE_ENV_FILE" \
    "${SECRET_ARGS[@]}" \
    "${BACKEND_RUNTIME_ARGS[@]}" \
    --command=python \
    --args=-m,app.utils.reconcile_redbeat_schedule,--apply \
    --task-timeout=300 \
    --max-retries=0 \
    2>/dev/null || gcloud run jobs update reputation-redbeat-reconcile \
    --image="$image_url" \
    --region="$REGION" \
    --env-vars-file="$SERVICE_ENV_FILE" \
    "${SECRET_ARGS[@]}" \
    "${BACKEND_RUNTIME_ARGS[@]}" \
    --command=python \
    --args=-m,app.utils.reconcile_redbeat_schedule,--apply \
    --task-timeout=300 \
    --max-retries=0
  set -u

  gcloud run jobs execute reputation-redbeat-reconcile --region="$REGION" --wait
  ok "RedBeat 저장 스케줄 정합성 복구 완료"
}

# ─── 롤백 좌표 ─────────────────────────────────────────────────────
# 다중 서비스 배포는 중간에 실패할 수 있다 (예: api/worker는 신버전, beat/site/admin은
# 구버전, 스키마는 이미 마이그레이션됨). 어떤 리비전으로 되돌려야 하는지는 배포가
# 시작되기 **전에만** 알 수 있으므로, 첫 mutation 이전에 기록해 둔다.
ROLLBACK_STATE_FILE="${DEPLOY_ROLLBACK_STATE_FILE:-${PROJECT_ROOT}/.deploy-rollback}"

current_ready_revision() {
  local service="$1"
  gcloud run services describe "$service" \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --format='value(status.latestReadyRevisionName)' 2>/dev/null || true
}

capture_rollback_point() {
  local service revision captured=0

  {
    printf '# scripts/deploy.sh %s — %s (project=%s region=%s)\n' \
      "$TARGET" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$PROJECT_ID" "$REGION"
  } > "$ROLLBACK_STATE_FILE"

  for service in "$@"; do
    revision="$(current_ready_revision "$service")"
    if [[ -n "$revision" ]]; then
      printf '%s=%s\n' "$service" "$revision" >> "$ROLLBACK_STATE_FILE"
      captured=$((captured + 1))
    fi
  done

  if (( captured == 0 )); then
    info "되돌릴 기존 리비전이 없습니다 (신규 환경). ${ROLLBACK_STATE_FILE}에 기록할 좌표 없음."
  else
    ok "롤백 좌표 ${captured}건 기록: ${ROLLBACK_STATE_FILE} (되돌리기: bash scripts/deploy.sh rollback)"
  fi
}

run_rollback() {
  [[ -f "$ROLLBACK_STATE_FILE" ]] \
    || fail "롤백 좌표 파일이 없습니다: ${ROLLBACK_STATE_FILE}. 배포를 시작한 머신에서 실행하거나 DEPLOY_ROLLBACK_STATE_FILE로 경로를 지정하세요."

  local line service revision rolled=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ -z "$line" || "$line" == \#* || "$line" != *=* ]]; then
      continue
    fi
    service="${line%%=*}"
    revision="${line#*=}"
    if [[ -z "$service" || -z "$revision" ]]; then
      continue
    fi

    info "${service} → ${revision} 트래픽 100% 복귀 중..."
    gcloud run services update-traffic "$service" \
      --region="$REGION" \
      --project="$PROJECT_ID" \
      --to-revisions="${revision}=100" \
      || fail "롤백 실패: ${service} → ${revision}. gcloud run services describe ${service} --region=${REGION}로 상태를 확인하세요."
    rolled=$((rolled + 1))
  done < "$ROLLBACK_STATE_FILE"

  (( rolled > 0 )) \
    || fail "${ROLLBACK_STATE_FILE}에 되돌릴 리비전이 없습니다 (신규 환경 배포였거나 기록 전에 실패)."

  ok "롤백 완료 — ${rolled}개 서비스"
  info "주의: 트래픽만 되돌립니다. 이미 적용된 DB 마이그레이션은 되돌아가지 않습니다. 신구 스키마가 호환되지 않으면 alembic downgrade를 별도로 판단해 실행하세요."
}

# ─── 메인 ──────────────────────────────────────────────────────────
case "$TARGET" in
  backend)
    prepare_backend_secret_args
    require_backend_runtime_shape
    require_production_feature_flags
    require_no_dropped_terraform_env
    require_asset_bucket
    if is_cloudsql_mode; then
      require_cloudsql_app_user
    fi
    capture_rollback_point reputation-api reputation-worker reputation-beat
    IMAGE_URL=$(build_and_push)
    run_migration "$IMAGE_URL"
    deploy_api "$IMAGE_URL"
    deploy_worker "$IMAGE_URL"
    run_redbeat_reconcile "$IMAGE_URL"
    deploy_beat "$IMAGE_URL"
    ;;
  api|worker|beat)
    prepare_backend_secret_args
    require_backend_runtime_shape
    require_production_feature_flags
    require_no_dropped_terraform_env
    require_asset_bucket
    if is_cloudsql_mode; then
      require_cloudsql_app_user
    fi
    capture_rollback_point "reputation-${TARGET}"
    IMAGE_URL=$(build_and_push)
    if [[ "$TARGET" == "beat" ]]; then
      run_redbeat_reconcile "$IMAGE_URL"
    fi
    "deploy_${TARGET}" "$IMAGE_URL"
    ;;
  site)
    require_secret_versions "${SITE_REQUIRED_SECRET_NAMES[@]}"
    require_asset_bucket
    capture_rollback_point reputation-site
    SITE_IMAGE_URL=$(build_and_push_site)
    deploy_site "$SITE_IMAGE_URL"
    ;;
  admin)
    require_secret_versions "${ADMIN_REQUIRED_SECRET_NAMES[@]}"
    require_admin_domain
    capture_rollback_point reputation-admin
    ADMIN_IMAGE_URL=$(build_and_push_admin)
    deploy_admin "$ADMIN_IMAGE_URL"
    ;;
  all)
    # 모든 필수 요건을 어떤 변경(마이그레이션 포함)보다 먼저 검증한다 (R10) —
    # PUBLIC_DOMAIN 누락이 site 빌드 단계에서야 터지면 새 backend + 옛 frontend의
    # 반쪽 롤아웃 상태로 중단된다.
    require_public_domain
    require_admin_domain
    require_public_dns
    require_backend_runtime_shape
    require_production_feature_flags
    require_no_dropped_terraform_env
    require_asset_bucket
    if is_cloudsql_mode; then
      require_cloudsql_app_user
    fi
    require_secret_versions "${SITE_REQUIRED_SECRET_NAMES[@]}" "${ADMIN_REQUIRED_SECRET_NAMES[@]}"
    prepare_backend_secret_args
    capture_rollback_point \
      reputation-api reputation-worker reputation-beat \
      reputation-site reputation-admin
    IMAGE_URL=$(build_and_push)
    SITE_IMAGE_URL=$(build_and_push_site)
    ADMIN_IMAGE_URL=$(build_and_push_admin)
    # 마이그레이션을 새 코드 배포보다 먼저 실행 — 새 리비전이 옛 스키마 위에서
    # 기동하는 시간을 없앤다 (additive migration 전제).
    run_migration "$IMAGE_URL"
    deploy_api "$IMAGE_URL"
    deploy_worker "$IMAGE_URL"
    run_redbeat_reconcile "$IMAGE_URL"
    deploy_beat "$IMAGE_URL"
    deploy_site "$SITE_IMAGE_URL"
    deploy_admin "$ADMIN_IMAGE_URL"
    ;;
  migrate)
    prepare_backend_secret_args
    require_backend_runtime_shape
    require_production_feature_flags
    require_no_dropped_terraform_env
    if is_cloudsql_mode; then
      require_cloudsql_app_user
    fi
    IMAGE_URL=$(build_and_push)
    run_migration "$IMAGE_URL"
    ;;
  rollback)
    run_rollback
    exit 0
    ;;
  *)
    fail "알 수 없는 대상: $TARGET (backend, api, worker, beat, site, admin, all, migrate, rollback 중 하나)"
    ;;
esac

echo ""
echo -e "${GREEN}${BOLD}✅ 배포 완료${RESET}"
echo "   API ingress: internal-and-cloud-load-balancing (direct Cloud Run URL is not the public entrypoint)"
if [[ -f "$ROLLBACK_STATE_FILE" ]]; then
  echo "   되돌리기: bash scripts/deploy.sh rollback (기준 리비전: ${ROLLBACK_STATE_FILE})"
fi
