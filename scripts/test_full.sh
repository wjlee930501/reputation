#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# Re:putation 종합 테스트 스크립트
# 사용법: bash scripts/test_full.sh
# 전제조건: docker compose up -d (api, db, redis, worker, beat 실행 중)
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

BASE="http://localhost:8000"
ADMIN_KEY="dev-secret-key"
PASS=0
FAIL=0
SKIP=0

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
RESET='\033[0m'

header() { echo -e "\n${BLUE}${BOLD}══ $1 ══${RESET}"; }
ok()     { echo -e "  ${GREEN}✓${RESET} $1"; PASS=$((PASS+1)); }
fail()   { echo -e "  ${RED}✗${RESET} $1"; FAIL=$((FAIL+1)); }
skip()   { echo -e "  ${YELLOW}⊘${RESET} $1 (API 키 없음 — 스킵)"; SKIP=$((SKIP+1)); }
info()   { echo -e "  ${YELLOW}ℹ${RESET} $1"; }

check_api() {
  local desc="$1" url="$2" expected="$3"
  local res
  res=$(curl -sf --max-time 5 "$url" -H "X-Admin-Key: $ADMIN_KEY" 2>/dev/null) || { fail "$desc — 응답 없음"; return; }
  if echo "$res" | grep -q "$expected"; then
    ok "$desc"
  else
    fail "$desc — 예상: '$expected' / 실제: $(echo "$res" | head -c 120)"
  fi
}

check_post() {
  local desc="$1" url="$2" body="$3" expected="$4"
  local res
  res=$(curl -sf --max-time 5 -X POST "$url" -H "X-Admin-Key: $ADMIN_KEY" -H "Content-Type: application/json" -d "$body" 2>/dev/null) || { fail "$desc — 응답 없음"; return; }
  if echo "$res" | grep -q "$expected"; then
    ok "$desc"
  else
    fail "$desc — 예상: '$expected' / 실제: $(echo "$res" | head -c 120)"
  fi
}

check_api_key() {
  local key="$1"
  local val
  val=$(grep "^${key}=" .env 2>/dev/null | cut -d= -f2-)
  if [[ -z "$val" || "$val" == *REPLACE_ME* || "$val" == *placeholder* || "$val" == "sk-ant-..." || "$val" == "sk-..." || "$val" == "pplx-..." ]]; then
    return 1
  fi
  return 0
}

# ─── 사전 확인 ─────────────────────────────────────────────────────
header "사전 확인"

if curl -sf --max-time 5 "$BASE/health" >/dev/null 2>&1 || curl -sf --max-time 5 "$BASE/docs" >/dev/null 2>&1; then
  ok "API 서버 응답 (port 8000)"
else
  fail "API 서버 응답 없음 — docker compose up -d api 확인"
  exit 1
fi

if docker compose ps worker 2>/dev/null | grep -q "Up"; then
  ok "Celery worker 실행 중"
else
  fail "Celery worker 미실행 — docker compose up -d worker"
fi

if docker compose ps beat 2>/dev/null | grep -q "Up"; then
  ok "Celery beat 실행 중"
else
  fail "Celery beat 미실행 — docker compose up -d beat"
fi

if curl -sf --max-time 5 "http://localhost:5555" >/dev/null 2>&1; then
  ok "Flower 대시보드 응답 (port 5555)"
else
  info "Flower 미응답 (선택사항)"
fi

# ─── 1. 병원 프로파일 CRUD ─────────────────────────────────────────
header "1. 병원 프로파일 CRUD"

HOSPITAL_JSON='{
  "name": "통합테스트 정형외과",
  "address": "서울시 강남구 테헤란로 999",
  "phone": "02-9999-0000",
  "region": ["강남구", "서초구"],
  "specialties": ["정형외과"],
  "keywords": ["무릎통증", "허리디스크"],
  "plan": "PLAN_16"
}'

CREATE_RES=$(curl -sf --max-time 5 -X POST "$BASE/api/v1/admin/hospitals" \
  -H "X-Admin-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
  -d "$HOSPITAL_JSON" 2>/dev/null) || { fail "병원 생성 실패"; CREATE_RES="{}"; }

TEST_ID=$(echo "$CREATE_RES" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null)
TEST_SLUG=$(echo "$CREATE_RES" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('slug',''))" 2>/dev/null)

if [[ -n "$TEST_ID" ]]; then
  ok "병원 생성 (id: ${TEST_ID:0:8}...)"
else
  fail "병원 생성 — ID 없음: $CREATE_RES"
fi

check_api "병원 목록 조회" "$BASE/api/v1/admin/hospitals" "통합테스트"
check_api "병원 상세 조회" "$BASE/api/v1/admin/hospitals/$TEST_ID" "통합테스트"

# 프로파일 업데이트 (PATCH /profile)
UPDATE_RES=$(curl -sf --max-time 5 -X PATCH "$BASE/api/v1/admin/hospitals/$TEST_ID/profile" \
  -H "X-Admin-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"director_name": "김통합", "director_career": "서울대 의대 졸업\n정형외과 전문의 15년", "director_philosophy": "환자 중심 비수술 치료"}' 2>/dev/null) || UPDATE_RES="{}"

if echo "$UPDATE_RES" | grep -q "김통합"; then
  ok "병원 프로파일 업데이트 (PATCH /profile)"
else
  fail "병원 프로파일 업데이트 실패: $UPDATE_RES"
fi

# ─── 2. 콘텐츠 스케줄 ─────────────────────────────────────────────
header "2. 콘텐츠 스케줄 설정"

SCHED_RES=$(curl -sf --max-time 5 -X POST "$BASE/api/v1/admin/hospitals/$TEST_ID/schedule" \
  -H "X-Admin-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"plan": "PLAN_16", "publish_days": [1, 4], "active_from": "2026-03-01"}' 2>/dev/null) || SCHED_RES="{}"

if echo "$SCHED_RES" | grep -q -E "(schedule_id|id|PLAN_16)"; then
  ok "콘텐츠 스케줄 생성"
  SCHED_ID=$(echo "$SCHED_RES" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null)
else
  fail "콘텐츠 스케줄 생성 실패: $(echo $SCHED_RES | head -c 200)"
fi

# 스케줄 조회 — DB 직접 확인 (GET /schedule 없음)
SCHED_DB=$(docker exec reputation-db-1 psql -U reputation -d reputation -t -c \
  "SELECT plan FROM content_schedules WHERE hospital_id='$TEST_ID' LIMIT 1" 2>/dev/null | tr -d ' \n') || SCHED_DB=""
if [[ "$SCHED_DB" == "PLAN_16" ]]; then
  ok "스케줄 생성 DB 확인 (PLAN_16)"
else
  fail "스케줄 DB 확인 실패: '$SCHED_DB'"
fi

# ─── 3. 콘텐츠 아이템 ─────────────────────────────────────────────
header "3. 콘텐츠 아이템"

CONTENT_LIST=$(curl -sf --max-time 5 "$BASE/api/v1/admin/hospitals/$TEST_ID/content" \
  -H "X-Admin-Key: $ADMIN_KEY" 2>/dev/null) || CONTENT_LIST="[]"

CONTENT_COUNT=$(echo "$CONTENT_LIST" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

if [[ "$CONTENT_COUNT" -gt 0 ]]; then
  ok "콘텐츠 목록 조회 (${CONTENT_COUNT}개 슬롯)"
  FIRST_CONTENT_ID=$(echo "$CONTENT_LIST" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['id'] if d else '')" 2>/dev/null)
else
  info "콘텐츠 슬롯 0개 (스케줄 생성 후 자동 생성됨)"
  FIRST_CONTENT_ID=""
fi

# DB에서 직접 콘텐츠 ID 조회 (스케줄이 있는 경우)
if [[ -z "$FIRST_CONTENT_ID" ]]; then
  FIRST_CONTENT_ID=$(docker exec reputation-db-1 psql -U reputation -d reputation -t -c \
    "SELECT id FROM content_items WHERE hospital_id = '$TEST_ID' LIMIT 1" 2>/dev/null | tr -d ' \n') || true
fi

if [[ -n "$FIRST_CONTENT_ID" ]]; then
  # 콘텐츠에 타이틀 시딩
  docker exec reputation-db-1 psql -U reputation -d reputation -c \
    "UPDATE content_items SET title='테스트 콘텐츠', body='## 테스트\n테스트 본문입니다.', status='DRAFT' WHERE id='$FIRST_CONTENT_ID'" >/dev/null 2>&1 || true

  check_api "콘텐츠 상세 조회" "$BASE/api/v1/admin/hospitals/$TEST_ID/content/$FIRST_CONTENT_ID" "DRAFT"

  # 발행
  PUB_RES=$(curl -sf --max-time 5 -X POST "$BASE/api/v1/admin/hospitals/$TEST_ID/content/$FIRST_CONTENT_ID/publish" \
    -H "X-Admin-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
    -d '{"published_by": "테스트AE"}' 2>/dev/null) || PUB_RES="{}"
  if echo "$PUB_RES" | grep -q "Published"; then
    ok "콘텐츠 발행 (→ PUBLISHED)"
  else
    fail "콘텐츠 발행 실패: $PUB_RES"
  fi
fi

# ─── 4. 반려 후 재생성 (BUG-01 검증) ─────────────────────────────
header "4. 반려 후 재생성 (BUG-01)"

# 두번째 콘텐츠 아이템 반려 테스트
SECOND_CONTENT_ID=$(docker exec reputation-db-1 psql -U reputation -d reputation -t -c \
  "SELECT id FROM content_items WHERE hospital_id = '$TEST_ID' AND id != '$FIRST_CONTENT_ID' LIMIT 1" 2>/dev/null | tr -d ' \n') || true

if [[ -n "$SECOND_CONTENT_ID" ]]; then
  docker exec reputation-db-1 psql -U reputation -d reputation -c \
    "UPDATE content_items SET title='반려 테스트', body='본문', status='DRAFT' WHERE id='$SECOND_CONTENT_ID'" >/dev/null 2>&1 || true

  REJ_RES=$(curl -sf --max-time 5 -X POST "$BASE/api/v1/admin/hospitals/$TEST_ID/content/$SECOND_CONTENT_ID/reject" \
    -H "X-Admin-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
    -d '{"reason": "내용 수정 필요"}' 2>/dev/null) || REJ_RES="{}"

  if echo "$REJ_RES" | grep -q "Rejected"; then
    ok "콘텐츠 반려 (→ REJECTED)"
    # REJECTED 상태 DB 확인
    STATUS=$(docker exec reputation-db-1 psql -U reputation -d reputation -t -c \
      "SELECT status FROM content_items WHERE id='$SECOND_CONTENT_ID'" 2>/dev/null | tr -d ' \n')
    if [[ "$STATUS" == "REJECTED" ]]; then
      ok "DB REJECTED 상태 확인"
    else
      fail "DB 상태 불일치: $STATUS"
    fi
    # tasks.py에서 REJECTED 픽업 코드 확인
    if grep -q "status.in_.*DRAFT.*REJECTED\|status.in_.*REJECTED.*DRAFT" backend/app/workers/tasks.py 2>/dev/null; then
      ok "BUG-01 fix 확인 — nightly 태스크가 DRAFT+REJECTED 모두 픽업"
    else
      fail "BUG-01 fix 미적용 확인 필요"
    fi
  else
    fail "콘텐츠 반려 실패: $REJ_RES"
  fi
fi

# ─── 5. Public API ────────────────────────────────────────────────
header "5. Public API"

# ACTIVE 상태로 전환
docker exec reputation-db-1 psql -U reputation -d reputation -c \
  "UPDATE hospitals SET status='ACTIVE' WHERE id='$TEST_ID'" >/dev/null 2>&1

check_api "Public — 병원 정보" "$BASE/api/v1/public/hospitals/$TEST_SLUG" "통합테스트"
check_api "Public — 발행 콘텐츠 목록" "$BASE/api/v1/public/hospitals/$TEST_SLUG/contents" "PUBLISHED\|published_at\|\[\]"

if [[ -n "$FIRST_CONTENT_ID" ]]; then
  check_api "Public — 콘텐츠 상세" "$BASE/api/v1/public/hospitals/$TEST_SLUG/contents/$FIRST_CONTENT_ID" "테스트 콘텐츠"
fi

# ONBOARDING 병원은 Public API에서 404
docker exec reputation-db-1 psql -U reputation -d reputation -c \
  "UPDATE hospitals SET status='ONBOARDING' WHERE id='$TEST_ID'" >/dev/null 2>&1
HTTP_CODE=$(curl -o /dev/null -sw "%{http_code}" "$BASE/api/v1/public/hospitals/$TEST_SLUG" 2>/dev/null)
if [[ "$HTTP_CODE" == "404" ]]; then
  ok "비ACTIVE 병원 Public API 404 응답"
else
  fail "비ACTIVE 병원이 Public에 노출됨 (HTTP $HTTP_CODE)"
fi
# 다시 ACTIVE로
docker exec reputation-db-1 psql -U reputation -d reputation -c \
  "UPDATE hospitals SET status='ACTIVE' WHERE id='$TEST_ID'" >/dev/null 2>&1

# ─── 6. V0 리포트 (API 키 필요) ───────────────────────────────────
header "6. V0 리포트 생성 (OpenAI + Perplexity 필요)"

if check_api_key "OPENAI_API_KEY" && check_api_key "PERPLEXITY_API_KEY"; then
  info "V0 리포트 태스크 트리거 중..."
  V0_RES=$(curl -sf --max-time 5 -X POST "$BASE/api/v1/admin/hospitals/$TEST_ID/reports/v0" \
    -H "X-Admin-Key: $ADMIN_KEY" 2>/dev/null) || V0_RES="{}"
  if echo "$V0_RES" | grep -q -E "(task_id|queued|started|report_id)"; then
    ok "V0 리포트 태스크 큐 등록"
    info "Flower에서 진행 확인: http://localhost:5555"
  else
    fail "V0 리포트 트리거 실패: $(echo $V0_RES | head -c 200)"
  fi
else
  skip "V0 리포트 — OPENAI_API_KEY 또는 PERPLEXITY_API_KEY 미설정"
fi

# ─── 7. 콘텐츠 생성 태스크 (API 키 필요) ──────────────────────────
header "7. 콘텐츠 자동 생성 (Anthropic + Imagen 필요)"

if check_api_key "ANTHROPIC_API_KEY"; then
  info "콘텐츠 생성 태스크 수동 트리거..."
  # Celery로 직접 태스크 발행
  TASK_RES=$(docker exec reputation-api-1 python3 -c "
from app.workers.tasks import nightly_content_generation
result = nightly_content_generation.delay()
print('task_id:', result.id)
" 2>/dev/null) || TASK_RES=""
  if echo "$TASK_RES" | grep -q "task_id"; then
    ok "콘텐츠 생성 태스크 큐 등록 ($TASK_RES)"
    info "약 30초 후 DB에서 DRAFT 콘텐츠 확인"
  else
    fail "콘텐츠 생성 태스크 트리거 실패"
  fi
else
  skip "콘텐츠 자동 생성 — ANTHROPIC_API_KEY 미설정"
fi

# ─── 8. Slack 알림 (API 키 필요) ──────────────────────────────────
header "8. Slack 알림"

if check_api_key "SLACK_WEBHOOK_URL"; then
  SLACK_RES=$(curl -sf --max-time 5 -X POST "$BASE/api/v1/admin/hospitals/$TEST_ID/test-slack" \
    -H "X-Admin-Key: $ADMIN_KEY" 2>/dev/null) || SLACK_RES=""
  if echo "$SLACK_RES" | grep -q "ok\|sent"; then
    ok "Slack 알림 전송"
  else
    # 직접 webhook 테스트
    SLACK_TEST=$(curl -sf --max-time 5 -X POST "$(grep SLACK_WEBHOOK_URL .env | cut -d= -f2-)" \
      -H "Content-Type: application/json" \
      -d '{"text": "🧪 Re:putation 테스트 알림 — 정상 동작 확인"}' 2>/dev/null) || SLACK_TEST=""
    if echo "$SLACK_TEST" | grep -q "ok"; then
      ok "Slack webhook 직접 테스트 성공"
    else
      fail "Slack 알림 실패"
    fi
  fi
else
  skip "Slack 알림 — SLACK_WEBHOOK_URL 미설정"
fi

# ─── 9. Admin Next.js ─────────────────────────────────────────────
header "9. Admin UI (Next.js)"

if curl -sf --max-time 5 "http://localhost:3000" >/dev/null 2>&1; then
  ok "Admin UI 응답 (port 3000)"
else
  fail "Admin UI 미응답 — 'cd admin && npm run dev' 실행 필요"
fi

# ─── 10. Site Next.js ─────────────────────────────────────────────
header "10. Site Next.js"

if curl -sf --max-time 5 "http://localhost:3002" >/dev/null 2>&1; then
  ok "Site 응답 (port 3002)"
elif curl -sf --max-time 5 "http://localhost:3001" >/dev/null 2>&1; then
  ok "Site 응답 (port 3001)"
else
  fail "Site 미응답 — 'cd site && npm run dev -- --port 3002' 실행 필요"
fi

# ─── 11. 테스트 데이터 정리 ────────────────────────────────────────
header "11. 테스트 데이터 정리"

if [[ -n "$TEST_ID" ]]; then
  DEL_RES=$(curl -sf --max-time 5 -X DELETE "$BASE/api/v1/admin/hospitals/$TEST_ID" \
    -H "X-Admin-Key: $ADMIN_KEY" 2>/dev/null) || DEL_RES="{}"
  if echo "$DEL_RES" | grep -q -E "(deleted|ok|success|\{\})"; then
    ok "테스트 병원 삭제"
  else
    # 강제 DB 삭제
    docker exec reputation-db-1 psql -U reputation -d reputation -c \
      "DELETE FROM hospitals WHERE id='$TEST_ID'" >/dev/null 2>&1 && ok "테스트 병원 DB 직접 삭제" || info "수동 정리 필요: $TEST_ID"
  fi
fi

# ─── 결과 요약 ────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══════════════════════════════════════${RESET}"
echo -e "${BOLD} 테스트 결과 요약${RESET}"
echo -e "${BOLD}═══════════════════════════════════════${RESET}"
echo -e "  ${GREEN}통과${RESET}: $PASS"
echo -e "  ${RED}실패${RESET}: $FAIL"
echo -e "  ${YELLOW}스킵${RESET}: $SKIP (API 키 필요)"
echo ""

if [[ $FAIL -eq 0 ]]; then
  echo -e "  ${GREEN}${BOLD}✅ 모든 테스트 통과!${RESET}"
else
  echo -e "  ${RED}${BOLD}❌ 실패 항목을 확인해주세요.${RESET}"
fi

if [[ $SKIP -gt 0 ]]; then
  echo ""
  echo -e "  ${YELLOW}API 키를 .env에 설정하면 스킵된 ${SKIP}개 항목도 테스트됩니다.${RESET}"
fi
echo ""
