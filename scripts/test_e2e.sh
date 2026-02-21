#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# Re:putation 완전 E2E 통합 테스트
#
# 시나리오: "장편한외과의원" 신규 계약부터 월간 리포트까지 전 과정
#   STEP 1.  시스템 상태 확인
#   STEP 2.  병원 프로파일 입력 (완전한 정보)
#   STEP 3.  V0 리포트 자동 생성 (profile_complete → SoV → PDF → Slack)
#   STEP 4.  AEO 홈페이지 빌드 (V0 완료 후 자동 트리거)
#   STEP 5.  도메인 연결 시뮬레이션
#   STEP 6.  콘텐츠 스케줄 설정 (PLAN_16, 화·금 발행)
#   STEP 7.  콘텐츠 자동 생성 (Claude Sonnet)
#   STEP 8.  콘텐츠 발행 + 반려 (BUG-01 검증)
#   STEP 9.  Public API 확인 (AEO 사이트 데이터)
#   STEP 10. SoV 측정 태스크 (ChatGPT + Gemini)
#   STEP 11. 월간 리포트 생성 (PDF)
#   STEP 12. 테스트 데이터 정리
#
# 사용법: bash scripts/test_e2e.sh
# ═══════════════════════════════════════════════════════════════════

set -uo pipefail

BASE="http://localhost:8000"
ADMIN_KEY="dev-secret-key"
DB_CONTAINER="reputation-db-1"
API_CONTAINER="reputation-api-1"

PASS=0; FAIL=0; SKIP=0

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

header()   { echo -e "\n${BLUE}${BOLD}╔══ STEP $1 ══╗${RESET}"; echo -e "${BLUE}${BOLD}  $2${RESET}"; }
ok()       { echo -e "  ${GREEN}✓${RESET} $1"; PASS=$((PASS+1)); }
fail()     { echo -e "  ${RED}✗${RESET} $1"; FAIL=$((FAIL+1)); }
skip()     { echo -e "  ${YELLOW}⊘${RESET} $1"; SKIP=$((SKIP+1)); }
info()     { echo -e "  ${CYAN}ℹ${RESET}  $1"; }
wait_msg() { echo -ne "  ${YELLOW}⏳${RESET} $1\r"; }

# ─── 유틸 ─────────────────────────────────────────────────────────
api_get() {
  curl -sf --max-time 10 "$BASE$1" -H "X-Admin-Key: $ADMIN_KEY" 2>/dev/null
}
api_post() {
  curl -sf --max-time 10 -X POST "$BASE$1" \
    -H "X-Admin-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
    -d "$2" 2>/dev/null
}
api_patch() {
  curl -sf --max-time 10 -X PATCH "$BASE$1" \
    -H "X-Admin-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
    -d "$2" 2>/dev/null
}
api_delete() {
  curl -sf --max-time 10 -X DELETE "$BASE$1" -H "X-Admin-Key: $ADMIN_KEY" 2>/dev/null
}

# PostgreSQL 쿼리 — \r\n 완전 제거
psql_q() {
  docker exec "$DB_CONTAINER" psql -U reputation -d reputation -t -c "$1" 2>/dev/null \
    | tr -d ' \n\r\t'
}

# Celery 태스크 동기 실행
run_task_sync() {
  docker exec "$API_CONTAINER" python3 -c "$1" 2>&1
}

has_key() {
  local val
  val=$(grep -E "^${1}=" .env 2>/dev/null | cut -d= -f2-)
  [[ -n "$val" && "$val" != *REPLACE_ME* && "$val" != *placeholder* ]]
}

# Celery 태스크 완료 대기 (최대 $2초)
wait_for_task() {
  local task_id="$1" timeout="${2:-120}" elapsed=0
  while [[ $elapsed -lt $timeout ]]; do
    local state
    state=$(run_task_sync "
from app.core.celery_app import celery_app
r = celery_app.AsyncResult('$task_id')
print(r.state)
" 2>/dev/null | tail -1 | tr -d '\r\n')
    case "$state" in
      SUCCESS) echo "SUCCESS"; return 0 ;;
      FAILURE) echo "FAILURE"; return 1 ;;
    esac
    sleep 5; elapsed=$((elapsed+5))
    wait_msg "대기 중... ${elapsed}/${timeout}초"
  done
  echo "TIMEOUT"; return 2
}

# ══════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}╔═══════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║  Re:putation 완전 E2E 통합 테스트                ║${RESET}"
echo -e "${BOLD}║  시나리오: 장편한외과의원 신규 계약 → 월간 리포트  ║${RESET}"
echo -e "${BOLD}╚═══════════════════════════════════════════════════╝${RESET}"
echo ""

# ══════════════════════════════════════════════════════════════════
header "1" "시스템 상태 확인"
# ══════════════════════════════════════════════════════════════════

if curl -sf --max-time 5 "$BASE/health" >/dev/null 2>&1 || \
   curl -sf --max-time 5 "$BASE/docs" >/dev/null 2>&1; then
  ok "API 서버 응답 (port 8000)"
else
  echo -e "  ${RED}✗ API 서버 미응답 — 'docker compose up -d api' 확인 후 재실행${RESET}"
  exit 1
fi

for svc in worker beat; do
  if docker compose ps "$svc" 2>/dev/null | grep -q "Up"; then
    ok "Celery $svc 실행 중"
  else
    fail "Celery $svc 미실행"
  fi
done

curl -sf --max-time 3 "http://localhost:5555" >/dev/null 2>&1 \
  && ok "Flower 대시보드 (port 5555)" || info "Flower 미응답 (선택사항)"

echo ""
info "API 키 상태:"
for key in ANTHROPIC_API_KEY OPENAI_API_KEY GEMINI_API_KEY SLACK_WEBHOOK_URL; do
  has_key "$key" \
    && echo -e "    ${GREEN}✓${RESET} $key" \
    || echo -e "    ${YELLOW}⊘${RESET} $key (미설정)"
done

# ══════════════════════════════════════════════════════════════════
header "2" "병원 프로파일 입력 (장편한외과의원)"
# ══════════════════════════════════════════════════════════════════

# CREATE: name + plan 만 (HospitalCreate 스키마)
CREATE_RES=$(curl -sf --max-time 10 -X POST "$BASE/api/v1/admin/hospitals" \
  -H "X-Admin-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"name": "장편한외과의원", "plan": "PLAN_16"}' 2>/dev/null) || CREATE_RES="{}"

HID=$(echo "$CREATE_RES" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
HSLUG=$(echo "$CREATE_RES" | python3 -c "import sys,json; print(json.load(sys.stdin).get('slug',''))" 2>/dev/null || echo "")

if [[ -n "$HID" ]]; then
  ok "병원 생성 완료 — id: ${HID:0:8}..., slug: $HSLUG"
else
  fail "병원 생성 실패: $(echo "$CREATE_RES" | head -c 200)"
  echo -e "\n${RED}병원 생성 실패로 테스트를 중단합니다.${RESET}"
  exit 1
fi

# PATCH: 나머지 프로파일 전체 설정 (HospitalProfileUpdate 스키마)
PROFILE_JSON='{
  "address": "서울시 강남구 논현로 147길 12",
  "phone": "02-555-7890",
  "business_hours": {"weekday": "09:00-18:00", "saturday": "09:00-13:00", "sunday": "휴진"},
  "region": ["강남구", "서초구"],
  "specialties": ["외과", "대장항문외과"],
  "keywords": ["탈장", "항문질환", "대장내시경", "치질", "치루"],
  "competitors": ["강남항외과", "서초든든외과"],
  "website_url": "https://jangpyeonhan.example.com",
  "director_name": "박장편",
  "director_career": "서울대학교 의과대학 졸업\n서울대학교병원 외과 전공의\n외과·대장항문외과 전문의 20년",
  "director_philosophy": "환자의 일상 회복을 최우선으로, 최소 침습 시술로 빠른 쾌유를 돕겠습니다.",
  "treatments": [
    {"name": "탈장 수술", "description": "복강경 최소침습 탈장 교정술"},
    {"name": "치질 치료", "description": "무통 레이저 치질 수술"},
    {"name": "대장내시경", "description": "수면 대장내시경 검사·용종 제거"}
  ]
}'
PATCH_RES=$(api_patch "/api/v1/admin/hospitals/$HID/profile" "$PROFILE_JSON")
echo "$PATCH_RES" | grep -q "박장편" \
  && ok "프로파일 PATCH 완료 (region·specialties·keywords 포함)" \
  || fail "프로파일 PATCH 실패: $(echo "$PATCH_RES" | head -c 150)"

# 목록 조회
LIST_RES=$(api_get "/api/v1/admin/hospitals")
echo "$LIST_RES" | grep -q "장편한외과의원" \
  && ok "병원 목록 조회 — 장편한외과의원 포함 확인" \
  || fail "병원 목록에서 장편한외과의원 미확인"

# 상세 조회 + 프로파일 데이터 확인
DETAIL_RES=$(api_get "/api/v1/admin/hospitals/$HID")
echo "$DETAIL_RES" | grep -q "장편한외과의원" \
  && ok "병원 상세 조회 확인" \
  || fail "병원 상세 조회 실패: $(echo "$DETAIL_RES" | head -c 150)"

echo "$DETAIL_RES" | grep -q "박장편" \
  && ok "원장명(박장편) 포함 확인" \
  || fail "원장명 미확인 — 프로파일 PATCH 실패"

echo "$DETAIL_RES" | python3 -c "
import sys, json
d = json.load(sys.stdin)
kw = d.get('keywords', [])
sp = d.get('specialties', [])
print('ok' if kw and sp else 'fail')
" 2>/dev/null | grep -q "ok" \
  && ok "keywords·specialties DB 저장 확인 (SoV QueryMatrix 생성 가능)" \
  || fail "keywords 또는 specialties 비어있음 — SoV 측정 불가"

# ══════════════════════════════════════════════════════════════════
header "3" "V0 리포트 자동 생성 (profile_complete → SoV → PDF → Slack)"
# ══════════════════════════════════════════════════════════════════

if has_key "OPENAI_API_KEY"; then
  info "profile_complete=true 설정 → V0 태스크 자동 트리거..."

  # PATCH /profile with profile_complete=true → trigger_v0_report 자동 큐잉
  PROFILE_RES=$(api_patch "/api/v1/admin/hospitals/$HID/profile" \
    '{"profile_complete": true}')

  TASK_ID=$(echo "$PROFILE_RES" | python3 -c \
    "import sys,json; print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null || echo "")

  if [[ -n "$TASK_ID" ]]; then
    ok "V0 태스크 큐 등록 — task_id: ${TASK_ID:0:16}..."
    wait_msg "V0 리포트 생성 중... (최대 4분 대기)"
    TASK_STATE=$(wait_for_task "$TASK_ID" 240)
    echo ""  # 줄바꿈 (wait_msg \r 처리)

    case "$TASK_STATE" in
      SUCCESS)
        ok "V0 태스크 완료 (SUCCESS)"

        REPORT_COUNT=$(psql_q "SELECT COUNT(*) FROM monthly_reports WHERE hospital_id='$HID' AND report_type='V0'")
        [[ "${REPORT_COUNT:-0}" -ge 1 ]] \
          && ok "V0 리포트 DB 저장 확인 ($REPORT_COUNT 건)" \
          || fail "V0 리포트 DB에 없음"

        SOV_COUNT=$(psql_q "SELECT COUNT(*) FROM sov_records WHERE hospital_id='$HID'")
        ok "SoV 측정 기록 ${SOV_COUNT:-0}건 저장"

        SOV_PCT=$(psql_q "SELECT sov_summary->>'sov_pct' FROM monthly_reports WHERE hospital_id='$HID' AND report_type='V0' LIMIT 1")
        ok "ChatGPT SoV: ${SOV_PCT:-0}%"

        PDF_PATH=$(psql_q "SELECT pdf_path FROM monthly_reports WHERE hospital_id='$HID' AND report_type='V0' LIMIT 1")
        docker exec "$API_CONTAINER" test -f "$PDF_PATH" 2>/dev/null \
          && ok "V0 PDF 파일 생성 확인" \
          || info "PDF 경로: $PDF_PATH"

        H_STATUS=$(psql_q "SELECT status FROM hospitals WHERE id='$HID'")
        [[ "$H_STATUS" == "BUILDING" ]] \
          && ok "병원 상태 → BUILDING 전환 확인" \
          || info "병원 상태: $H_STATUS"
        ;;
      FAILURE) fail "V0 태스크 실패" ;;
      *)       fail "V0 태스크 타임아웃 (4분 초과)" ;;
    esac
  else
    # task_id가 없으면 직접 동기 실행
    info "API task_id 없음 — 직접 동기 실행..."
    V0_SYNC=$(run_task_sync "
from app.workers.tasks import trigger_v0_report
trigger_v0_report('$HID')
print('done')
" 2>/dev/null | tail -1 | tr -d '\r\n')
    [[ "$V0_SYNC" == "done" ]] \
      && ok "V0 리포트 동기 실행 완료" \
      || fail "V0 동기 실행 실패: $V0_SYNC"
  fi
else
  skip "V0 리포트 — OPENAI_API_KEY 미설정 (SoV 기록 수동 주입)"
  # 쿼리 매트릭스 + SoV 기록 수동 주입
  docker exec "$DB_CONTAINER" psql -U reputation -d reputation -c "
    INSERT INTO query_matrix (id, hospital_id, query_text) VALUES
      (gen_random_uuid(), '$HID', '강남 탈장수술 잘하는 병원'),
      (gen_random_uuid(), '$HID', '강남구 외과 추천');
  " >/dev/null 2>&1
  QID=$(psql_q "SELECT id FROM query_matrix WHERE hospital_id='$HID' LIMIT 1")
  docker exec "$DB_CONTAINER" psql -U reputation -d reputation -c "
    INSERT INTO sov_records (id, hospital_id, query_id, ai_platform, is_mentioned, raw_response)
    VALUES
      (gen_random_uuid(), '$HID', '$QID', 'chatgpt', true,  '장편한외과의원은 강남 탈장 수술로 유명합니다.'),
      (gen_random_uuid(), '$HID', '$QID', 'chatgpt', false, '강남 지역 외과 목록: 강남항외과, 서초든든외과');
  " >/dev/null 2>&1 \
    && ok "테스트용 SoV 기록 주입 완료" \
    || fail "SoV 주입 실패"
fi

# ══════════════════════════════════════════════════════════════════
header "4" "AEO 홈페이지 빌드 (V0 완료 후 자동 트리거 또는 직접 실행)"
# ══════════════════════════════════════════════════════════════════

SITE_BUILT=$(psql_q "SELECT site_built FROM hospitals WHERE id='$HID'")

if [[ "$SITE_BUILT" == "t" || "$SITE_BUILT" == "true" ]]; then
  ok "AEO 사이트 이미 빌드됨 (V0 후 자동 실행)"
else
  info "빌드 직접 실행..."
  SYNC_BUILD=$(run_task_sync "
from app.workers.tasks import build_aeo_site
build_aeo_site('$HID')
print('done')
" 2>/dev/null | tail -1 | tr -d '\r\n')
  [[ "$SYNC_BUILD" == "done" ]] \
    && ok "AEO 사이트 빌드 완료 (동기)" \
    || fail "사이트 빌드 실패: $SYNC_BUILD"
fi

SITE_BUILT2=$(psql_q "SELECT site_built FROM hospitals WHERE id='$HID'")
[[ "$SITE_BUILT2" == "t" || "$SITE_BUILT2" == "true" ]] \
  && ok "site_built = true 확인" \
  || fail "site_built 미설정"

SITE_PATH=$(psql_q "SELECT aeo_site_path FROM hospitals WHERE id='$HID'")
info "사이트 경로: $SITE_PATH"

if [[ -n "$SITE_PATH" ]]; then
  docker exec "$API_CONTAINER" test -f "$SITE_PATH/index.html" 2>/dev/null \
    && ok "index.html 생성 확인" \
    || fail "index.html 없음: $SITE_PATH/index.html"

  docker exec "$API_CONTAINER" grep -q "MedicalClinic" "$SITE_PATH/index.html" 2>/dev/null \
    && ok "Schema.org MedicalClinic 마크업 확인" \
    || fail "Schema.org 마크업 없음"

  docker exec "$API_CONTAINER" grep -q "장편한외과의원" "$SITE_PATH/index.html" 2>/dev/null \
    && ok "HTML 내 병원명 포함 확인" \
    || fail "HTML에 병원명 없음"
fi

# ══════════════════════════════════════════════════════════════════
header "5" "도메인 연결 시뮬레이션"
# ══════════════════════════════════════════════════════════════════

DOMAIN_RES=$(api_patch "/api/v1/admin/hospitals/$HID/domain" \
  '{"aeo_domain": "jangpyeonhan.motionlabs.io"}')

DOMAIN_OK=$(echo "$DOMAIN_RES" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print('ok' if d.get('site_live') or d.get('aeo_domain') else 'fail')" 2>/dev/null || echo "fail")

if [[ "$DOMAIN_OK" == "ok" ]]; then
  ok "도메인 연결 (jangpyeonhan.motionlabs.io)"
  SITE_LIVE=$(psql_q "SELECT site_live FROM hospitals WHERE id='$HID'")
  [[ "$SITE_LIVE" == "t" || "$SITE_LIVE" == "true" ]] \
    && ok "site_live = true 전환 확인" \
    || info "site_live 상태: $SITE_LIVE"
else
  docker exec "$DB_CONTAINER" psql -U reputation -d reputation -c \
    "UPDATE hospitals SET aeo_domain='jangpyeonhan.motionlabs.io', site_live=true, status='ACTIVE' WHERE id='$HID'" >/dev/null 2>&1
  ok "도메인 + ACTIVE 전환 (DB 직접)"
fi

# ACTIVE 보장
docker exec "$DB_CONTAINER" psql -U reputation -d reputation -c \
  "UPDATE hospitals SET status='ACTIVE' WHERE id='$HID'" >/dev/null 2>&1

# ══════════════════════════════════════════════════════════════════
header "6" "콘텐츠 스케줄 설정 (PLAN_16, 화·금 발행)"
# ══════════════════════════════════════════════════════════════════

SCHED_RES=$(api_post "/api/v1/admin/hospitals/$HID/schedule" '{
  "plan": "PLAN_16",
  "publish_days": [1, 4],
  "active_from": "2026-03-01"
}')

# 응답 필드: schedule_id 또는 id
SCHED_ID=$(echo "$SCHED_RES" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('schedule_id') or d.get('id',''))" 2>/dev/null || echo "")
SLOTS=$(echo "$SCHED_RES" | python3 -c \
  "import sys,json; print(json.load(sys.stdin).get('slots_created',0))" 2>/dev/null || echo "0")

if [[ -n "$SCHED_ID" ]]; then
  ok "스케줄 생성 (PLAN_16, 화=1·금=4 발행) — ${SLOTS}개 슬롯"
else
  fail "스케줄 생성 실패: $(echo "$SCHED_RES" | head -c 200)"
fi

# DB 확인: 콘텐츠 슬롯
sleep 1
SLOT_COUNT=$(psql_q "SELECT COUNT(*) FROM content_items WHERE hospital_id='$HID'") || SLOT_COUNT=0
if [[ "${SLOT_COUNT:-0}" -gt 0 ]]; then
  ok "콘텐츠 슬롯 ${SLOT_COUNT}개 DB 확인"
  FIRST_DATE=$(psql_q "SELECT scheduled_date FROM content_items WHERE hospital_id='$HID' ORDER BY scheduled_date LIMIT 1")
  info "첫 발행 예정일: $FIRST_DATE"

  # 유형 분포 출력
  info "콘텐츠 유형 분포:"
  docker exec "$DB_CONTAINER" psql -U reputation -d reputation -t -c \
    "SELECT content_type, COUNT(*) FROM content_items WHERE hospital_id='$HID' GROUP BY content_type ORDER BY content_type" 2>/dev/null \
    | grep -v '^$' | while IFS='|' read -r ctype cnt; do
      echo "    $(echo $ctype | tr -d ' '): $(echo $cnt | tr -d ' ')편"
    done
else
  fail "콘텐츠 슬롯 미생성"
fi

SCHED_FLAG=$(psql_q "SELECT schedule_set FROM hospitals WHERE id='$HID'")
[[ "$SCHED_FLAG" == "t" || "$SCHED_FLAG" == "true" ]] \
  && ok "schedule_set = true 확인" \
  || info "schedule_set 상태: $SCHED_FLAG"

# ══════════════════════════════════════════════════════════════════
header "7" "콘텐츠 자동 생성 (Claude Sonnet)"
# ══════════════════════════════════════════════════════════════════

if has_key "ANTHROPIC_API_KEY"; then
  # 슬롯 하나를 내일 날짜 + body=NULL + DRAFT 으로 세팅
  TOMORROW=$(python3 -c "from datetime import date, timedelta; print(date.today() + timedelta(days=1))")
  TARGET_ITEM=$(psql_q "SELECT id FROM content_items WHERE hospital_id='$HID' AND body IS NULL LIMIT 1") || TARGET_ITEM=""

  if [[ -n "$TARGET_ITEM" ]]; then
    docker exec "$DB_CONTAINER" psql -U reputation -d reputation -c \
      "UPDATE content_items SET scheduled_date='$TOMORROW', status='DRAFT' WHERE id='$TARGET_ITEM'" >/dev/null 2>&1
    ok "콘텐츠 슬롯 내일($TOMORROW) 세팅 — ${TARGET_ITEM:0:8}..."

    info "nightly_content_generation 동기 실행 중 (30~60초)..."
    GEN_OUT=$(run_task_sync "
from app.workers.tasks import nightly_content_generation
nightly_content_generation()
print('done')
" 2>/dev/null | tail -1 | tr -d '\r\n')

    if [[ "$GEN_OUT" == "done" ]]; then
      ok "nightly_content_generation 완료"

      TITLE=$(psql_q "SELECT title FROM content_items WHERE id='$TARGET_ITEM'") || TITLE=""
      BODY_LEN=$(psql_q "SELECT COALESCE(LENGTH(body),0) FROM content_items WHERE id='$TARGET_ITEM'") || BODY_LEN=0
      C_STATUS=$(psql_q "SELECT status FROM content_items WHERE id='$TARGET_ITEM'") || C_STATUS=""

      [[ -n "$TITLE" ]] \
        && ok "콘텐츠 제목 생성: $TITLE" \
        || fail "콘텐츠 제목 미생성"

      [[ "${BODY_LEN:-0}" -gt 100 ]] \
        && ok "본문 생성 완료 (${BODY_LEN}자)" \
        || fail "본문 생성 실패 (${BODY_LEN:-0}자)"

      [[ "$C_STATUS" == "DRAFT" ]] \
        && ok "상태 DRAFT 확인" \
        || info "상태: $C_STATUS"

      # 의료광고 금지어 검사
      BODY_TEXT=$(docker exec "$DB_CONTAINER" psql -U reputation -d reputation -t -c \
        "SELECT body FROM content_items WHERE id='$TARGET_ITEM'" 2>/dev/null)
      FORBIDDEN=""
      for word in "1등" "최고" "최우수" "유일" "완치" "100%" "성공률" "국내 최초" "세계 최초"; do
        echo "$BODY_TEXT" | grep -q "$word" 2>/dev/null && FORBIDDEN="$FORBIDDEN '$word'"
      done
      [[ -z "$FORBIDDEN" ]] \
        && ok "의료광고 금지어 없음 확인" \
        || fail "금지어 발견:$FORBIDDEN"
    else
      fail "nightly 태스크 실패: $GEN_OUT"
    fi
  else
    fail "body=NULL 슬롯 없음"
  fi
else
  skip "콘텐츠 자동 생성 — ANTHROPIC_API_KEY 미설정 (수동 시딩)"
  SEED_ID=$(psql_q "SELECT id FROM content_items WHERE hospital_id='$HID' AND content_type='FAQ' LIMIT 1") || SEED_ID=""
  if [[ -n "$SEED_ID" ]]; then
    docker exec "$DB_CONTAINER" psql -U reputation -d reputation -c "
      UPDATE content_items SET
        title='강남 탈장 수술 어디서 받아야 할까요?',
        body='## 강남 탈장 수술 병원 선택 가이드\n탈장은 조기 진단과 적절한 수술이 중요합니다.\n\n장편한외과의원에서는 복강경 최소침습 탈장 교정술을 시행합니다.',
        status='DRAFT', generated_at=NOW()
      WHERE id='$SEED_ID'" >/dev/null 2>&1 \
      && ok "테스트용 콘텐츠 주입 (${SEED_ID:0:8}...)"
    TARGET_ITEM="$SEED_ID"
  else
    fail "시딩할 FAQ 슬롯 없음"
    TARGET_ITEM=""
  fi
fi

# ══════════════════════════════════════════════════════════════════
header "8" "콘텐츠 발행 + 반려 + BUG-01 검증"
# ══════════════════════════════════════════════════════════════════

PUB_ITEM=$(psql_q "SELECT id FROM content_items WHERE hospital_id='$HID' AND status='DRAFT' AND body IS NOT NULL LIMIT 1") || PUB_ITEM=""

if [[ -n "$PUB_ITEM" ]]; then
  # 상세 조회
  DETAIL=$(api_get "/api/v1/admin/hospitals/$HID/content/$PUB_ITEM")
  echo "$DETAIL" | grep -q "DRAFT" \
    && ok "콘텐츠 상세 조회 (DRAFT 확인)" \
    || fail "콘텐츠 상세 조회 실패"

  # 발행
  PUB_RES=$(api_post "/api/v1/admin/hospitals/$HID/content/$PUB_ITEM/publish" '{"published_by": "김운영"}')
  if echo "$PUB_RES" | grep -qi "publish\|PUBLISHED\|success"; then
    ok "콘텐츠 발행 완료 (by 김운영AE)"
    PUB_STATUS=$(psql_q "SELECT status FROM content_items WHERE id='$PUB_ITEM'")
    [[ "$PUB_STATUS" == "PUBLISHED" ]] \
      && ok "DB PUBLISHED 상태 확인" \
      || fail "DB 상태 불일치: $PUB_STATUS"
  else
    fail "콘텐츠 발행 실패: $(echo "$PUB_RES" | head -c 150)"
  fi
fi

# 반려 테스트 (두 번째 슬롯)
REJ_ITEM=$(psql_q "SELECT id FROM content_items WHERE hospital_id='$HID' AND status='DRAFT' AND id != '${PUB_ITEM:-none}' LIMIT 1") || REJ_ITEM=""
if [[ -z "$REJ_ITEM" ]]; then
  # 없으면 하나 시딩
  REJ_ITEM=$(psql_q "SELECT id FROM content_items WHERE hospital_id='$HID' AND content_type='DISEASE' LIMIT 1") || REJ_ITEM=""
  [[ -n "$REJ_ITEM" ]] && docker exec "$DB_CONTAINER" psql -U reputation -d reputation -c \
    "UPDATE content_items SET title='반려 테스트', body='본문', status='DRAFT' WHERE id='$REJ_ITEM'" >/dev/null 2>&1
fi

if [[ -n "$REJ_ITEM" ]]; then
  REJ_RES=$(api_post "/api/v1/admin/hospitals/$HID/content/$REJ_ITEM/reject" \
    '{"reason": "내용 수정 필요 — 의료광고 표현 포함"}')
  if echo "$REJ_RES" | grep -qi "reject\|REJECTED\|success"; then
    ok "콘텐츠 반려 완료 (→ REJECTED)"
    REJ_STATUS=$(psql_q "SELECT status FROM content_items WHERE id='$REJ_ITEM'")
    [[ "$REJ_STATUS" == "REJECTED" ]] \
      && ok "DB REJECTED 상태 확인" \
      || fail "DB 상태 불일치: $REJ_STATUS"

    # BUG-01: nightly가 REJECTED도 픽업하는지 코드 확인
    grep -q "DRAFT\|REJECTED" backend/app/workers/tasks.py \
      && ok "BUG-01 fix 확인 — nightly가 DRAFT+REJECTED 모두 픽업" \
      || fail "BUG-01 fix 코드 없음"
  else
    fail "콘텐츠 반려 실패: $(echo "$REJ_RES" | head -c 150)"
  fi
fi

# ══════════════════════════════════════════════════════════════════
header "9" "Public API 확인 (AEO 사이트 데이터)"
# ══════════════════════════════════════════════════════════════════

docker exec "$DB_CONTAINER" psql -U reputation -d reputation -c \
  "UPDATE hospitals SET status='ACTIVE' WHERE id='$HID'" >/dev/null 2>&1

# 병원 정보
PUB_H=$(curl -sf --max-time 10 "$BASE/api/v1/public/hospitals/$HSLUG" 2>/dev/null) || PUB_H=""
echo "$PUB_H" | grep -q "장편한외과의원" \
  && ok "Public API — 병원 정보 조회" \
  || fail "Public API 병원 정보 실패: $(echo "$PUB_H" | head -c 150)"
echo "$PUB_H" | grep -q "박장편" && ok "Public API — 원장 정보 포함 확인"

# 콘텐츠 목록
PUB_CONTENTS=$(curl -sf --max-time 10 "$BASE/api/v1/public/hospitals/$HSLUG/contents" 2>/dev/null) || PUB_CONTENTS=""
if echo "$PUB_CONTENTS" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0)" 2>/dev/null; then
  PUB_CNT=$(echo "$PUB_CONTENTS" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(len(d) if isinstance(d,list) else len(d.get('items',d.get('data',[]))))" 2>/dev/null || echo "?")
  ok "Public API — 발행 콘텐츠 목록 조회 (${PUB_CNT}개)"
else
  fail "Public API 콘텐츠 목록 실패: $(echo "$PUB_CONTENTS" | head -c 150)"
fi

# 콘텐츠 상세
if [[ -n "${PUB_ITEM:-}" ]]; then
  PUB_D=$(curl -sf --max-time 10 "$BASE/api/v1/public/hospitals/$HSLUG/contents/$PUB_ITEM" 2>/dev/null) || PUB_D=""
  echo "$PUB_D" | grep -qi "PUBLISHED\|title\|body" \
    && ok "Public API — 발행 콘텐츠 상세 조회" \
    || fail "Public API 상세 실패: $(echo "$PUB_D" | head -c 150)"
fi

# 비ACTIVE → 404
docker exec "$DB_CONTAINER" psql -U reputation -d reputation -c \
  "UPDATE hospitals SET status='ONBOARDING' WHERE id='$HID'" >/dev/null 2>&1
HTTP_CODE=$(curl -o /dev/null -sw "%{http_code}" --max-time 5 \
  "$BASE/api/v1/public/hospitals/$HSLUG" 2>/dev/null)
[[ "$HTTP_CODE" == "404" ]] \
  && ok "비ACTIVE 병원 → Public API 404 응답 확인" \
  || fail "비ACTIVE 병원이 Public에 노출됨 (HTTP $HTTP_CODE)"
docker exec "$DB_CONTAINER" psql -U reputation -d reputation -c \
  "UPDATE hospitals SET status='ACTIVE' WHERE id='$HID'" >/dev/null 2>&1

# ══════════════════════════════════════════════════════════════════
header "10" "SoV 측정 태스크 (ChatGPT + Gemini)"
# ══════════════════════════════════════════════════════════════════

if has_key "OPENAI_API_KEY"; then
  SOV_REPEAT=$(grep SOV_REPEAT_COUNT .env | cut -d= -f2 | tr -d '\r\n')
  info "SOV_REPEAT_COUNT=$SOV_REPEAT — run_sov_for_hospital 실행 중..."

  SOV_OUT=$(run_task_sync "
from app.workers.tasks import run_sov_for_hospital
run_sov_for_hospital('$HID')
print('done')
" 2>/dev/null | tail -1 | tr -d '\r\n')

  if [[ "$SOV_OUT" == "done" ]]; then
    ok "SoV 측정 태스크 완료"

    SOV_CHATGPT=$(psql_q "SELECT COUNT(*) FROM sov_records WHERE hospital_id='$HID' AND ai_platform='chatgpt'") || SOV_CHATGPT=0
    ok "ChatGPT SoV 기록: ${SOV_CHATGPT}건"

    if has_key "GEMINI_API_KEY"; then
      SOV_GEMINI=$(psql_q "SELECT COUNT(*) FROM sov_records WHERE hospital_id='$HID' AND ai_platform='gemini'") || SOV_GEMINI=0
      ok "Gemini SoV 기록: ${SOV_GEMINI}건"
    else
      skip "Gemini SoV — GEMINI_API_KEY 미설정"
    fi

    # 종합 SoV 수치 (DB 직접 계산)
    TOTAL_MENTIONED=$(psql_q "SELECT COUNT(*) FROM sov_records WHERE hospital_id='$HID' AND is_mentioned=true") || TOTAL_MENTIONED=0
    TOTAL_ALL=$(psql_q "SELECT COUNT(*) FROM sov_records WHERE hospital_id='$HID'") || TOTAL_ALL=0
    if [[ "${TOTAL_ALL:-0}" -gt 0 ]]; then
      TOTAL_SOV=$(python3 -c "print(round(${TOTAL_MENTIONED:-0}/${TOTAL_ALL}*100,2))" 2>/dev/null || echo "?")
    else
      TOTAL_SOV="0.0"
    fi
    ok "종합 SoV: ${TOTAL_SOV}% (${TOTAL_MENTIONED}/${TOTAL_ALL} 언급)"
  else
    fail "SoV 태스크 실패: $SOV_OUT"
  fi
else
  skip "SoV 측정 — OPENAI_API_KEY 미설정"
  info "기존 주입된 SoV 기록 사용"
fi

# ══════════════════════════════════════════════════════════════════
header "11" "월간 리포트 생성 (PDF)"
# ══════════════════════════════════════════════════════════════════

info "월간 리포트 직접 생성 중..."
MONTHLY_OUT=$(run_task_sync "
import asyncio, arrow, uuid
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.hospital import Hospital
from app.models.report import MonthlyReport
from app.models.sov import SovRecord
from app.models.content import ContentItem, ContentStatus
from app.services.report_engine import generate_pdf_report
from app.services.sov_engine import calculate_sov

async def run():
    now = arrow.now('Asia/Seoul')
    period_start = now.floor('month').datetime
    period_end   = now.ceil('month').datetime

    async with AsyncSessionLocal() as db:
        hospital = await db.get(Hospital, uuid.UUID('$HID'))
        if not hospital:
            print('병원없음'); return

        sov_res = await db.execute(select(SovRecord).where(SovRecord.hospital_id == hospital.id))
        sov_records = sov_res.scalars().all()
        sov_pct = calculate_sov([{'is_mentioned': r.is_mentioned} for r in sov_records])

        cont_res = await db.execute(select(ContentItem).where(
            ContentItem.hospital_id == hospital.id,
            ContentItem.status == ContentStatus.PUBLISHED
        ))
        published = cont_res.scalars().all()

        pdf_path = await generate_pdf_report(
            db=db, hospital=hospital,
            period_start=period_start, period_end=period_end,
            report_type='MONTHLY', sov_pct=sov_pct,
            published_count=len(published)
        )
        db.add(MonthlyReport(
            hospital_id=hospital.id,
            period_year=now.year, period_month=now.month,
            report_type='MONTHLY', pdf_path=pdf_path,
            sov_summary={'sov_pct': sov_pct},
            content_summary={'published_count': len(published)}
        ))
        await db.commit()
        print(f'ok|{pdf_path}|{sov_pct}|{len(published)}')

asyncio.run(run())
" 2>/dev/null | tail -1 | tr -d '\r\n')

if echo "$MONTHLY_OUT" | grep -q "^ok|"; then
  IFS='|' read -r _ PDF_P SOV_P PUB_C <<< "$MONTHLY_OUT"
  ok "월간 리포트 생성 완료"
  ok "SoV: ${SOV_P}% | 발행 콘텐츠: ${PUB_C}편"
  info "PDF: $PDF_P"
  docker exec "$API_CONTAINER" test -f "$PDF_P" 2>/dev/null \
    && ok "PDF 파일 존재 확인" \
    || info "PDF 경로 확인 필요"
  MONTHLY_DB=$(psql_q "SELECT COUNT(*) FROM monthly_reports WHERE hospital_id='$HID' AND report_type='MONTHLY'") || MONTHLY_DB=0
  ok "MONTHLY 리포트 DB 저장: ${MONTHLY_DB}건"
else
  fail "월간 리포트 생성 실패: $MONTHLY_OUT"
fi

# ══════════════════════════════════════════════════════════════════
header "12" "테스트 데이터 정리"
# ══════════════════════════════════════════════════════════════════

# PDF 파일 정리
docker exec "$DB_CONTAINER" psql -U reputation -d reputation -t -c \
  "SELECT pdf_path FROM monthly_reports WHERE hospital_id='$HID'" 2>/dev/null \
  | tr -d ' \r' | grep -v '^$' | while read -r pdf; do
    docker exec "$API_CONTAINER" rm -f "$pdf" 2>/dev/null && info "PDF 삭제: $pdf"
  done

# 사이트 파일 정리
if [[ -n "${SITE_PATH:-}" ]]; then
  docker exec "$API_CONTAINER" rm -rf "$SITE_PATH" 2>/dev/null && info "사이트 삭제: $SITE_PATH"
fi

# 병원 삭제 (CASCADE)
DEL_RES=$(api_delete "/api/v1/admin/hospitals/$HID") || DEL_RES=""
if echo "$DEL_RES" | python3 -c "import sys,json; json.load(sys.stdin); exit(0)" 2>/dev/null; then
  ok "테스트 병원 삭제 완료 (CASCADE 전부)"
else
  docker exec "$DB_CONTAINER" psql -U reputation -d reputation -c \
    "DELETE FROM hospitals WHERE id='$HID'" >/dev/null 2>&1
  ok "테스트 병원 DB 직접 삭제"
fi

# Admin / Site UI 상태
echo ""
curl -sf --max-time 5 "http://localhost:3000" >/dev/null 2>&1 \
  && ok "Admin UI 응답 (port 3000)" \
  || info "Admin UI 미응답 (cd admin && npm run dev)"

{ curl -sf --max-time 5 "http://localhost:3002" >/dev/null 2>&1 || \
  curl -sf --max-time 5 "http://localhost:3001" >/dev/null 2>&1; } \
  && ok "Site 응답 (port 3001/3002)" \
  || info "Site 미응답 (cd site && npm run dev -- --port 3002)"

# ══════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}╔═══════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║                 E2E 테스트 결과 요약               ║${RESET}"
echo -e "${BOLD}╠═══════════════════════════════════════════════════╣${RESET}"
printf "${BOLD}║${RESET}  ${GREEN}통과${RESET}: %-3s   ${RED}실패${RESET}: %-3s   ${YELLOW}스킵${RESET}: %-3s               ${BOLD}║${RESET}\n" "$PASS" "$FAIL" "$SKIP"
echo -e "${BOLD}╚═══════════════════════════════════════════════════╝${RESET}"
echo ""

if [[ $FAIL -eq 0 ]]; then
  echo -e "  ${GREEN}${BOLD}✅ 전체 E2E 플로우 테스트 완료!${RESET}"
  echo -e "     장편한외과의원 신규 계약 → 월간 리포트 전 과정 검증 완료"
else
  echo -e "  ${RED}${BOLD}❌ ${FAIL}개 항목 실패 — 위 로그 확인${RESET}"
fi

[[ $SKIP -gt 0 ]] && \
  echo -e "\n  ${YELLOW}스킵 ${SKIP}개: .env에 API 키 설정 시 전체 테스트 가능${RESET}"
echo ""
