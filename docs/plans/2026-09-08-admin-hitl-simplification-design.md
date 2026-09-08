# 무결성 수정 + Admin HITL 최소화 설계 — 2026-09-08

문서 버전: **1.0** · 기준 커밋: `59acabe` · 근거: [무결성·HITL 검토](../reviews/2026-09-08-integrity-hitl-review.md)
상태: **설계 승인됨 · Phase 0 진행 중 (PR-0A 완료 2026-09-08)** (접근 A "예외 인박스형 재구성" + SEC-01 "BFF 서명 actor")

## 1. 목표와 비목표

**목표**
1. 검토에서 드러난 HIGH 15건·연결된 MED를 고쳐 자동화의 구멍과 화면-서버 모순을 없앤다.
2. Admin을 "운영 콘솔"에서 "예외 콘솔"로 바꾼다. 사람은 CLAUDE.md v2.5가 정한 네 가지만 한다: **계약 인수 · 공식 사실과 공개 주소 결정 · 자동 검토가 못 푼 예외 · 원장 보고서 전달.**
3. 성공 지표: 상담→운영 시작 **2페이지 / ≈12 상호작용 / 8 결정**, 월 정상 운영 **1~2페이지 / ≤5 클릭**. 정상 경로에 재실행·재처리·수동 승인 버튼 **0개**.

**비목표**
- 백엔드 파이프라인(생성·측정·리포트) 자체의 재설계. 결함 수정만.
- 공개 사이트(/site) 변경. H-01의 사이트 쪽은 변경하지 않는다(이미 올바르게 fail-closed).
- 새 기능. 요금제·콘텐츠 유형·측정 규칙은 그대로.
- Slack 정상 발행 알림 재도입(v2.5 정책 유지).

**전제로 확정하는 제품 결정**
- 요금제의 권위는 **계약 인수 기록**(`Handoff`)이다. 병원·일정은 이를 읽기만 한다.
- 공개 후 확인은 **표본**(`sequence_no==1` 또는 공개 후 본문 편집)만이다. 나머지 글은 확인 대상이 아니다.
- 환자 질문·노출 보완은 **읽기 전용**이다. 사람이 우선순위·완료를 편집하지 않는다(시스템이 덮어쓰므로).
- "수동 초안"·"수동 발행"·"재측정"은 정상 화면에서 사라지고, 운영 센터의 **인시던트에 묶인 서버 허용 행동**으로만 존재한다.

## 2. 목표 HITL 계약 — 사람 행동 확정 목록

이 목록에 없는 버튼은 admin에 존재하지 않는다.

| 구간 | 사람 행동 | 화면 |
|---|---|---|
| 계약 | 병원 생성(리드 연결 자동), 계약 번호·효력일·요금제·담당 AE 입력 | `/hospitals/new` |
| 공식 사실 | 병원·원장·진료·연락처·공식 채널 편집·저장 (자동 입력은 배경에서 채움, 사람은 비어 있거나 충돌한 항목만) | `/hospitals/{id}/info` |
| 공개 주소 | 자기 도메인 입력·제거 (확인은 자동 폴링) | `/hospitals/{id}/info` |
| 공개 페이지 브랜드 | 대표색·첫 화면 문구 **override**(기본값 자동), 로고·실사진 업로드 + **사진 권리 정보** | `/hospitals/{id}/info` |
| 발행 요일 | 요일 선택(요금제 기본값 미리 채움) | `/hospitals/{id}/content` |
| 서비스 | 일시정지 / 재개 | 헤더 |
| 예외 | ESCALATED 운영 기준 초안: 자동 검수 finding을 읽고 **수정 후 재검수 요청** 또는 **근거를 적고 예외 승인** | `/hospitals/{id}` 현황 → 예외 카드 |
| 예외 | 근거 노트 노이즈 제외(→ 승인 stale, 자동 재검수) | 예외 카드 안 근거 보기 |
| 예외 | 인시던트 담당 지정·확인·서버가 허용한 복구 | `/operations` |
| 공개 후 확인 | 표본 글 1편 "확인 완료" | `/hospitals/{id}/content` |
| 보고서 | 원장용 PDF 열기, 전달 기록·정정·철회 | `/hospitals/{id}/reports` |
| 계정 | 운영자 계정 관리 | `/accounts` |

## 3. Phase 0 — 무결성 수정 (Admin 재구성의 전제)

예비 버튼을 없애려면 자동화가 실제로 구멍 없이 돌아야 한다. 도메인별 PR 4개. 각 PR은 결함별 회귀 테스트를 **먼저** 추가한다.

### PR-0A 생애주기·활성화·도메인 (H-05, H-06, H-07, M-10, M-11, M-13)
- ACTIVE 전환 **단일 진입점**: `hospital_activation.activate(...)`만 `status=ACTIVE`를 쓴다. `domain_verification.py:123-127`, `operations.py:801-808`의 직접 기록을 이 함수 호출로 교체. PAUSED면 `HospitalNotActivatable`. 활성화 후 `site_revalidate` 호출.
- `pause`/`resume`가 `site_revalidate`를 호출. `resume`는 자기 도메인이면 DNS/live 증거를 갱신한 뒤 활성화.
- admin lib에 **단일 판정** `publicServiceState(hospital)` 추가: 백엔드 `_has_public_site`와 동일하게 `status === ACTIVE && site_live` → `live`, `PAUSED` → `paused`, 그 외 → `not_live` (ACTIVE는 활성화 시점에 게이트를 통과했음을 뜻하고, 운영 중 `profile_complete` 해제는 같은 PR에서 409로 막는다). `site_live`를 직접 읽는 6곳을 이 함수로 교체.
- `resume` 버튼 조건에서 `schedule_set` 제거.
- 프로필 체크리스트의 도메인 상태를 배지와 같은 `liveCheckProvesServing`으로.
- 운영 중 병원의 `profile_complete=false` PATCH는 409 + 사유 (공개 사이트가 404가 되므로).
- 테스트: PAUSED에서 도메인 확인 → 409; resume → revalidate 호출; `schedule_set=false` resume 허용; 6개 표시 지점의 PAUSED 렌더.

### PR-0B 운영 기준·근거 (H-02, H-03, H-04, M-01, M-02, M-03, M-04)
- snapshot hash에 **노트 노이즈 상태**를 포함(`essence_engine.py:201`): `sha(source_id|content_hash|status|processed_at|excluded_note_ids)`. 노트 PATCH(`essence.py:545`)가 hash를 바꾸면 기존 승인 stale + 자동 재검수 큐잉. 자동 합성/검수가 `is_noise` 노트를 제외(`essence_auto_review.py:286-304`).
- 수동(예외) 승인 API(`essence.py:1672`)는 **현재 finding을 다시 계산**해 HARD finding이 남아 있으면 409. 승인 본문에 `override_reason`(≥20자) 필수, 감사 로그.
- "수동 초안 만들기" 엔드포인트 제거. 대신 ESCALATED 초안에 대해 `PATCH /drafts/{id}`(본문 수정) + `POST /drafts/{id}/re-review`(재검수 요청) 추가. 자동 승인 해제 플래그(`essence_auto_review.py:368-384`) 삭제. 기존 부분 선택 초안은 마이그레이션에서 `ARCHIVED`.
- 원문 추출 불가 URL 자료: 처리 run이 `UNEXTRACTABLE`로 종결하고 필수 집합에서 자동 제외(사람 "제외" 불필요). 24k 청크당 노트 상한 120개 적용, 초과분은 두 번째 pass에서 유사 노트끼리 병합.
- 비ACTIVE 병원의 ESCALATED도 인시던트 개설. "재처리" no-op 경로 삭제.
- 테스트: 노이즈 토글 → readiness `current=None` → 재검수 → 승인; HARD finding 있는 초안 예외 승인 409; UNEXTRACTABLE URL이 승인을 막지 않음.

### PR-0C 콘텐츠·발행 (H-01, H-08, H-09, H-14, M-05, M-06, M-07, M-15, M-21, M-22)
- **공개 가시성 단일 판정**: `content_visibility.assess(item, hospital) -> {visible: bool, blockers: [code]}`를 `api/public/site.py`의 `_is_public_safe_content`와 admin 직렬화(`content.py:1267,1320,1339`)가 **같이** 사용. admin은 `PUBLISHED`를 `공개 중` / `공개 보류(사유)`로 구분 표시. `compliance.publishable`을 이 판정으로 교체.
- 공개 글의 제목/본문 편집: 이미지 인증이 무효화되면 **자동 재인증 task** 큐잉(기존 인증 파이프라인 재사용). 재인증 실패 시 인시던트. IndexNow는 `visible=true`일 때만 제출.
- 생성 시도 fingerprint에서 `scheduled_date` 제거(`tasks.py:388-401`) — item id + content revision + essence id 기준. 예산 소진 시 인시던트 1건(dedupe).
- 수동 발행 API(`content.py:782`): 병원 row lock + `publicly_operational_hospital_predicate` + 일정 존재 검사. 제목/본문 `.strip()` 후 비어 있으면 400. `published_by`를 요청 본문에서 **제거**하고 검증된 actor로 기록.
- 일정 API(`content.py:300`)에서 `hospital.plan` 기록 **제거**. 일정은 `handoff.plan`을 읽는다. 일정 교체 시 옛 일정의 과거 미발행 슬롯을 새 일정으로 이관.
- 운영자 재생성이 생성 lease를 사용(`tasks.py:4091`과 동일 경로).
- 새 운영 기준 승인 시 relabel 대신: 저장된 본문은 그대로 두고 **재검사만** 기록(`_rescreen_content` 축소).
- 공개 글에 brief 편집 UI 제거. 공개 후 확인 버튼은 `is_human_post_publish_review_sample`이 참인 글에만.
- TS 타입: `compliance` optional, `first_publish_date: string | null`, `in_tracking_set` 추가, readiness 타입 단일화.
- 테스트: 제목 편집 → 인증 무효 → 재인증 큐잉 → visible 복귀; fingerprint가 날짜에 불변; 공백 발행 400; `published_by` 무시; PAUSED 병원 수동 발행 409; 일정 저장이 `hospital.plan` 불변.

### PR-0D 측정·리포트·복구·보안 (H-10, H-11, H-12, H-13, H-15, M-08, M-09, M-18, M-19, M-20)
- milestone 투영기: 리포트별 try/except 격리 + `LIMITED`를 최종으로 수용. "최종" 정의를 `monthly_report_delivery`의 게이트 함수 하나로 통일(09:00 요약·`tasks.py:8375`도 사용).
- V0: 비용 보류·예산 소진에 `safe_error_message` 기록 + 인시던트. "다시 실행"은 **기존 lineage 이어가기**만(전량 재구매 경로는 인시던트 행동으로 격리). V0 시작 API·worker에 `profile_complete` 게이트.
- 사이트 준비 복구: `OperationRun` claim/lease 선행, 시도 예산(3) 후 인시던트 1건, 오래된 100곳 고정 대신 `next_due_at` 순.
- 인시던트 **담당 지정** admin 배선 + 무배정 인시던트는 병원의 담당 AE에게 자동 지정.
- 전달 기록: 서버가 현재 artifact hash를 다시 계산해 요청 hash와 비교; 대체된 버전이면 409.
- 언급률 집계 5개 → 공통 `mention_rate(records, denominator=confirmed)` primitive 위에 재구성(수치 정의 변경 없음, 함수 통일).
- 환자 질문 비어 있으면 admin이 `/seed-from-matrix` 호출(수동 생성 UI 제거). 노출 보완 "갱신" POST·"완료" 제거.
- DB: `plan` enum에서 `PLAN_8` 제거 마이그레이션, `content_schedules.plan` CHECK.
- **SEC-01**: BFF가 `X-Admin-Actor-Assertion = base64(json{email, role, exp, nonce}).hmac(BFF_ACTOR_SECRET)`를 붙인다. 백엔드 `capture_admin_actor`가 서명·만료 검증; 사람 변경 라우트(POST/PATCH/DELETE under `/api/v1/admin/*`)는 단언 **필수**, 없으면 403. 시스템/배치 호출은 `X-Admin-Key` + `X-Admin-Actor-System: <job>`로 구분. 비밀은 Secret Manager(`BFF_ACTOR_SECRET`), terraform env 추가. `accounts.py:47`의 헤더 기반 계정 선택은 단언의 email만 사용.
- 테스트: LIMITED 리포트가 다른 병원 milestone을 막지 않음; V0 profile 전 409; 복구 재큐잉 상한; 서명 없는 변경 403, 위조 서명 403, 만료 403.

## 4. Phase 1 — Admin 재구성

### 4.1 정보 구조

```
/hospitals                    병원 목록 (공개 서비스 상태 · 콘텐츠 준비 상태 · 예외 수 · 담당 AE)
/hospitals/new                계약 등록 (한 화면, 인수 수락 자동)
/hospitals/{id}               ─ 헤더: 병원명 · 3상태 · 일시정지/재개 · 공개 주소
  /hospitals/{id}             현황     ← 예외 카드 + 3상태 상세 + 이번 달 요약 (dashboard 대체)
  /hospitals/{id}/info        병원 정보 ← profile + 브랜드 + 도메인 + 공식 채널·근거 자료 (onboarding·profile·wiki 통합)
  /hospitals/{id}/content     콘텐츠   ← 월 표 + 발행 요일 + 표본 확인 (content·schedule 통합)
  /hospitals/{id}/reports     보고서   ← 그대로, 다이얼로그 1개로 축약
/operations                   예외 인박스 (전역)
/leads                        상담 요청 (변경 최소: 온보딩 시작 → /hospitals/new?leadId= 직행)
/accounts                     그대로
```

삭제: `/hospitals/{id}/{dashboard,onboarding,profile,schedule,wiki,essence,query-targets,exposure-actions}` (8개 라우트, 합계 ≈11,000줄). 옛 URL은 새 탭으로 redirect.

### 4.2 세 가지 명시 상태 (헤더·목록·현황이 같은 함수를 씀)

| 상태 | 계산 | 값 |
|---|---|---|
| 공개 서비스 | `publicServiceState(hospital)` (PR-0A) | 공개 중 / 일시 정지 / 준비 중(남은 조건 나열) |
| 콘텐츠 준비 | `schedule_set && essence_readiness.current != null` | 자동 발행 중 / 준비 중(남은 조건: 일정 · 자료 처리 N건 · 운영 기준 검수) / 예외 있음 |
| 자기 도메인 | `domain_last_check_ok` + cert 상태 (있을 때만) | 연결됨 / 확인 중(마지막 확인 시각) / 문제(사유) / 사용 안 함 |

현재의 5개 점(`hospital-header-progress.ts:41-45`)·STATUS_LABELS·온보딩 10단계·readiness checks는 이 3상태로 대체된다. `v0_report_done`은 상태가 아니라 보고서 탭의 항목이다.

### 4.3 화면별 명세

**`/hospitals/new`** — 필드: 병원명(리드에서 자동), 계약 번호(자동 생성 제안), 효력일, 요금제, 담당 AE(로그인 사용자 기본), 영업 담당(리드에서). 제출 1회 = 생성+계약+인수 수락. 중복 병원 409는 **기존 병원에 연결** 선택지로 표시(재시도 안내 금지). 성공 시 `/hospitals/{id}/info`로 이동하며 자동 입력이 배경에서 시작된다.

**`/hospitals/{id}` 현황**
- 상단: 3상태 카드. 각 카드에 "남은 조건"을 사람이 할 일/시스템이 하는 중으로 구분해 표시. 사람이 할 일에만 링크.
- 예외 카드(이 병원의 open 인시던트 + ESCALATED 초안): 사유, 근거, **서버가 준 허용 행동**만 버튼으로. ESCALATED 초안 카드는 finding 목록 → 본문 편집 → 재검수 요청 / 예외 승인(사유 필수).
- 이번 달 요약: 발행 n/총, 언급률(측정 결과 있을 때만, 없으면 "측정 결과 없음"), 다음 보고서 예정일.
- **없음**: 재실행 버튼, 측정 로그, 운영 흐름 점, 노출 우선순위, 질문별 언급률(→ 콘텐츠·보고서 탭의 읽기 전용 섹션).

**`/hospitals/{id}/info`**
- 섹션: 병원·원장·진료 항목·연락처·공식 채널(URL 목록) / 공개 페이지(대표색·첫 화면 문구: 자동값 표시 + override) / 로고·사진(권리 정보 필수) / 자기 도메인(입력·제거; 상태는 자동 폴링).
- 상단에 **"남은 필수 항목 N개"**(서버 `missing_profile_requirement_keys`) — 이것이 온보딩 체크리스트의 전부. `profile_complete`는 저장 시 서버가 파생. 체크박스 없음.
- 공식 채널을 저장하면 근거 자료로 **자동 등록·처리**. "자료로 추가" 버튼 없음. 자료 표는 처리 상태 읽기 전용 + 파일 업로드 + 제외 토글만.
- 근거 노트는 자료 행을 펼치면 보이고 노이즈 제외 토글만 있다.

**`/hospitals/{id}/content`**
- 발행 요일(요금제 기본값 미리 선택, 시작일 자동=다음 달 1일 또는 즉시), 요금제는 계약 값 읽기 전용(정정은 `/hospitals/new` 계약 정정 링크 → `correct-contract`, OWNER).
- 월 표: 각 글의 상태를 `공개 중 / 공개 보류(사유) / 예정 / 초안 생성 중 / 차단(사유·인시던트 링크)`으로 — 사이트와 같은 판정.
- 액션: 표본 글에만 "확인 완료". 글 상세는 읽기 + 제목/본문 편집(편집 후 자동 재검수·재인증 안내). 지금 발행·반려·재생성·이미지 재생성·일정 변경·취소는 **없음**(인시던트 행동으로만).
- 하단 읽기 전용: 이번 달 환자 질문과 측정 대상 여부(`in_tracking_set`), 노출 보완 제안(정보로만).

**`/hospitals/{id}/reports`** — 목록 + 한 다이얼로그: 원장용 PDF 열기 → 전달 기록(받은 분·방법·메모) → 이력. V0 보고서도 같은 다이얼로그(전달 기록 포함).

**`/operations`** — 사람 처리 필요 항목만(기존 필터 유지). 각 항목: 병원·사유·근거·**담당(지정/변경)**·서버 허용 행동. RUNNING/기한 내 RETRYING 제외 유지. 문구는 "무엇이 막혔고 무엇을 결정해야 하는가"만.

### 4.4 용어 사전

`admin/lib/admin-copy.ts`를 유일한 사전으로 하고, 검토 §4의 통일안을 적용한다. 서버 라벨(`serializers.py:283-291`, `operations-center.ts`, `hospitals.py:1298,1316`)도 같은 용어로. `scripts/test_copy_contracts.py`에 admin 페이지 소스가 금지 변형(`공개 표면`, `허브`, `리포트`, `스케줄`, `slug`)을 포함하지 않는지 검사 추가.

### 4.5 백엔드 변경 (Phase 1에 필요한 최소)
- `GET /admin/hospitals/{id}/overview`: 3상태 + 남은 조건 + open 예외 + 이번 달 요약을 한 번에(현재 대시보드의 10회 fetch 대체).
- `PATCH /admin/hospitals/{id}` 응답에 `missing_profile_requirement_keys`, `profile_complete`(파생) 포함 — 이미 계산됨.
- 공식 채널 저장 → 자료 자동 등록 hook(기존 `essence.py:625` 경로 재사용).
- 옛 라우트 삭제로 호출자가 사라지는 엔드포인트는 **삭제하지 않고** 유지(CLI·복구용) — 단, 위험 행동(수동 초안, 요금제 변경)은 PR-0에서 제거된 상태.

## 5. 테스트·검증 기준

- Phase 0 각 PR: 결함별 회귀 테스트 선행(실 PostgreSQL), 전체 `make test-backend-local` + `make test-frontend` + `make copy-guard` 초록.
- Phase 1: 삭제된 페이지의 테스트 제거, 새 화면은 (a) 3상태 함수 단위 테스트 (b) 각 화면의 "정상 경로에 나타나는 버튼 목록" 스냅샷 테스트 — 목록이 §2를 넘으면 실패 (c) 클릭 수 시나리오 테스트(리드→운영 시작 ≤12 상호작용, 월 운영 ≤5 클릭).
- 릴리스 전: 운영 DB read-only로 PAUSED·ESCALATED·부분 초안·PLAN_8 실제 개수 확인, 마이그레이션 dry-run.

## 6. 실행 순서·역할

| 순서 | 작업 | 담당 |
|---|---|---|
| 1 | 이 설계 승인 | 사용자 |
| 2 | 구현 계획(writing-plans) 작성 | Fable |
| 3 | PR-0A → 0B → 0C → 0D (병렬 가능: 0A‖0B, 0C‖0D) | Opus 5 구현 / Fable 검수 / Codex sol high 교차 검토 |
| 4 | PR-1A 공통(3상태 함수·사전·overview API) → PR-1B info → PR-1C content → PR-1D 현황·operations → PR-1E 라우트 삭제·redirect | 동일 |
| 5 | 문서 갱신: CLAUDE.md §변경 시 보존할 계약, system-map §4·§10, runbook, language guide | Fable |

각 PR은 Fable이 결함 등록부 ID 기준으로 검수·승인·반려한다. 반려 사유는 PR에 남긴다.

## 7. 리스크

- Phase 0 없이 Phase 1을 하면 예비 버튼 제거가 운영 사고를 만든다 → 순서 고정.
- 옛 라우트 삭제로 북마크·Slack 링크가 깨짐 → redirect 유지 1개월.
- 인시던트 자동 배정이 담당 AE 없는 병원에서 실패 → OWNER 폴백.
- SEC-01 단언 도입 시 배치/CLI 호출 경로 누락 → `X-Admin-Actor-System` 허용목록을 먼저 조사(현재 `default_actor` 폴백 경로 전수).
