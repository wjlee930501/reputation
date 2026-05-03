# Re:putation Essence System Impact Review

작성일: 2026-05-03

## Purpose

이 문서는 기존 Re:putation 1.0 제품 기능을 `Content Essence / Hospital Content Philosophy` 중심 구조로 바꿀 때 손봐야 할 모듈, 화면, 데이터, 리포트, 운영 흐름을 점검한 영향도 리뷰다.

구현 문서가 아니다. 기존 코드 변경 없이 현재 코드/문서 기준으로 무엇이 충돌하는지와 어떤 contract를 새 기준으로 삼아야 하는지를 정리한다.

참조 문서:

- `docs/plans/2026-05-03-reputation-content-essence-model.md`
- `docs/plans/2026-05-03-reputation-essence-execution-plan.md`
- `docs/prd/REPUTATION-1.0-PRD.md`

## Executive Assessment

현재 제품은 병원 프로파일(`Hospital`)을 중심으로 콘텐츠 생성, public site, llms.txt, structured data, SoV query, report가 연결된다. Essence 모델/API/화면은 아직 없고, `director_philosophy` 같은 자유 입력 필드가 사실상 병원별 콘텐츠 철학 역할을 하고 있다.

Essence 구조가 제품의 중심 기준이 되면 가장 큰 문제는 "승인된 철학 없이도 자동 생성, 발행, public 노출, 월간 리포트가 가능하다"는 점이다. 이것은 단순 기능 누락이 아니라 운영 contract 누락이다.

우선순위 기준:

- `P0`: Essence 없이는 자동 운영 품질을 통과하면 안 되는 차단 계약. 출시 전 반드시 막아야 한다.
- `P1`: MVP 운영 품질과 내부 검수 효율에 직접 영향을 주는 보강.
- `P2`: 자동화/고도화/확장성 개선. MVP 이후로 미룰 수 있다.

## Product-wide Essence Contract

Essence가 중심 기준이 되면 모든 기능은 아래 contract를 지켜야 한다.

1. `Hospital` profile은 공개 엔티티 사실의 저장소다. 병원 고유 문체, 약속, 금지 표현, 치료 설명 관점은 approved `HospitalContentPhilosophy`가 canonical source다.
2. 병원 고유 주장과 톤은 source asset/evidence note에 근거해야 한다. AI는 병원 철학을 창작하지 않는다.
3. 병원당 active approved philosophy는 하나만 존재해야 한다. 이전 approved version은 immutable snapshot으로 남긴다.
4. 자동 생성 콘텐츠는 생성 시 사용한 philosophy version을 기록해야 한다.
5. approved philosophy가 없으면 콘텐츠는 만들 수 있더라도 자동 운영 품질 통과 상태가 아니다. publish는 차단하거나 최소한 강한 내부 override와 audit가 필요하다. MVP는 차단을 기본값으로 둔다.
6. 콘텐츠 저장, 발행, bulk publish, monthly report 생성 경로는 모두 medical risk와 essence alignment를 재확인해야 한다.
7. public site, llms.txt, sitemap, JSON-LD에는 public-safe entity facts와 approved/aligned content만 노출한다. 내부 evidence excerpt나 검수 메모를 그대로 공개하지 않는다.
8. 월간 리포트는 SoV/발행 수치뿐 아니라 approved philosophy 존재 여부, source stale 여부, content alignment, off-brand/risk findings를 내부 screening summary로 가져야 한다.
9. 운영자가 승인, 발행, override한 행위는 who/when/what/version이 남아야 한다.
10. 자동 크롤링, 외부 API 자동 수정, 완전 자동 발행은 MVP contract 밖이다.

## Impact Review

### 1. Hospital onboarding/profile

현재 상태:

- `backend/app/models/hospital.py`의 `Hospital`이 프로파일, 외부 URL, Google/Naver 자산, status flag를 모두 보유한다.
- `backend/app/api/admin/hospitals.py`에서 `profile_complete=True`가 되면 V0 report가 자동 트리거된다.
- Admin profile 화면은 원장 약력, `director_philosophy`, 진료 항목, 외부 URL, 도메인 연결을 한 화면에서 입력한다.
- Public API와 public site는 `director_philosophy`, `treatments`, `specialties`, `keywords`를 그대로 사용한다.
- readiness는 core profile, external profiles, V0, site/domain, schedule, published content, SoV만 본다.

Essence 구조와의 충돌/갭:

- `profile_complete`가 source 수집/철학 승인과 무관하게 V0/site/content 운영의 시작점이 된다.
- `director_philosophy`는 근거 없는 자유 텍스트인데 콘텐츠와 public site에서 병원 철학처럼 쓰인다.
- 병원 profile facts와 content philosophy의 책임이 분리되어 있지 않다.
- source freshness, approved philosophy, evidence review 상태가 onboarding 완료 기준에 없다.

수정 필요 방향:

- 프로파일은 병원 공개 facts와 로컬 엔티티 정합성 입력으로 한정한다.
- Essence onboarding 상태를 별도로 둔다: processed source count, approved philosophy 존재, source snapshot freshness.
- `profile_complete`는 V0 진단의 전제 조건으로만 유지하고, content autopilot/publish readiness에는 approved philosophy를 추가한다.
- `director_philosophy`는 legacy/profile note로 취급하고 approved philosophy와 혼동하지 않게 UI 문구와 API contract를 분리한다.

우선순위: `P0`

수정 파일 후보:

- `backend/app/models/hospital.py`
- `backend/app/schemas/hospital.py`
- `backend/app/api/admin/hospitals.py`
- `admin/app/hospitals/[id]/profile/page.tsx`
- `admin/app/hospitals/[id]/dashboard/page.tsx`
- `admin/types/index.ts`

검증 방법:

- approved philosophy가 없는 병원은 readiness에서 READY가 되지 않는지 확인한다.
- `profile_complete=True`만으로 content generation/publish 품질 통과가 되지 않는지 테스트한다.
- profile 저장 후 V0는 기존대로 가능하되 Essence readiness와 분리되는지 확인한다.

### 2. Source ingestion / asset management

현재 상태:

- 병원 source는 `website_url`, `blog_url`, `kakao_channel_url`, Google/Naver URL 같은 profile 필드로만 저장된다.
- raw text, source metadata, source processing status, evidence note, stale 판단 모델이 없다.
- Admin에는 source 자료 입력/추출/승인 화면이 없다.

Essence 구조와의 충돌/갭:

- approved philosophy를 만들 근거 저장소가 없다.
- URL-only asset과 실제 원문 근거를 구분할 수 없다.
- AI synthesis가 근거를 사용했는지 검증할 수 없다.
- source 수정 후 기존 approved philosophy가 stale인지 판단할 방법이 없다.

수정 필요 방향:

- `hospital_source_assets`, `hospital_source_evidence_notes`, `hospital_content_philosophies`를 별도 모델로 추가한다.
- MVP에서는 자동 크롤링 없이 URL metadata와 raw text paste를 받는다.
- source processing은 raw_text/operator_note에서 evidence note를 추출하고, excerpt가 원문에 실제 존재하는지 검증해야 한다.
- approved philosophy는 evidence note ID와 source snapshot hash를 포함해야 한다.

우선순위: `P0`

수정 파일 후보:

- `backend/app/models/essence.py`
- `backend/app/models/__init__.py`
- `backend/alembic/versions/0006_add_content_essence.py`
- `backend/app/schemas/essence.py`
- `backend/app/services/essence_engine.py`
- `backend/app/api/admin/essence.py`
- `backend/app/main.py`
- `admin/app/api/admin/[...path]/route.ts`
- `admin/app/hospitals/[id]/essence/page.tsx`

검증 방법:

- URL 또는 raw_text 중 하나가 없으면 source 생성이 실패하는지 확인한다.
- raw_text 없는 URL-only source는 저장 가능하지만 process는 400인지 확인한다.
- 원문에 없는 excerpt가 evidence note로 저장되지 않는지 테스트한다.
- approved philosophy가 evidence 없는 non-empty field를 승인하지 못하는지 테스트한다.

### 3. Content calendar

현재 상태:

- `backend/app/services/content_calendar.py`는 plan과 publish_days만으로 월간 슬롯을 만든다.
- `backend/app/api/admin/content.py`의 schedule 생성은 즉시 `ContentItem` 슬롯을 만들고 `hospital.schedule_set=True`로 바꾼다.
- `backend/app/workers/tasks.py`의 monthly slot generation도 active schedule 기준으로 다음 달 슬롯을 만든다.
- 슬롯은 날짜, content type, sequence만 갖고 topic/essence target은 없다.

Essence 구조와의 충돌/갭:

- approved philosophy 없이 schedule_set이 완료될 수 있다.
- 콘텐츠 슬롯이 어떤 treatment narrative, must-use message, local context를 목표로 하는지 기록하지 않는다.
- philosophy가 갱신되어도 이미 생성된 슬롯과 draft의 기준 version이 없다.

수정 필요 방향:

- 슬롯 생성 자체는 허용하되, generation worker에서 approved philosophy 없으면 `MISSING_APPROVED_PHILOSOPHY`로 남기고 자동 생성/발행 품질 통과를 막는다.
- `ContentItem`에 `content_philosophy_id`, `essence_status`, `essence_check_summary`를 추가한다.
- P1에서는 slot/topic planning이 approved philosophy의 treatment narratives와 content principles를 참고하도록 확장한다.

우선순위: `P1`
단, generation/publish 차단은 `P0`이다.

수정 파일 후보:

- `backend/app/models/content.py`
- `backend/app/services/content_calendar.py`
- `backend/app/api/admin/content.py`
- `backend/app/workers/tasks.py`
- `admin/app/hospitals/[id]/schedule/page.tsx`
- `admin/app/hospitals/[id]/content/page.tsx`

검증 방법:

- approved philosophy 없는 병원도 슬롯 생성은 가능하되 readiness는 실패하는지 확인한다.
- nightly generation이 missing philosophy 상태를 남기거나 생성을 스킵하는지 테스트한다.
- philosophy version 변경 후 새 draft/content가 새 version을 참조하는지 확인한다.

### 4. Content generation

현재 상태:

- `backend/app/services/content_engine.py`는 `Hospital` profile만 프롬프트에 넣는다.
- type prompt는 `keywords`, `region`, `director_name`, `director_philosophy`, `treatments`를 직접 사용한다.
- 생성 후 `check_forbidden()` 위반이 있으면 retry한다.
- `backend/app/workers/tasks.py`의 `nightly_content_generation`은 DRAFT/REJECTED + body empty 콘텐츠를 생성한다.

Essence 구조와의 충돌/갭:

- approved philosophy 없이 병원 고유 콘텐츠를 생성한다.
- source-backed key message, avoid messages, treatment narratives, hospital-specific risk rules가 prompt에 없다.
- 생성물이 approved philosophy와 정렬되었는지 screen하지 않고 저장한다.
- prompt가 profile의 자유 입력 철학을 병원 고유 철학으로 취급한다.

수정 필요 방향:

- generation 전 approved philosophy를 조회하고 prompt에는 profile facts보다 philosophy context를 우선한다.
- raw source dump를 prompt에 넣지 않고 approved philosophy와 필요한 evidence-backed snippets만 넣는다.
- 생성 후 `screen_content_against_philosophy()`를 실행해 `ALIGNED`, `NEEDS_ESSENCE_REVIEW`, `MISSING_APPROVED_PHILOSOPHY`를 저장한다.
- approved philosophy가 없으면 자동 publish 가능한 draft로 취급하지 않는다.

우선순위: `P0`

수정 파일 후보:

- `backend/app/services/content_engine.py`
- `backend/app/services/essence_engine.py`
- `backend/app/workers/tasks.py`
- `backend/app/models/content.py`
- `backend/app/schemas/content.py`
- `backend/tests/test_essence_engine.py`

검증 방법:

- approved philosophy 없는 콘텐츠 생성 시 `MISSING_APPROVED_PHILOSOPHY`가 기록되는지 테스트한다.
- approved philosophy가 있으면 prompt에 version, avoid/risk rules, relevant treatment narrative가 포함되는지 fixture로 검증한다.
- 생성 결과가 forbidden expression과 essence screen을 모두 통과해야 `ALIGNED`가 되는지 확인한다.

### 5. Medical ad / risk filtering

현재 상태:

- `backend/app/utils/medical_filter.py`에 정적 forbidden pattern이 있다.
- content generation과 content update path에서 금지 표현을 검사한다.
- Admin content 화면도 단순 문자열 포함 기반으로 일부 금지 표현을 미리 보여준다.
- publish endpoint는 저장된 본문을 다시 검사하지 않는다.

Essence 구조와의 충돌/갭:

- 병원별 risk rules와 avoid messages가 없다.
- 금지 표현 위반 결과가 content item에 구조화되어 저장되지 않는다.
- publish 직전 검사가 없어 과거 저장물, DB 수정, bulk publish 경로에서 위험 표현이 통과할 수 있다.
- "검출된 금지 표현 없음"과 "병원별 금지 약속 위반 없음"은 다른데 현재 구분하지 않는다.

수정 필요 방향:

- 정적 forbidden filter와 philosophy-specific risk rules를 모두 적용한다.
- update, publish, bulk publish, generation output에서 같은 backend validator를 사용한다.
- risk findings를 `essence_check_summary` 또는 별도 risk summary에 저장한다.
- publish는 `ALIGNED`와 no-risk 상태를 요구한다.

우선순위: `P0`

수정 파일 후보:

- `backend/app/utils/medical_filter.py`
- `backend/app/services/essence_engine.py`
- `backend/app/services/content_engine.py`
- `backend/app/api/admin/content.py`
- `admin/app/hospitals/[id]/content/page.tsx`
- `backend/tests/test_medical_filter.py`

검증 방법:

- forbidden expression이 update와 publish 모두에서 차단되는지 테스트한다.
- 병원별 avoid/risk rule 위반이 `NEEDS_ESSENCE_REVIEW` 또는 publish block으로 이어지는지 테스트한다.
- bulk publish가 개별 publish와 같은 validator를 거치는지 확인한다.

### 6. Content review/publish workflow

현재 상태:

- Admin content 화면은 list, detail modal, markdown edit, preview, publish/reject, bulk publish를 제공한다.
- backend publish는 `body` 존재와 not already published만 확인하고 바로 `PUBLISHED`로 바꾼다.
- reject는 title/body/image를 초기화하고 nightly regeneration 대상이 된다.
- `published_by`는 요청 body 문자열이며 사용자 session과 연결되어 있지 않다.

Essence 구조와의 충돌/갭:

- off-brand 또는 missing philosophy content도 버튼 한 번으로 public에 노출된다.
- bulk publish가 essence/risk 상태를 표시하거나 차단하지 않는다.
- 승인 근거, philosophy version, screen summary가 reviewer에게 보이지 않는다.
- publish/reject 이유와 실제 운영자 identity가 audit되지 않는다.

수정 필요 방향:

- content list/detail에 essence status, philosophy version, risk findings를 노출한다.
- publish endpoint에서 backend 재검증을 수행한다.
- `MISSING_APPROVED_PHILOSOPHY`와 `NEEDS_ESSENCE_REVIEW`는 MVP에서 publish 차단한다.
- reject reason, publish actor, screening summary를 저장한다.
- bulk publish는 eligible content만 선택 가능해야 한다.

우선순위: `P0`

수정 파일 후보:

- `backend/app/api/admin/content.py`
- `backend/app/models/content.py`
- `backend/app/schemas/content.py`
- `admin/app/hospitals/[id]/content/page.tsx`
- `admin/types/index.ts`

검증 방법:

- `MISSING_APPROVED_PHILOSOPHY` content publish가 400인지 테스트한다.
- `ALIGNED` content만 bulk selectable인지 UI smoke test로 확인한다.
- publish 직전 본문을 DB에서 위험 표현으로 바꿔도 backend가 차단하는지 테스트한다.

### 7. Public AEO site / llms.txt / sitemap / structured data

현재 상태:

- `backend/app/api/public/site.py`는 ACTIVE 병원과 PUBLISHED 콘텐츠만 public API로 제공한다.
- `site/app/[slug]/page.tsx`는 profile facts, director career/philosophy, treatment list로 SSR page와 MedicalClinic/LocalBusiness JSON-LD를 만든다.
- `site/app/[slug]/llms.txt/route.ts`는 병원 정보, 원장 소개, 진료 철학, 콘텐츠 목록을 노출한다.
- `site/app/sitemap.ts`는 ACTIVE 병원과 published content URL을 넣는다.
- `backend/app/services/site_builder.py`는 deprecated fallback이다.

Essence 구조와의 충돌/갭:

- public site와 llms.txt가 approved philosophy가 아니라 profile의 `director_philosophy`를 그대로 노출한다.
- structured data의 `availableService`와 public text가 source-backed인지 알 수 없다.
- sitemap은 published status만 보며 essence alignment 여부를 모른다.
- internal evidence excerpt와 public-safe message의 경계가 아직 없다.

수정 필요 방향:

- public API는 approved philosophy의 public-safe summary/version 상태를 선택적으로 제공하되 내부 evidence map은 공개하지 않는다.
- home/llms.txt는 `director_philosophy`를 무조건 병원 철학처럼 쓰지 말고 approved philosophy가 있을 때만 public-safe positioning/tone summary를 사용한다.
- content detail/list는 `PUBLISHED`이면서 essence-aligned content만 반환하는 정책을 검토한다. DB migration 전에는 publish gate로 보장한다.
- JSON-LD와 visible content가 같은 fact source를 보도록 contract를 명시한다.

우선순위: `P1`
단, unapproved content publish 차단은 `P0`이다.

수정 파일 후보:

- `backend/app/api/public/site.py`
- `site/lib/api.ts`
- `site/app/[slug]/page.tsx`
- `site/app/[slug]/contents/page.tsx`
- `site/app/[slug]/contents/[contentId]/page.tsx`
- `site/app/[slug]/llms.txt/route.ts`
- `site/app/llms.txt/route.ts`
- `site/app/sitemap.ts`
- `backend/app/services/site_builder.py`

검증 방법:

- approved philosophy 없는 ACTIVE 병원에서 llms.txt가 legacy philosophy를 병원 철학으로 노출하지 않는지 확인한다.
- sitemap에 only active hospital and published/aligned content만 들어가는지 확인한다.
- JSON-LD의 physician/service description과 visible text가 충돌하지 않는지 HTML assertion을 추가한다.

### 8. SoV / AI search measurement

현재 상태:

- `backend/app/services/sov_engine.py`의 query matrix는 region, specialties, keywords 조합으로 생성된다.
- OpenAI와 Gemini를 platform으로 저장하며, competitor mentions도 저장한다.
- query failure는 `run_single_query()`에서 empty raw response와 `is_mentioned=False` 형태로 반환된다.
- `backend/app/api/admin/sov.py` trend/queries는 platform split 없이 aggregate한다.
- PRD는 failure와 미언급을 분리해야 한다고 명시하지만 모델에는 status/error가 없다.

Essence 구조와의 충돌/갭:

- SoV query가 병원별 approved positioning, treatment narrative, patient language를 반영하지 않는다.
- AI response가 병원 철학과 다르게 설명되는지 측정하지 않는다.
- 실패가 미언급처럼 집계될 수 있어 monthly report의 개선 판단이 왜곡된다.
- query priority 조정이 mention 여부만 보고, 전략적으로 강화해야 할 essence theme를 보지 않는다.

수정 필요 방향:

- query matrix 생성 시 approved philosophy의 treatment narratives, local context, patient language를 query seed로 사용할 수 있게 한다.
- `SovRecord`에 measurement status/error/source/citation metadata를 추가해 실패를 분모에서 제외한다.
- Admin dashboard는 platform split과 failure rate를 표시한다.
- P2에서는 AI response의 message alignment를 essence 기준으로 screen해 report insight로 연결한다.

우선순위: `P1`

수정 파일 후보:

- `backend/app/models/sov.py`
- `backend/app/services/sov_engine.py`
- `backend/app/workers/tasks.py`
- `backend/app/api/admin/sov.py`
- `admin/app/hospitals/[id]/dashboard/page.tsx`
- `backend/tests/test_sov_engine.py`

검증 방법:

- API failure fixture가 `FAILED`로 저장되고 SoV denominator에서 제외되는지 테스트한다.
- platform별 trend가 분리되는지 API response contract를 검증한다.
- approved philosophy 기반 query seed가 생성되는지 deterministic test를 추가한다.

### 9. Monthly report generation

현재 상태:

- `backend/app/services/report_engine.py`는 hospital, report_type, sov_pct, published_count만 템플릿에 전달한다.
- `backend/app/workers/tasks.py`의 monthly report는 month SoV, prev SoV, published count만 저장한다.
- `backend/app/models/report.py`는 `sov_summary`, `content_summary`만 갖는다.
- `backend/app/templates/report.html`은 generic SoV/action message 중심이다.

Essence 구조와의 충돌/갭:

- monthly report가 approved philosophy 존재 여부, source count, freshness, aligned content count를 모른다.
- off-brand/risk findings가 원장 커뮤니케이션 전에 내부적으로 드러나지 않는다.
- report가 "온보딩 자료 기반 콘텐츠 기준"을 반영하지 않고 단순 발행 수와 SoV만 말한다.
- V0 report도 source/essence readiness와 분리되어 있어 sales/demo 메시지가 흔들릴 수 있다.

수정 필요 방향:

- `monthly_reports.essence_summary`를 추가한다.
- monthly report 생성 시 approved philosophy version, approved_at, processed source count, stale 여부, aligned/needs review/missing counts, recommended actions를 저장한다.
- PDF에는 고객에게 보여도 되는 client-safe summary만 넣고, Admin에는 내부 screening summary를 먼저 보여준다.
- source 부족 또는 stale이면 "자료 보강 필요"를 next action으로 제안한다.

우선순위: `P0`

수정 파일 후보:

- `backend/app/models/report.py`
- `backend/app/schemas/report.py`
- `backend/app/services/report_engine.py`
- `backend/app/workers/tasks.py`
- `backend/app/templates/report.html`
- `backend/app/api/admin/reports.py`
- `admin/app/hospitals/[id]/reports/page.tsx`

검증 방법:

- approved philosophy 없는 병원의 monthly report에 missing philosophy finding이 저장되는지 테스트한다.
- aligned/needs review/missing content count가 실제 content item 상태와 일치하는지 테스트한다.
- Admin report detail에서 PDF 다운로드 전 screening summary가 보이는지 smoke test한다.

### 10. Admin dashboard/readiness score

현재 상태:

- Dashboard는 readiness, SoV trend, query table을 병원 상세 첫 화면에서 표시한다.
- readiness endpoint는 profile/site/domain/schedule/content/sov/report 중심으로 0-100을 계산한다.
- Essence check는 없다.

Essence 구조와의 충돌/갭:

- approved philosophy가 없어도 사이트/스케줄/content/sov/report 조건만 맞으면 READY로 보일 수 있다.
- 운영자가 다음 작업으로 Essence source 입력이나 approval을 발견할 수 없다.
- "AI 검색 준비도"가 Content Essence managed service의 핵심 기준을 반영하지 않는다.

수정 필요 방향:

- readiness check에 `essence_sources`, `essence_philosophy`, `essence_freshness`, `content_alignment`를 추가한다.
- score weight를 조정해 approved philosophy 없는 병원은 READY가 되지 않게 한다.
- missing check의 next_action이 Essence tab으로 연결되게 한다.
- dashboard KPI에 current philosophy version과 stale badge를 추가한다.

우선순위: `P0`

수정 파일 후보:

- `backend/app/api/admin/hospitals.py`
- `admin/app/hospitals/[id]/dashboard/page.tsx`
- `admin/app/hospitals/[id]/layout.tsx`
- `admin/types/index.ts`

검증 방법:

- approved philosophy 없는 병원의 readiness status가 `NEEDS_WORK`인지 테스트한다.
- processed source는 있으나 stale이면 freshness check가 fail인지 테스트한다.
- dashboard에서 Essence missing next action이 표시되는지 확인한다.

### 11. Admin reports screening mode

현재 상태:

- Admin reports 화면은 report 목록, simple detail modal, `sov_summary`, `content_summary`, PDF download만 제공한다.
- report approval/screening status, internal notes, send-to-doctor state는 없다.
- `MonthlyReport.sent_at`은 존재하지만 UI/flow에서 적극적으로 쓰이지 않는다.

Essence 구조와의 충돌/갭:

- MotionLabs가 doctor에게 전달하기 전에 Essence alignment/risk/source freshness를 검수할 화면이 없다.
- 리포트 PDF와 내부 screening summary의 경계가 없다.
- report issue를 content/source/philosophy 작업으로 되돌리는 운영 흐름이 없다.

수정 필요 방향:

- reports detail에 internal screening summary를 PDF보다 먼저 보여준다.
- `READY_FOR_REVIEW`, `APPROVED_TO_SEND`, `SENT`, `NEEDS_FIX` 같은 내부 report workflow를 검토한다.
- source refresh, content revise, philosophy update로 연결되는 action link를 둔다.
- MVP에서는 고객 발송 자동화가 아니라 MotionLabs 내부 확인 상태까지만 만든다.

우선순위: `P1`

수정 파일 후보:

- `backend/app/models/report.py`
- `backend/app/schemas/report.py`
- `backend/app/api/admin/reports.py`
- `admin/app/hospitals/[id]/reports/page.tsx`
- `backend/app/services/notifier.py`

검증 방법:

- report detail API가 `essence_summary`와 screening status를 반환하는지 테스트한다.
- UI에서 source stale/off-brand finding이 PDF 다운로드 전 보이는지 확인한다.
- `sent_at` 또는 send status가 내부 승인 전 설정되지 않는지 테스트한다.

### 12. Auth/permissions/audit log

현재 상태:

- Backend admin API는 `X-Admin-Key`로 보호된다.
- Admin Next proxy가 backend admin key를 서버에서 붙인다.
- Admin login은 session cookie를 만들지만 단일 password/secret 구조이고 logout route는 검색되지 않는다.
- 실제 사용자 identity/RBAC/audit log 모델은 없다.
- `published_by`는 request body 문자열이다.

Essence 구조와의 충돌/갭:

- philosophy approval은 의료 콘텐츠 운영상 중요한 행위인데 who/when/evidence-reviewed가 보장되지 않는다.
- source 입력, source exclude, philosophy approve, publish override, report screening에 audit trail이 없다.
- `created_by`, `reviewed_by`, `published_by`를 body로 받으면 신뢰할 수 있는 operator identity가 아니다.
- full RBAC는 MVP 범위 밖이지만 최소 audit 없이 approved philosophy contract를 운영하기 어렵다.

수정 필요 방향:

- MVP 최소: server-side session에서 operator identity를 만들고 mutating API에 actor를 주입한다.
- `audit_events` 또는 각 모델의 approval/publish fields에 trusted actor와 timestamp를 남긴다.
- essence approve에는 `confirm_evidence_reviewed=true`, `reviewed_by`, `approval_note`, previous version archival이 필요하다.
- logout/session expiry/password separation은 PRD 보안 요구와 함께 P1로 정리하되, approval audit은 P0로 둔다.

우선순위: `P0` for Essence approval/publish audit, `P1` for broader auth hardening

수정 파일 후보:

- `backend/app/core/security.py`
- `backend/app/api/admin/essence.py`
- `backend/app/api/admin/content.py`
- `admin/app/api/auth/login/route.ts`
- `admin/middleware.ts`
- `admin/lib/session.ts`
- `backend/app/models/essence.py`
- `backend/app/models/content.py`

검증 방법:

- philosophy approve response와 DB record에 trusted actor/approved_at이 남는지 테스트한다.
- publish actor가 클라이언트 body spoofing이 아니라 session/backend context에서 결정되는지 확인한다.
- mutating API audit event가 생성되는지 fixture test를 추가한다.

### 13. Demo seed / sales demo flow

현재 상태:

- `backend/app/utils/demo_seed.py`는 ACTIVE demo hospital, published content, SoV records, V0 report를 deterministic하게 생성한다.
- demo content는 approved philosophy나 source/evidence 없이 바로 PUBLISHED다.
- scripts는 public/admin/site smoke를 돌리지만 Essence flow는 없다.

Essence 구조와의 충돌/갭:

- sales demo가 새 제품 narrative인 source-backed Content Essence를 보여주지 못한다.
- demo readiness가 Essence 없는 상태에서도 좋아 보일 수 있다.
- 생성된 PDF/report가 source/essence screening summary를 보여주지 않는다.

수정 필요 방향:

- demo seed에 source assets, evidence notes, approved philosophy, aligned content item, essence_summary가 포함되어야 한다.
- demo Admin flow는 Profile -> Essence -> Content -> Reports 순서로 보여야 한다.
- fake source는 "원장 인터뷰 메모", "기존 블로그 발췌"처럼 수동 입력 MVP를 설명하는 형태가 적합하다.

우선순위: `P1`

수정 파일 후보:

- `backend/app/utils/demo_seed.py`
- `Makefile`
- `scripts/test_e2e.sh`
- `scripts/test_full.sh`
- `docs/prd/REPUTATION-1.0-PRD.md`

검증 방법:

- `make demo-seed` 후 Admin Essence tab에 processed source와 approved philosophy가 보이는지 확인한다.
- demo hospital readiness가 Essence checks까지 통과하는지 확인한다.
- public site와 llms.txt가 demo approved/aligned content만 노출하는지 확인한다.

### 14. Tests / CI / operational scripts

현재 상태:

- backend tests는 `test_medical_filter.py`, `test_public_site.py` 정도로 좁다.
- `scripts/test_full.sh`는 직접 DB 수정과 오래된 endpoint 가정이 섞여 있고, 일부 API key 이름이 현재 코드와 맞지 않는다.
- Admin/site build와 browser E2E는 문서화되어 있지만 CI contract로 고정되어 있지 않다.

Essence 구조와의 충돌/갭:

- 가장 중요한 contract인 "근거 없는 philosophy 승인 금지", "missing philosophy publish 차단", "report essence summary 생성"을 검증하지 않는다.
- migration, proxy allow-list, admin type contract가 깨져도 빨리 잡기 어렵다.
- operational script가 Essence flow를 건너뛰면 운영자가 옛 방식으로 데모/테스트를 계속할 수 있다.

수정 필요 방향:

- backend: `test_essence_api.py`, `test_essence_engine.py`, `test_content_essence_gate.py`, `test_report_essence_summary.py` 추가.
- admin: build/typecheck에서 Essence types와 proxy path를 검증한다.
- site: public HTML/llms/sitemap assertion에 approved/aligned policy를 포함한다.
- scripts: demo seed와 E2E path를 Essence 중심 flow로 업데이트한다.

우선순위: `P0` for backend contract tests, `P1` for browser/ops script coverage

수정 파일 후보:

- `backend/tests/test_essence_api.py`
- `backend/tests/test_essence_engine.py`
- `backend/tests/test_medical_filter.py`
- `backend/tests/test_public_site.py`
- `scripts/test_e2e.sh`
- `scripts/test_full.sh`
- `Makefile`
- `admin/package.json`
- `site/package.json`

검증 방법:

- `cd backend && pytest tests/test_essence_api.py tests/test_essence_engine.py tests/test_medical_filter.py tests/test_public_site.py -q`
- `cd admin && npm run build`
- `cd site && npm run build`
- `make demo-seed` 후 browser smoke: Admin login -> 병원 상세 -> Essence -> Content -> Reports -> Public Site -> llms/sitemap.

## P0 Summary

P0로 먼저 막아야 할 contract:

- Source/evidence/philosophy data model과 approved philosophy 단일성.
- Admin Essence API와 proxy path.
- Readiness에 Essence source/approved/freshness check 추가.
- Content generation이 approved philosophy를 사용하고 사용 version/status를 기록.
- Publish/bulk publish가 missing philosophy, needs review, medical risk를 차단.
- Monthly report가 `essence_summary`를 저장.
- Essence approval/publish actor와 timestamp audit.
- Backend tests로 위 contract를 고정.

## P1 Summary

P1로 이어서 정리할 항목:

- Admin Essence tab의 운영 UX 완성도.
- Public site/llms.txt/JSON-LD의 public-safe approved philosophy 반영.
- Admin report screening mode.
- Demo seed와 browser E2E를 Essence 중심으로 업데이트.
- SoV query seed와 dashboard platform/failure split 개선.
- Session/logout/password separation 등 admin auth hardening.

## P2 Summary

P2로 미룰 수 있는 항목:

- Philosophy 기반 자동 topic planning 고도화.
- AI response의 essence alignment scoring.
- Source stale diff/re-draft recommendation.
- Rich audit event explorer/RBAC.
- Source refresh notifications.
- Advanced report workflow and analytics.

## Implementation Sequencing

### Week 1

목표: Essence contract를 backend 중심으로 먼저 닫아 자동 운영 위험을 막는다.

1. DB/model/API foundation
   - `hospital_source_assets`, `hospital_source_evidence_notes`, `hospital_content_philosophies` 추가.
   - approved philosophy partial unique constraint 또는 service-level guard 추가.
   - `backend/app/api/admin/essence.py`와 admin proxy allow-list 추가.

2. Essence engine MVP
   - source processing, excerpt validation, deterministic fallback.
   - philosophy synthesis validation: non-empty field는 evidence note를 요구.

3. Readiness and admin navigation
   - hospital readiness에 Essence checks 추가.
   - hospital detail tab에 Essence entry 추가.
   - 최소 Essence page는 source list/create/process/draft/approve까지 가능해야 한다.

4. Content safety gate
   - `ContentItem`에 philosophy version/status/check summary 추가.
   - generation prompt에 approved philosophy 주입.
   - no approved philosophy -> `MISSING_APPROVED_PHILOSOPHY`.
   - publish/bulk publish backend block.

5. Report summary foundation
   - `MonthlyReport.essence_summary` 추가.
   - monthly report worker가 approved/source/content alignment count를 저장.

6. Backend contract tests
   - source process, evidence grounding, approve uniqueness.
   - missing philosophy generation/publish block.
   - report essence summary.

### Week 2

목표: 운영자가 실제로 검수/데모/공개 노출을 안정적으로 다룰 수 있게 만든다.

1. Admin Essence UX polish
   - evidence badge, source excerpt drawer, stale badge, approve modal.

2. Admin content/reports UX
   - content list/detail에 essence status와 findings 노출.
   - report detail에 internal screening summary 추가.

3. Public AEO alignment
   - public API/site/llms.txt에서 approved public-safe philosophy만 선택적으로 사용.
   - sitemap/public content contract를 aligned publish gate와 맞춘다.

4. Demo seed and E2E
   - demo source/evidence/philosophy/aligned content/report summary 추가.
   - browser smoke flow를 Essence 중심으로 업데이트.

5. SoV measurement cleanup
   - platform split/failure handling을 PRD 기준에 맞게 보강.
   - approved philosophy에서 query seed를 일부 생성.

### Later

목표: 자동화와 scale-out을 안전하게 확장한다.

- Source stale diff, re-synthesis recommendation.
- AI response/message alignment analysis.
- Report workflow states and richer audit explorer.
- RBAC/multi-operator permissions.
- Custom domain canonical strategy 고도화.
- Source refresh notification and scheduled review.

## Do Not Build Yet

MVP에서는 아래를 아직 만들지 않는다.

- Naver Blog, YouTube, homepage 자동 크롤러.
- 고객 셀프서브 온보딩/승인 포털.
- Google Business Profile, Naver Place, YouTube, 홈페이지 CMS 외부 API 자동 수정.
- 완전 자동 발행 또는 사람 검수 없는 publish.
- 의료광고 심의 제출 자동화.
- 리뷰 생성, 리뷰 대행, 허위 평판 생성.
- vector DB/RAG 기반 대규모 source search.
- CRM/예약/전화 전환 자동 연동.
- 다중 tenant self-serve SaaS billing/seat management.
