# Re:putation Content Essence Model Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build a marketer-operated Content Essence system where MotionLabs members can input Naver Blog, YouTube, homepage, interviews, and manual notes, then synthesize an accurate hospital-specific content philosophy used by automated content and reporting workflows.

**Architecture:** Add source ingestion records, extracted source notes, and a versioned Hospital Content Philosophy model. The first MVP accepts manual text/URL metadata and optional extracted text; automatic crawlers can be added later behind the same Source model. Content generation should depend on the approved philosophy, not raw source dumps.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, PostgreSQL, Next.js Admin, existing AI service layer.

---

## Product Definition

### Operating Model

Re:putation is not self-serve SaaS first. It is an **initial-onboarding-heavy, automated-operations service**:

1. MotionLabs marketer/operator collects source materials.
2. Operator uploads/pastes source material into Admin.
3. System extracts structured signals.
4. System drafts a hospital-specific content philosophy.
5. Operator reviews/edits/approves the philosophy.
6. Monthly autopilot uses the approved philosophy to generate content and reports.
7. MotionLabs screens generated report and communicates to the doctor.

### Source Types

MVP source types:
- `NAVER_BLOG`
- `YOUTUBE`
- `HOMEPAGE`
- `INTERVIEW`
- `LANDING_PAGE`
- `BROCHURE`
- `INTERNAL_NOTE`
- `OTHER`

### Content Philosophy Output

The approved philosophy must answer:
- 이 병원은 어떤 진료 철학을 갖는가?
- 환자에게 어떤 약속을 하면 안 되는가?
- 어떤 표현과 문체가 이 병원답나?
- 어떤 진료/시술을 어떤 관점에서 설명해야 하나?
- 네이버 블로그/홈페이지/인터뷰에서 반복되는 핵심 메시지는 무엇인가?
- 콘텐츠에서 반드시 살려야 할 병원 고유의 강점은 무엇인가?
- 의료광고 리스크를 피하기 위한 병원별 금지 표현은 무엇인가?
- 자동 생성 콘텐츠가 “이 병원답다/아니다”를 판단하는 기준은 무엇인가?

---

## Data Model

### New Table: `hospital_source_assets`

Fields:
- `id: uuid`
- `hospital_id: uuid`
- `source_type: enum`
- `title: str`
- `url: str | null`
- `raw_text: text | null`
- `extracted_text: text | null`
- `summary: text | null`
- `key_messages: jsonb` — string[]
- `tone_signals: jsonb` — string[]
- `treatment_signals: jsonb` — object[]
- `risk_signals: jsonb` — string[]
- `status: enum` — `PENDING`, `PROCESSED`, `EXCLUDED`, `ERROR`
- `operator_note: text | null`
- `created_by: str | null`
- `created_at`, `updated_at`

### New Table: `hospital_content_philosophies`

Versioned philosophy record.

Fields:
- `id: uuid`
- `hospital_id: uuid`
- `version: int`
- `status: enum` — `DRAFT`, `APPROVED`, `ARCHIVED`
- `positioning_statement: text`
- `doctor_voice: text`
- `patient_promise: text`
- `content_principles: jsonb` — string[]
- `tone_guidelines: jsonb` — string[]
- `must_use_messages: jsonb` — string[]
- `avoid_messages: jsonb` — string[]
- `treatment_narratives: jsonb` — object[] with `{treatment, angle, explanation_style, cautions}`
- `local_context: jsonb` — `{region_terms, local_patient_context, avoid_region_stuffing}`
- `medical_ad_risk_rules: jsonb` — string[]
- `source_asset_ids: jsonb` — uuid[]
- `synthesis_notes: text`
- `reviewed_by: str | null`
- `approved_at: datetime | null`
- `created_at`, `updated_at`

Rule: only one `APPROVED` philosophy per hospital.

---

## API Design

### Source Assets

- `GET /api/v1/admin/hospitals/{hospital_id}/essence/sources`
- `POST /api/v1/admin/hospitals/{hospital_id}/essence/sources`
- `PATCH /api/v1/admin/hospitals/{hospital_id}/essence/sources/{source_id}`
- `POST /api/v1/admin/hospitals/{hospital_id}/essence/sources/{source_id}/process`
- `DELETE /api/v1/admin/hospitals/{hospital_id}/essence/sources/{source_id}` or mark `EXCLUDED`

### Philosophy

- `GET /api/v1/admin/hospitals/{hospital_id}/essence/philosophy`
- `POST /api/v1/admin/hospitals/{hospital_id}/essence/philosophy/draft`
- `PATCH /api/v1/admin/hospitals/{hospital_id}/essence/philosophy/{philosophy_id}`
- `POST /api/v1/admin/hospitals/{hospital_id}/essence/philosophy/{philosophy_id}/approve`

---

## Admin UX

Add new tab under hospital detail:

`병원 상세 > Essence`

Sections:
1. **자료 입력**
   - source type
   - title
   - URL
   - raw text paste area
   - operator note
2. **자료별 추출 결과**
   - summary
   - key messages
   - tone signals
   - treatment signals
   - risk signals
3. **콘텐츠 철학 초안 생성**
   - selected sources
   - generate draft
4. **콘텐츠 철학 편집/승인**
   - positioning statement
   - doctor voice
   - patient promise
   - content principles
   - must-use / avoid messages
   - treatment narratives
   - medical ad risk rules
5. **승인 상태**
   - DRAFT / APPROVED
   - approved by / approved at

---

## AI Synthesis Contract

### Source Processing Prompt

Input: one source asset.

Output JSON:
```json
{
  "summary": "...",
  "key_messages": ["..."],
  "tone_signals": ["..."],
  "treatment_signals": [
    {
      "treatment": "...",
      "angle": "...",
      "patient_language": ["..."],
      "cautions": ["..."]
    }
  ],
  "risk_signals": ["..."]
}
```

### Philosophy Synthesis Prompt

Input:
- hospital profile
- selected processed sources
- operator notes

Output JSON matching `HospitalContentPhilosophy` fields.

Strict rules:
- Do not invent credentials or medical claims not present in sources.
- Distinguish source-backed messages from inferred editorial guidance.
- Avoid “최고/유일/완치/100%” style claims.
- Capture the hospital’s real tone from sources, not generic healthcare copy.
- If sources conflict, include `synthesis_notes` explaining the conflict.

---

## Content Generation Integration

Modify content generation so each content prompt receives:
- approved content philosophy
- source-backed key messages
- treatment narrative relevant to the content topic
- avoid messages
- medical ad risk rules

Generation rule:
- If no approved philosophy exists, content can be generated only in `DRAFT_NEEDS_ESSENCE_REVIEW` or equivalent internal state, not auto-published.

---

## Report Integration

Monthly report screening summary should include:
- whether approved philosophy exists
- which sources were used
- whether this month’s content followed core principles
- any “off-brand” or medical-risk findings
- recommended source refresh if sources are stale

---

## Implementation Tasks

### Task 1: Add backend enums and SQLAlchemy models

**Files:**
- Modify: `backend/app/models/hospital.py` or create `backend/app/models/essence.py`
- Modify: `backend/app/models/__init__.py`

**Acceptance Criteria:**
- Source asset and philosophy models exist.
- Models use JSON fields for flexible source signals.
- Relationship to `Hospital` exists.

### Task 2: Add Alembic migration

**Files:**
- Create: `backend/alembic/versions/0006_add_content_essence.py`

**Acceptance Criteria:**
- Creates `hospital_source_assets`.
- Creates `hospital_content_philosophies`.
- Adds indexes on `hospital_id`, `status`, `source_type`.
- Enforces only one approved philosophy per hospital if practical via partial unique index, otherwise enforce in service.

### Task 3: Add Pydantic schemas

**Files:**
- Create: `backend/app/schemas/essence.py`

**Acceptance Criteria:**
- Request/response schemas for source create/update/process.
- Request/response schemas for philosophy draft/update/approve.
- JSON fields have typed structures where possible.

### Task 4: Add essence service

**Files:**
- Create: `backend/app/services/essence_engine.py`

**Acceptance Criteria:**
- `process_source_asset(asset)` returns structured signals.
- `synthesize_philosophy(hospital, sources, operator_note)` returns philosophy draft.
- Prompt explicitly prevents invented claims.
- Safe fallback exists when AI keys are absent for local/demo mode.

### Task 5: Add Admin API router

**Files:**
- Create: `backend/app/api/admin/essence.py`
- Modify: `backend/app/api/admin/__init__.py` or `backend/app/main.py`

**Acceptance Criteria:**
- CRUD source endpoints work.
- Process source endpoint updates status and extracted fields.
- Draft philosophy endpoint creates `DRAFT` version.
- Approve endpoint archives previous approved philosophy and approves selected draft.

### Task 6: Add backend tests

**Files:**
- Create: `backend/tests/test_essence_api.py`
- Create: `backend/tests/test_essence_engine.py`

**Acceptance Criteria:**
- Source create/list works.
- Source processing stores structured fields.
- Philosophy approval leaves only one approved record.
- Synthesis does not include unsupported claims in fixture test.

### Task 7: Add Admin types and API client helpers

**Files:**
- Modify: `admin/types/index.ts`
- Modify: `admin/lib/api.ts` if helper additions are useful

**Acceptance Criteria:**
- Types mirror backend schemas.
- No direct secret exposure.

### Task 8: Add Essence Admin tab/page

**Files:**
- Create: `admin/app/hospitals/[id]/essence/page.tsx`
- Modify: `admin/app/hospitals/[id]/layout.tsx`

**Acceptance Criteria:**
- Operator can add source material.
- Operator can process a source.
- Operator can generate draft philosophy.
- Operator can edit/approve philosophy.
- UI clearly shows approved/draft state.

### Task 9: Wire approved philosophy into content generation

**Files:**
- Modify: `backend/app/services/content_engine.py`
- Modify related content generation tests if present.

**Acceptance Criteria:**
- Content prompt includes approved philosophy fields.
- No approved philosophy triggers review-required state or warning.
- Generated content prompt includes source-backed messages and avoid rules.

### Task 10: Wire philosophy into report screening

**Files:**
- Modify: `backend/app/services/report_engine.py`
- Modify: `backend/app/templates/report.html` or internal report summary model.

**Acceptance Criteria:**
- Report includes source/essence readiness summary.
- Internal screening can identify no-approved-philosophy risk.

### Task 11: Verification

Run:
```bash
cd backend && pytest tests/test_essence_api.py tests/test_essence_engine.py tests/test_public_site.py tests/test_medical_filter.py -q
cd admin && npm run build
cd site && npm run build
```

Expected:
- backend tests pass
- admin build passes
- site build passes

---

## MVP Scope Guardrails

Do now:
- Manual URL/text source input
- AI-assisted extraction
- Operator-approved philosophy
- Content generation uses approved philosophy

Do later:
- Fully automated Naver/YouTube crawling
- Vector DB/RAG search
- Client-facing approval portal
- Multi-tenant self-serve onboarding
