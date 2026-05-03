# Re:putation Essence Execution Plan

작성일: 2026-05-03

## 방향

Re:putation의 Essence 기능은 셀프서브 입력 폼이 아니라 MotionLabs 운영자가 초기 온보딩 때 병원 자료를 집중 수집하고, 그 자료에서 근거 기반의 `Content Essence / Hospital Content Philosophy`를 수립한 뒤, 이후 콘텐츠 생성과 월간 리포트 검수는 최대한 자동화하는 운영 레이어다.

가장 중요한 제약은 “AI가 병원 철학을 창작하지 않는다”이다. AI는 입력된 원문, URL metadata, 인터뷰 메모, 내부 메모에서 반복되는 메시지와 표현 방식을 추출하고 구조화할 뿐이다. 근거가 없는 자격, 치료 효과, 비교 우위, 의료적 약속은 생성하지 않으며, 필요한 내용이 부족하면 `정보 부족`으로 남긴다.

## 현재 코드 기준

- Backend는 FastAPI, SQLAlchemy async/sync, Alembic, Celery 구조다.
- `Hospital`은 프로파일과 외부 URL 필드를 갖고 있으나, 자료별 원문/근거/철학 버전은 없다.
- 콘텐츠 생성은 `backend/app/services/content_engine.py`에서 병원 프로파일만 프롬프트에 넣는다.
- 월간 리포트는 `backend/app/services/report_engine.py`와 `MonthlyReport.sov_summary/content_summary` 중심이다.
- Admin은 병원 상세 탭 구조가 있고, `profile/content/schedule/reports/dashboard`가 존재한다.
- Admin proxy는 `admin/app/api/admin/[...path]/route.ts`의 allow-list를 통과한 경로만 backend로 전달한다.

## 구현 순서

### 1. DB 모델 / 마이그레이션

신규 모델 파일은 `backend/app/models/essence.py`로 둔다. `Hospital` 모델 파일에 계속 추가하면 병원 프로파일 책임이 커지므로, Essence는 별도 모델로 분리하고 `backend/app/models/__init__.py`에서 import한다.

#### `hospital_source_assets`

운영자가 입력한 원천 자료 단위다. MVP에서는 URL을 크롤링하지 않고 metadata로만 저장하며, 본문은 운영자가 `raw_text`에 붙여넣는다.

필드:

- `id: uuid primary key`
- `hospital_id: uuid fk hospitals.id ondelete cascade`
- `source_type: enum`
  - `NAVER_BLOG`
  - `YOUTUBE`
  - `HOMEPAGE`
  - `INTERVIEW`
  - `LANDING_PAGE`
  - `BROCHURE`
  - `INTERNAL_NOTE`
  - `OTHER`
- `title: string(300)`
- `url: string(1000) nullable`
- `raw_text: text nullable`
- `operator_note: text nullable`
- `source_metadata: jsonb default {}`
  - 예: `{ "published_at": "...", "author": "...", "channel": "...", "manual_source_label": "원장 인터뷰 1차" }`
- `content_hash: string(64) nullable`
  - `raw_text + url + title` 기준. 동일 자료 중복 처리 방지와 stale 판정에 사용한다.
- `status: enum`
  - `PENDING`
  - `PROCESSED`
  - `EXCLUDED`
  - `ERROR`
- `process_error: text nullable`
- `processed_at: datetime nullable`
- `created_by: string(100) nullable`
- `updated_by: string(100) nullable`
- `created_at`, `updated_at`

인덱스:

- `(hospital_id, status)`
- `(hospital_id, source_type)`
- `(hospital_id, content_hash)`

MVP validation:

- `url` 또는 `raw_text` 중 하나는 필수다.
- `raw_text`가 비어 있으면 source processing은 실행하지 않고 `PENDING`으로 유지한다.
- 자동 크롤링은 하지 않는다. URL은 출처 확인용 metadata다.

#### `hospital_source_evidence_notes`

원천 자료에서 뽑은 원자적 근거다. 철학은 이 note들을 참조해야 한다.

필드:

- `id: uuid primary key`
- `hospital_id: uuid fk hospitals.id ondelete cascade`
- `source_asset_id: uuid fk hospital_source_assets.id ondelete cascade`
- `note_type: enum`
  - `KEY_MESSAGE`
  - `TONE_SIGNAL`
  - `TREATMENT_SIGNAL`
  - `RISK_SIGNAL`
  - `PATIENT_PROMISE`
  - `DOCTOR_PHILOSOPHY`
  - `LOCAL_CONTEXT`
  - `PROOF_POINT`
  - `CONFLICT`
- `claim: text`
  - AI가 요약한 짧은 근거 명제
- `source_excerpt: text`
  - 반드시 `raw_text`에 실제로 존재하는 짧은 발췌
- `excerpt_start: int nullable`
- `excerpt_end: int nullable`
- `confidence: float nullable`
- `note_metadata: jsonb default {}`
  - 예: `{ "treatment": "치질", "patient_language": ["통증", "일상 복귀"] }`
- `created_at`

인덱스:

- `(hospital_id, note_type)`
- `(source_asset_id)`

후처리 규칙:

- `source_excerpt`가 원문에 존재하지 않으면 note 저장 실패 또는 `ERROR` 처리한다.
- note는 창작된 병원 주장 저장소가 아니라 원문 근거 색인이다.

#### `hospital_content_philosophies`

병원별 승인 가능한 콘텐츠 철학 버전이다.

필드:

- `id: uuid primary key`
- `hospital_id: uuid fk hospitals.id ondelete cascade`
- `version: int`
- `status: enum`
  - `DRAFT`
  - `APPROVED`
  - `ARCHIVED`
- `positioning_statement: text nullable`
- `doctor_voice: text nullable`
- `patient_promise: text nullable`
- `content_principles: jsonb default []`
- `tone_guidelines: jsonb default []`
- `must_use_messages: jsonb default []`
- `avoid_messages: jsonb default []`
- `treatment_narratives: jsonb default []`
  - `{ "treatment": "...", "angle": "...", "explanation_style": "...", "cautions": [...], "evidence_note_ids": [...] }`
- `local_context: jsonb default {}`
  - `{ "region_terms": [], "local_patient_context": [], "avoid_region_stuffing": true, "evidence_note_ids": [] }`
- `medical_ad_risk_rules: jsonb default []`
- `evidence_map: jsonb default {}`
  - 필드별 근거 note ID 목록. 예: `{ "doctor_voice": ["..."], "patient_promise": ["..."] }`
- `source_asset_ids: jsonb default []`
- `unsupported_gaps: jsonb default []`
  - 근거가 부족해서 비워둔 항목
- `conflict_notes: jsonb default []`
  - 상충하는 자료 설명
- `synthesis_notes: text nullable`
- `source_snapshot_hash: string(64) nullable`
- `created_by: string(100) nullable`
- `reviewed_by: string(100) nullable`
- `approved_at: datetime nullable`
- `approval_note: text nullable`
- `created_at`, `updated_at`

제약:

- 병원별 `version`은 단조 증가한다.
- `APPROVED`는 병원당 하나만 허용한다.
- PostgreSQL partial unique index를 우선 사용한다.
  - `unique(hospital_id) where status = 'APPROVED'`
- `APPROVED` 레코드는 직접 수정하지 않는다. 수정은 approved version을 복제한 새 `DRAFT`에서 한다.

#### 선택적 ContentItem 확장

콘텐츠 생성 integration 시점에 추가한다.

- `content_items.content_philosophy_id: uuid nullable`
- `content_items.essence_status: string nullable`
  - `ALIGNED`
  - `NEEDS_ESSENCE_REVIEW`
  - `MISSING_APPROVED_PHILOSOPHY`
- `content_items.essence_check_summary: jsonb nullable`

기존 `ContentStatus` enum을 바로 늘리면 PostgreSQL enum migration 부담이 있으므로, MVP에서는 별도 `essence_status`를 권장한다.

#### 선택적 MonthlyReport 확장

리포트 integration 시점에 추가한다.

- `monthly_reports.essence_summary: jsonb nullable`

기존 `content_summary`에 넣을 수도 있지만, 리포트 검수 항목이 커질 가능성이 높으므로 별도 필드를 권장한다.

### 2. API

신규 router는 `backend/app/api/admin/essence.py`로 둔다. `backend/app/main.py`에 admin dependency를 적용해 include한다.

Admin proxy도 `admin/app/api/admin/[...path]/route.ts`의 `ALLOWED_PREFIXES`에 `essence`를 추가해야 한다.

#### Source endpoints

- `GET /api/v1/admin/hospitals/{hospital_id}/essence/sources`
  - query: `status`, `source_type`
  - source와 evidence note count를 반환한다.
- `POST /api/v1/admin/hospitals/{hospital_id}/essence/sources`
  - body: `source_type`, `title`, `url`, `raw_text`, `operator_note`, `source_metadata`, `created_by`
  - URL만 있는 자료도 저장 가능하나 process는 불가하다.
- `GET /api/v1/admin/hospitals/{hospital_id}/essence/sources/{source_id}`
  - source 상세와 evidence notes 반환
- `PATCH /api/v1/admin/hospitals/{hospital_id}/essence/sources/{source_id}`
  - `DRAFT/APPROVED` philosophy와 연결된 source라도 원문 수정은 허용하되, 기존 approved philosophy는 snapshot으로 유지한다.
  - 수정 후 `status=PENDING`, 기존 notes 삭제 또는 `superseded` 처리한다.
- `POST /api/v1/admin/hospitals/{hospital_id}/essence/sources/{source_id}/process`
  - raw_text 기반 evidence note 추출
  - raw_text 없으면 400
- `POST /api/v1/admin/hospitals/{hospital_id}/essence/sources/{source_id}/exclude`
  - hard delete 대신 `EXCLUDED`
- `DELETE /api/v1/admin/hospitals/{hospital_id}/essence/sources/{source_id}`
  - MVP에서는 미승인/미연결 source만 hard delete 허용

#### Philosophy endpoints

- `GET /api/v1/admin/hospitals/{hospital_id}/essence/philosophies`
  - version 목록
- `GET /api/v1/admin/hospitals/{hospital_id}/essence/philosophy/approved`
  - 현재 approved version. 없으면 404 대신 `{ "approved": null }` 권장
- `POST /api/v1/admin/hospitals/{hospital_id}/essence/philosophy/draft`
  - body: `source_asset_ids`, `operator_note`, `created_by`
  - 처리된 evidence notes만 synthesis 입력으로 사용한다.
- `PATCH /api/v1/admin/hospitals/{hospital_id}/essence/philosophy/{philosophy_id}`
  - `DRAFT`만 수정 가능
  - 수정된 각 필드가 `evidence_map`을 유지해야 한다.
- `POST /api/v1/admin/hospitals/{hospital_id}/essence/philosophy/{philosophy_id}/approve`
  - body: `reviewed_by`, `approval_note`, `confirm_evidence_reviewed: true`
  - 이전 `APPROVED`는 `ARCHIVED`
- `POST /api/v1/admin/hospitals/{hospital_id}/essence/philosophy/{philosophy_id}/archive`
  - `DRAFT` 또는 과거 version 정리용

#### Readiness integration

기존 `GET /admin/hospitals/{id}/readiness`에 Essence check를 추가한다.

- `essence_sources`: processed source 1개 이상
- `essence_philosophy`: approved philosophy 존재
- `essence_freshness`: approved version의 `source_snapshot_hash`가 현재 processed sources와 일치

### 3. AI synthesis service

신규 서비스는 `backend/app/services/essence_engine.py`로 둔다.

핵심 함수:

- `process_source_asset(asset: HospitalSourceAsset) -> list[EvidenceNotePayload]`
- `synthesize_philosophy(hospital: Hospital, sources: list[HospitalSourceAsset], notes: list[HospitalSourceEvidenceNote], operator_note: str | None) -> PhilosophyDraftPayload`
- `validate_philosophy_grounding(payload, notes) -> list[GroundingError]`
- `screen_content_against_philosophy(content_item, philosophy) -> EssenceScreeningResult`

MVP에서는 Anthropic API를 우선 사용하되, `ANTHROPIC_API_KEY`가 없으면 deterministic fallback을 제공한다. fallback은 창작하지 않고 간단한 원문 발췌/빈 필드만 반환해야 한다.

#### Source processing prompt contract

입력:

- source id
- source type
- title
- url metadata
- raw_text
- operator_note

규칙:

- raw_text와 operator_note 밖의 사실을 사용하지 않는다.
- 병원 자격, 수상, 장비, 치료 효과, 환자 결과를 추정하지 않는다.
- 발췌문은 반드시 입력 텍스트에 존재해야 한다.
- 불명확하면 빈 배열 또는 `unsupported_or_ambiguous`에 넣는다.
- 의료광고 리스크 표현은 `risk_signals`로 분리한다.

출력 JSON:

```json
{
  "evidence_notes": [
    {
      "note_type": "KEY_MESSAGE",
      "claim": "환자에게 충분히 설명하는 진료를 강조한다.",
      "source_excerpt": "치료 전 충분한 설명을 드리는 것을 중요하게 생각합니다",
      "confidence": 0.86,
      "note_metadata": {
        "treatment": null,
        "patient_language": ["충분한 설명"]
      }
    }
  ],
  "unsupported_or_ambiguous": [
    {
      "text": "최고 수준",
      "reason": "비교 우위 표현이며 근거/심의 리스크가 있음"
    }
  ]
}
```

후처리:

- `source_excerpt in raw_text/operator_note` 검증
- `check_forbidden()`으로 위험 표현 태깅
- note 저장 후 source `status=PROCESSED`
- 실패 시 source `status=ERROR`, `process_error` 저장

#### Philosophy synthesis prompt contract

입력:

- hospital profile
- selected sources
- evidence notes
- operator_note

규칙:

- 각 출력 필드는 evidence note ID를 가져야 한다.
- evidence note가 없는 주장은 만들지 않는다.
- “원장/병원답다”는 판단은 반복 근거와 톤 signal에서만 도출한다.
- 근거 부족 항목은 `unsupported_gaps`에 남긴다.
- 상충 자료는 `conflict_notes`에 남기고 임의로 결론내리지 않는다.
- `최고`, `유일`, `완치`, `100%`, 성공률 보장, 부작용 없음 등 의료광고 리스크 표현은 금지한다.
- “환자에게 약속하면 안 되는 것”을 명시한다.

출력 JSON:

```json
{
  "positioning_statement": {
    "text": "지역 환자가 부담 없이 상담하고 설명을 들을 수 있는 병원",
    "evidence_note_ids": ["..."]
  },
  "doctor_voice": {
    "text": "단정적 홍보보다 차분한 설명형 문체",
    "evidence_note_ids": ["..."]
  },
  "patient_promise": {
    "text": "치료 결과 보장이 아니라 진료 과정과 선택지를 충분히 설명하겠다는 약속",
    "evidence_note_ids": ["..."]
  },
  "content_principles": [
    { "text": "시술 효과보다 환자가 궁금해하는 과정과 주의사항을 먼저 설명한다.", "evidence_note_ids": ["..."] }
  ],
  "tone_guidelines": [],
  "must_use_messages": [],
  "avoid_messages": [],
  "treatment_narratives": [],
  "local_context": {
    "region_terms": [],
    "local_patient_context": [],
    "avoid_region_stuffing": true,
    "evidence_note_ids": []
  },
  "medical_ad_risk_rules": [],
  "unsupported_gaps": [],
  "conflict_notes": [],
  "synthesis_notes": "근거 기반 요약. 외부 지식 사용 없음."
}
```

저장 전 변환:

- `{ text, evidence_note_ids }` 구조에서 `text`는 각 philosophy 필드로 저장한다.
- 전체 field-to-evidence 매핑은 `evidence_map`에 저장한다.
- evidence 없는 non-empty field가 있으면 422 또는 service validation error.

### 4. Admin UI

신규 페이지:

- `admin/app/hospitals/[id]/essence/page.tsx`
- `admin/types/index.ts`에 Essence 타입 추가
- `admin/app/hospitals/[id]/layout.tsx` 탭에 `Essence` 또는 `콘텐츠 철학` 추가

Admin UX는 온보딩 작업대처럼 구성한다.

#### A. 자료 입력

필드:

- 자료 유형
- 제목
- URL
- 원문 붙여넣기
- 운영자 메모
- 작성자

MVP 문구:

- “URL은 자동 수집하지 않고 출처 metadata로 저장됩니다. 본문은 아래 원문 영역에 붙여넣어 주세요.”

액션:

- 저장
- 저장 후 추출
- 제외

#### B. 자료별 근거 추출 결과

source별로 아래를 보여준다.

- status
- key messages
- tone signals
- treatment signals
- risk signals
- source excerpt
- confidence

운영자는 note를 제외하거나 source 자체를 `EXCLUDED` 처리할 수 있다. MVP에서 note 직접 편집은 후순위로 둔다. 틀린 note는 source 원문/메모를 보정하고 재처리하는 흐름이 더 안전하다.

#### C. 철학 초안 생성

조건:

- `PROCESSED` source 1개 이상
- 권장: 서로 다른 source type 2개 이상
- raw_text 없는 URL-only source는 synthesis에 쓰지 않는다.

입력:

- 사용할 source 선택
- synthesis operator note

출력:

- positioning
- doctor voice
- patient promise
- content principles
- tone guidelines
- must-use messages
- avoid messages
- treatment narratives
- medical ad risk rules
- unsupported gaps
- conflict notes

각 항목 옆에는 evidence badge를 붙인다. 클릭 시 source title, URL, excerpt를 보여준다.

#### D. 편집 / 승인

정책:

- `DRAFT`만 편집 가능
- 운영자가 근거 없는 내용을 추가하고 싶으면 본문에 바로 쓰지 않고 `INTERNAL_NOTE` source를 먼저 추가한 뒤 재처리한다.
- evidence 없는 필드가 있으면 승인 버튼을 비활성화한다.
- 승인 modal에서 `reviewed_by`, `approval_note`, `confirm_evidence_reviewed`를 받는다.
- 승인 완료 후 version/status/approved_at 표시

#### E. 운영 상태

상단 summary:

- approved philosophy 존재 여부
- current version
- processed source count
- last source processed_at
- stale 여부
- 콘텐츠 생성에 사용 중인 version

### 5. Content generation integration

변경 지점:

- `backend/app/services/content_engine.py`
- `backend/app/workers/tasks.py`
- 필요 시 `backend/app/api/admin/content.py`

구현 방식:

1. 콘텐츠 생성 전 approved philosophy를 조회한다.
2. `generate_content()`에 `philosophy`와 `evidence context`를 전달한다.
3. prompt에는 병원 프로파일보다 approved philosophy를 우선한다.
4. 관련 진료 항목에 맞는 `treatment_narratives`, `must_use_messages`, `avoid_messages`, `medical_ad_risk_rules`만 추려 넣는다.
5. 출력 후 `screen_content_against_philosophy()`를 실행한다.
6. 결과를 `content_items.content_philosophy_id`, `essence_status`, `essence_check_summary`에 저장한다.

프롬프트 규칙:

- approved philosophy 밖의 병원 고유 주장 생성 금지
- 환자에게 결과 보장성 약속 금지
- evidence-backed message는 자연스럽게 반영하되 과장하지 않음
- no approved philosophy이면 병원 프로파일 기반 일반 초안은 만들 수 있으나 `essence_status=MISSING_APPROVED_PHILOSOPHY`로 저장하고 자동 운영 품질 통과로 보지 않는다.

발행 정책:

- `MISSING_APPROVED_PHILOSOPHY`: publish 차단 또는 강한 override 필요. MVP는 차단 권장.
- `NEEDS_ESSENCE_REVIEW`: Admin에서 경고 표시. 운영자가 수정 후 재검수 필요.
- `ALIGNED`: 일반 검수/발행 가능.

### 6. Report screening integration

변경 지점:

- `backend/app/services/report_engine.py`
- `backend/app/workers/tasks.py`
- `backend/app/models/report.py`
- `backend/app/schemas/report.py`
- `backend/app/templates/report.html`
- `admin/app/hospitals/[id]/reports/page.tsx`

월간 리포트 생성 시 아래를 `essence_summary`에 저장한다.

필드:

- `approved_philosophy_exists: bool`
- `philosophy_version: int | null`
- `approved_at: string | null`
- `source_count: int`
- `processed_source_count: int`
- `source_asset_ids: string[]`
- `source_stale: bool`
- `generated_content_count: int`
- `aligned_content_count: int`
- `needs_review_content_count: int`
- `missing_philosophy_content_count: int`
- `off_brand_findings: []`
- `medical_risk_findings: []`
- `recommended_actions: []`

리포트 문구 원칙:

- 원장에게 “AI가 병원 철학을 만들었다”고 표현하지 않는다.
- “온보딩 자료 기반 콘텐츠 기준” 또는 “승인된 콘텐츠 철학 기준”으로 표현한다.
- 근거 부족/자료 노후화는 개선 액션으로 제안한다.

Admin 리포트 상세에는 PDF 전에 내부 screening summary를 보여준다.

### 7. Verification

Backend tests:

```bash
cd backend && pytest \
  tests/test_essence_api.py \
  tests/test_essence_engine.py \
  tests/test_medical_filter.py \
  tests/test_public_site.py \
  -q
```

Migration:

```bash
cd backend && alembic upgrade head
```

Admin build:

```bash
cd admin && npm run build
```

Site build:

```bash
cd site && npm run build
```

Docker smoke:

```bash
make up
make migrate
make demo-seed
bash scripts/test_full.sh
```

추가해야 할 테스트:

- source 생성/list/detail/update/exclude
- raw_text 없는 URL-only source process 시 400
- process_source_asset이 원문에 없는 excerpt를 저장하지 않음
- synthesis 결과의 모든 non-empty philosophy field가 evidence note를 참조함
- approval 시 병원당 approved version 하나만 남음
- approved record PATCH 불가
- approved philosophy 없을 때 콘텐츠가 `MISSING_APPROVED_PHILOSOPHY`로 저장됨
- approved philosophy가 있으면 content prompt에 version과 avoid/risk rules가 포함됨
- report `essence_summary`가 approved 여부와 content alignment count를 포함함

## Phase 1 MVP

- 운영자 수동 source 입력
- URL metadata 저장
- raw text 붙여넣기
- source evidence extraction
- evidence-backed philosophy draft
- 운영자 편집/승인
- 병원당 approved philosophy 1개 유지
- 콘텐츠 생성 prompt에 approved philosophy 주입
- no approved philosophy 콘텐츠는 review-required 상태로 표시
- 월간 리포트에 Essence readiness/screening summary 포함

## Phase 2 Autopilot

- Naver Blog, YouTube, 홈페이지 crawler/loader 추가
- URL fetch 결과를 같은 `hospital_source_assets`에 저장
- source refresh/staleness 자동 알림
- source별 변경 diff와 philosophy 재초안 제안
- content alignment 자동 점수화 고도화
- 월간 리포트에서 off-brand content와 source refresh 추천 자동 생성
- 원장 승인 포털 또는 external review link
- vector search/RAG는 evidence note 규모가 커진 뒤 검토

## Non-goals

- MVP에서 자동 크롤링 구현
- 병원이 직접 셀프서브로 철학을 생성하는 고객용 온보딩
- 근거 없는 병원 강점/수상/전문성 생성
- 의료 효과, 완치, 성공률, 비교 우위 보장 표현 생성
- Google Business Profile, Naver Place, YouTube API 자동 수정
- 의료광고 심의 제출 자동화
- 리뷰 생성/리뷰 대행/허위 평판 생성
- 매일 사람이 붙는 대행 워크플로우 확장
