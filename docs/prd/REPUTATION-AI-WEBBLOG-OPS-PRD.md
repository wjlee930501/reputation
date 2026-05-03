# Re:putation AI 노출 웹블로그 운영대행 PRD

작성일: 2026-05-03
제품명: Re:putation
운영사: MotionLabs Inc.
문서 목적: Re:putation의 다음 제품 반복을 "홈페이지 제작"이 아니라 "AI 노출을 위한 컨설팅 기반의 웹블로그 운영대행"으로 재정의하고, 현재 시스템 위에 필요한 제품/운영/기술 요구사항을 정리한다.

## 1. Executive Summary

Re:putation의 다음 반복은 병원용 홈페이지 빌더가 아니다. MotionLabs가 병원별 AI 노출 전략을 세우고, ChatGPT/Gemini 질의에서 병원이 추천·언급될 가능성을 높이기 위해 웹블로그와 공개 정보 자산을 운영하는 관리형 서비스다.

핵심 고객 약속은 "AI 상위 노출 보장"이 아니라 "환자가 ChatGPT, Gemini 같은 AI에서 병원을 찾을 때, 우리 병원이 추천·노출되도록 돕는 운영 체계"다. 이를 위해 병원 온보딩, source-backed Essence, AI Query Target, SoV 측정, gap diagnosis, query-linked content planning, 웹블로그 발행, 월간 리포트를 하나의 운영 루프로 묶는다.

이번 PRD의 제품 방향은 기존 AEO 사이트를 "홈페이지"로 판매하지 않고, AI가 읽고 인용하기 쉬운 병원 지식 기반 웹블로그로 재정의하는 것이다. Public web surface는 병원 소개 페이지가 아니라 AI 노출 운영의 결과물이자 콘텐츠 허브다.

## 2. Product Positioning

### One-Sentence Definition

Re:putation은 병원이 AI 검색/답변 환경에서 추천 후보로 등장할 수 있도록, 질의 전략 수립부터 근거 기반 콘텐츠 제작, 웹블로그 발행, SoV 측정, 월간 개선 리포트까지 MotionLabs가 운영하는 컨설팅 기반 웹블로그 운영대행 서비스다.

### What It Is / What It Is Not

| 구분 | 정의 |
| --- | --- |
| It is | 병원별 AI 노출 컨설팅, AI 질의 전략, 근거 기반 콘텐츠 철학 수립, 웹블로그 운영, 구조화 데이터/llms.txt/sitemap 관리, ChatGPT/Gemini SoV 측정, 월간 개선 액션 제안 |
| It is not | 범용 홈페이지 빌더, 병원 랜딩페이지 제작 대행, 단순 블로그 글 대량 생산, SEO 순위 보장 상품, ChatGPT/Gemini 상위 노출 보장 상품, 리뷰 조작/허위 평판 대행 |

### Customer-Facing Wording

권장 문구:

- "환자가 ChatGPT, Gemini 같은 AI에서 병원을 찾을 때, 우리 병원이 추천·노출되도록 돕습니다."
- "Re:putation은 병원의 AI 노출을 위해 질의 전략, 의료 콘텐츠, 웹블로그, 구조화 데이터, 월간 리포트를 함께 운영하는 서비스입니다."
- "홈페이지를 새로 만드는 것이 아니라, AI가 읽고 추천할 수 있는 병원 정보와 콘텐츠 자산을 꾸준히 운영합니다."

금지 문구:

- "AI 검색 1위 보장"
- "ChatGPT/Gemini 추천 보장"
- "홈페이지 자동 제작 서비스"
- "블로그 글 자동 발행으로 환자 유입 보장"
- "경쟁 병원보다 반드시 먼저 노출"

## 3. Customer Problem

병원 원장은 환자가 AI에 "우리 지역에서 어떤 병원이 좋아?"라고 물었을 때 자사 병원이 후보로 등장하길 원한다. 그러나 대부분의 병원은 아래 문제를 갖고 있다.

- ChatGPT/Gemini에서 우리 병원이 실제로 언급되는지 측정하지 못한다.
- 어떤 환자 질문을 타겟으로 해야 하는지 정의하지 못한다.
- 기존 홈페이지/블로그/지도 정보가 AI가 읽기 쉬운 구조로 정리되어 있지 않다.
- 병원 고유 철학과 강점이 의료광고 리스크 없이 콘텐츠로 축적되지 않는다.
- 콘텐츠 발행이 단순 블로그 업로드에 머물고, AI 노출 gap과 연결되지 않는다.
- 월간 운영 결과를 "무엇이 좋아졌고 다음 달 무엇을 해야 하는지"로 설명하지 못한다.

따라서 Re:putation은 병원에게 홈페이지를 납품하는 제품이 아니라, AI 노출을 위한 지속 운영 대행 체계를 제공해야 한다.

## 4. Core Value Proposition

고객 가치:

- 병원별 AI 질의 타겟을 정의하고 우선순위를 관리한다.
- ChatGPT/Gemini에서 현재 노출 현황과 경쟁 병원 언급을 정기 측정한다.
- 노출이 안 되는 이유를 content gap, entity gap, technical gap, source gap, reputation gap으로 진단한다.
- 병원 자료에서 확인된 근거만으로 Content Essence를 만들고 콘텐츠 기준으로 사용한다.
- 환자 질문과 연결된 웹블로그 콘텐츠를 매월 기획·생성·검수·발행한다.
- AI crawler와 검색엔진이 읽을 수 있도록 schema, sitemap, llms.txt, canonical, internal link를 운영한다.
- 월간 리포트에서 SoV 수치뿐 아니라 다음 달 운영 액션을 제시한다.

내부 운영 가치:

- AE/운영자가 병원별 다음 작업을 한 화면에서 판단한다.
- 콘텐츠 생산량이 아니라 query coverage와 exposure action 완료율로 운영한다.
- 근거 없는 의료 주장, 과장 표현, 병원답지 않은 콘텐츠 발행을 차단한다.

## 5. Product Principles

- Re:putation은 컨설팅 기반 웹블로그 운영대행이다. 홈페이지 제작 납품이 아니다.
- AI 노출은 보장하지 않는다. 측정, 진단, 개선 루프를 보장한다.
- 모든 병원 고유 주장과 콘텐츠 톤은 source asset과 evidence note에 근거해야 한다.
- 콘텐츠는 월 발행 수가 아니라 AI Query Target과 gap diagnosis에 연결되어야 한다.
- Public web surface는 예쁜 홈페이지가 아니라 AI가 읽는 병원 지식 기반 웹블로그다.
- SoV 실패, 미언급, 경쟁 병원 언급, 출처 링크, 플랫폼 차이는 분리 기록한다.
- 의료광고 리스크가 있거나 human approval이 없는 콘텐츠는 발행하지 않는다.
- 월간 리포트는 성과 자랑이 아니라 다음 달 운영 의사결정 문서다.

## 6. Current System Baseline

현재 유지할 자산:

- `Hospital` profile onboarding: 병원명, 주소, 전화, 진료시간, 지역, 진료과목, 키워드, 경쟁 병원, 원장 정보, 진료항목, 외부 URL, Google/Naver/도메인 필드를 유지한다.
- Source-backed Essence: `hospital_source_assets`, `hospital_source_evidence_notes`, `hospital_content_philosophies` 모델과 Admin Essence 탭을 유지한다.
- Approved content philosophy contract: 승인된 철학이 없으면 자동 콘텐츠 운영 품질을 통과하지 않는 구조를 유지한다.
- Content schedule/item: `PLAN_16/12/8`, 콘텐츠 타입, 발행일, DRAFT/PUBLISHED/REJECTED 상태, `content_philosophy_id`, `essence_status`, `essence_check_summary`를 유지한다.
- Claude content generation: 승인된 philosophy를 프롬프트에 넣는 방향을 유지하되, Query Target 기반 brief를 추가한다.
- Medical filter: 정적 금지 표현 검사와 Essence 기반 검수 차단을 유지한다.
- SoV baseline: `QueryMatrix`, `SovRecord`, ChatGPT/Gemini 플랫폼, raw response, mention rank, competitor mentions, priority 필드를 유지한다.
- Admin dashboard: readiness, SoV trend, query table, profile/content/schedule/reports/essence 탭 구조를 유지한다.
- Public AEO site: SSR 병원 페이지, 콘텐츠 목록/상세, JSON-LD, sitemap, robots, 병원별 `llms.txt`를 유지한다.
- Monthly report: `MonthlyReport`의 `sov_summary`, `content_summary`, `essence_summary`, PDF/GCS 다운로드 구조를 유지한다.

유지하되 재정의할 자산:

- "AEO 홈페이지"는 고객-facing으로 "AI 노출 웹블로그" 또는 "AI가 읽는 병원 웹블로그"로 재명명한다.
- Public main page는 홈페이지 첫 화면이 아니라 병원 엔티티 + 웹블로그 허브의 entry page로 취급한다.
- `director_philosophy` 자유 입력은 legacy profile note로 남기고, 콘텐츠 기준은 approved Content Philosophy로 둔다.
- 현재 ChatGPT 측정이 실제 ChatGPT Search가 아니라 일반 model response라면 "ChatGPT Search SoV"로 부르지 않고 "OpenAI response SoV" 또는 별도 라벨로 분리한다.

## 7. Target Operating Loop

1. Onboarding: MotionLabs 운영자가 병원 프로파일, 외부 공식 채널, Google Maps/Business Profile, 기존 홈페이지/블로그, 인터뷰/브로슈어/내부 메모를 수집한다.
2. AI query strategy: 병원별 AI Query Target을 정의한다. 지역, 질환, 증상, 진료 선택 기준, 경쟁 병원, 플랫폼, 우선순위를 함께 설정한다.
3. Baseline measurement: ChatGPT/Gemini 대상 질의 세트를 실행해 baseline SoV, 경쟁 병원 언급, 출처, 실패율을 저장한다.
4. Gap diagnosis: 미노출/낮은 노출 질의를 content gap, entity gap, source gap, technical gap, local reputation gap으로 분류한다.
5. Action planning: gap별로 이번 달 실행 액션을 만든다. 예: 웹블로그 콘텐츠, profile 보강, schema 보강, llms.txt 보강, source refresh, 재측정.
6. Webblog content production: Query Target과 approved Essence를 기반으로 콘텐츠 brief를 만들고 Claude 생성 초안을 만든다.
7. Human review/publish: 운영자가 의료광고, source grounding, 병원다움, query fit을 검수한 뒤 웹블로그에 발행한다.
8. Re-measure: 발행 후 정해진 측정 주기로 같은 Query Target을 재측정한다.
9. Report: 월간 리포트에서 SoV 변화, query gap, 발행 콘텐츠, 완료 액션, 남은 리스크, 다음 달 전략을 설명한다.
10. Next month strategy: 리포트 결과를 다음 달 AI Query Target priority와 콘텐츠 계획에 반영한다.

이 루프가 제품의 본체다. 웹블로그는 이 루프의 공개 실행면이지, 독립 홈페이지 상품이 아니다.

## 8. Functional Requirements

### 8.1 AI Query Target Model/Management

- 병원별 `AIQueryTarget`을 생성/수정/비활성화할 수 있어야 한다.
- Query Target은 `region_terms`, `patient_intent`, `symptom_or_condition`, `treatment`, `specialty`, `decision_criteria`, `platforms`, `priority`, `competitors`, `target_status`를 가진다.
- 하나의 Query Target은 여러 `query_variants`를 가진다. 예: "강남 치질 병원 추천", "치질 수술 어디가 좋아", "항문외과 잘 보는 곳".
- 기존 `QueryMatrix`는 실행용 query variant로 유지하고, 상위 전략 단위는 `AIQueryTarget`으로 분리한다.
- Query Target마다 연결 콘텐츠, 연결 source, 마지막 SoV, 경쟁 병원 언급, gap status, next action을 보여준다.
- 운영자는 월별로 target priority를 조정할 수 있어야 한다.

### 8.2 SoV Measurement Improvements

- 측정 batch/run 개념을 추가해 언제, 어떤 플랫폼, 어떤 query set, 몇 회 반복, 어떤 모델/검색 방식으로 측정했는지 저장한다.
- 플랫폼 라벨을 명확히 분리한다: `OPENAI_RESPONSE`, `CHATGPT_SEARCH`, `GEMINI_GROUNDING`, `GOOGLE_AI_OVERVIEW` 등 실제 측정 방식과 이름이 일치해야 한다.
- API failure, timeout, empty response, parser failure를 `not_mentioned`와 분리한다.
- SoV denominator는 성공 측정만 포함하고 실패율은 별도 KPI로 표시한다.
- raw response, mentioned entity, mention rank, sentiment/context, source/citation URL, competitor_mentions를 보존한다.
- Query Target별, 플랫폼별, 경쟁 병원별, 월별 SoV를 집계한다.
- AI response가 approved Essence와 다르게 병원을 설명하는 경우 message alignment finding으로 기록한다.

### 8.3 Gap Diagnosis / Exposure Action Engine

- Query Target별로 아래 gap type을 자동 진단한다.
  - `NO_MENTION`: 자사 미언급
  - `COMPETITOR_DOMINANT`: 경쟁 병원 반복 언급
  - `NO_PUBLIC_CONTENT`: 해당 질의에 대응하는 웹블로그 콘텐츠 없음
  - `WEAK_ENTITY_FACTS`: 주소/진료과/Google Maps/외부 채널 정보 부족
  - `TECHNICAL_CRAWL_GAP`: sitemap/llms/schema/canonical/robots 문제
  - `SOURCE_GAP`: 주장할 근거 source/evidence 부족
  - `CONTENT_STALE`: 관련 콘텐츠 오래됨
  - `MEDICAL_RISK_BLOCKED`: 콘텐츠가 검수 차단 상태
- gap은 `ExposureAction`으로 전환된다.
- Action은 owner, due_month, status, evidence, linked_query_target, linked_content, expected_impact를 가진다.
- Action engine은 "다음 달 추천 액션 TOP 3"를 report와 dashboard에 제공한다.

### 8.4 Query-Linked Content Planning

- 월간 콘텐츠 슬롯은 단순 타입 배분이 아니라 Query Target 또는 ExposureAction에 연결되어야 한다.
- 콘텐츠 brief는 target query, 환자 intent, approved philosophy version, relevant treatment narrative, must-use/avoid message, medical risk rules, internal link target을 포함한다.
- 콘텐츠 생성 전 brief를 운영자가 승인하거나 수정할 수 있어야 한다.
- 발행된 콘텐츠는 어떤 Query Target을 커버하는지 저장한다.
- 기존 FAQ/DISEASE/TREATMENT/COLUMN/HEALTH/LOCAL/NOTICE 타입은 유지하되, 핵심 운영 단위는 query coverage다.

### 8.5 Webblog IA Changes

- Public site IA를 "홈페이지"에서 "AI 노출 웹블로그"로 전환한다.
- 기본 IA:
  - 병원 엔티티 프로필: 주소, 전화, 진료시간, 진료과목, 지도, 원장 정보
  - AI 질문형 콘텐츠 허브: FAQ, 증상/질환, 치료/시술, 지역 질문, 원장 칼럼
  - Query cluster page: 특정 Query Target에 연결된 글 묶음
  - 최신 콘텐츠와 주요 진료 콘텐츠 간 internal links
- 메인 페이지는 마케팅 랜딩이 아니라 AI와 환자가 병원 facts와 콘텐츠 허브를 빠르게 파악하는 entry page로 설계한다.
- "병원 홈페이지" CTA는 필요 시 보조 링크로 두고, Re:putation 웹블로그 자체를 병원 공식 홈페이지처럼 과장하지 않는다.

### 8.6 llms.txt / Schema / Sitemap Enhancements

- 병원별 `llms.txt`는 병원 facts, 주요 진료항목, 웹블로그 콘텐츠 URL, query cluster URL, 최신 업데이트 일자를 포함한다.
- 내부 검수 메모, raw evidence excerpt, operator note는 public `llms.txt`에 노출하지 않는다.
- JSON-LD는 visible content와 일치해야 한다.
- 지원 schema:
  - `MedicalClinic`
  - `LocalBusiness`
  - `Physician`
  - `Article`
  - `FAQPage` 또는 `QAPage` where applicable
  - `BreadcrumbList`
  - `WebSite` / `CollectionPage`
- sitemap은 병원 main, content index, content detail, query cluster, llms.txt를 포함하고 `lastmod`를 실제 발행/수정일과 연결한다.
- robots는 AI crawler와 검색 crawler 접근 허용을 유지하되, private/admin 경로는 노출하지 않는다.

### 8.7 Report Redesign

- 리포트는 "월간 SoV 리포트"가 아니라 "AI 노출 웹블로그 운영 리포트"로 재설계한다.
- 필수 섹션:
  - 이번 달 요약: 현재 AI 노출 상태와 핵심 변화
  - 플랫폼별 SoV: ChatGPT/Gemini/기타 측정 방식별
  - Query Target별 결과: target, SoV, 경쟁 병원, gap, status
  - 이번 달 실행: 발행 콘텐츠, technical updates, source/Essence updates
  - Gap diagnosis: 왜 노출이 약한지
  - 다음 달 action plan: Query Target priority와 콘텐츠 계획
  - Compliance note: 의료광고 리스크 검수, human approval 상태
  - Caveat: AI 답변은 변동 가능하며 노출 보장이 아님
- 내부 Admin report detail에는 원장 전달용 PDF와 운영자용 screening summary를 분리해서 보여준다.

### 8.8 Admin UX Changes

- 병원 Dashboard를 운영 콘솔로 전환한다.
- 신규/개선 화면:
  - AI Query Targets: target CRUD, variant 관리, priority, platform, competitor 설정
  - Baseline/Monitoring: measurement run, failure rate, platform split
  - Exposure Actions: gap diagnosis 결과와 실행 상태
  - Content Plan: Query Target 기반 월간 brief와 슬롯
  - Webblog Coverage: target별 public page/content/schema/llms coverage
  - Report Builder/Review: 원장 전달 전 요약 문구와 action plan 검수
- 기존 Essence/Profile/Content/Schedule/Reports 탭은 유지하되, 각 탭의 상태가 Dashboard next action으로 연결되어야 한다.

## 9. Non-Functional Requirements

### Medical Ad Compliance

- 모든 생성, 수정, 발행, bulk publish, report wording 경로에서 의료광고 금지/주의 표현을 검사한다.
- "최고", "유일", "완치", "100%", "부작용 없음", 경쟁 비방, 치료 효과 보장성 표현은 기본 차단한다.
- 병원별 avoid message와 risk rule은 approved Content Philosophy에서 가져온다.
- 리포트도 상위 노출/환자 유입 보장처럼 오해될 표현을 사용하지 않는다.

### Source Grounding

- 병원 고유 주장, 원장 철학, 치료 설명 관점은 source asset/evidence note와 연결되어야 한다.
- 근거가 부족한 항목은 `unsupported_gaps`로 남기고 생성하지 않는다.
- 운영자가 근거 없는 주장을 추가하려면 먼저 `INTERNAL_NOTE` 또는 적절한 source를 추가하고 재처리한다.

### Human Approval Gates

- Content Philosophy approval이 없으면 자동 콘텐츠 생성/발행 품질 통과가 불가능해야 한다.
- Query Target monthly strategy, content brief, generated content, report는 운영자 승인 상태를 가져야 한다.
- 발행과 승인에는 who/when/version/audit note가 남아야 한다.

### Observability

- measurement run, content generation, essence screening, publish block, report generation, sitemap/llms fetch failure를 로그/메트릭으로 남긴다.
- SoV 측정 실패율, parser failure, platform latency, cost estimate를 병원/월 단위로 볼 수 있어야 한다.
- Public webblog의 sitemap/llms/schema 검증 결과를 readiness에 반영한다.

### Privacy/Security

- Admin secret, API key, raw source material, operator note, internal report finding은 public bundle/API에 노출하지 않는다.
- Public API는 PUBLISHED content와 public-safe hospital facts만 반환한다.
- raw source/evidence excerpt는 내부 Admin 전용이다.
- 리포트 다운로드는 signed URL 또는 Admin proxy를 통해 제한한다.

## 10. Data Model Additions

기존 모델을 유지하고 아래 모델/필드를 추가한다.

### AIQueryTarget

- `id`
- `hospital_id`
- `name`
- `target_intent`: 추천형, 증상형, 비교형, 비용/정보형, 치료방법형 등
- `region_terms`
- `specialty`
- `condition_or_symptom`
- `treatment`
- `decision_criteria`
- `patient_language`
- `platforms`
- `competitor_names`
- `priority`: HIGH/NORMAL/LOW
- `status`: ACTIVE/PAUSED/ARCHIVED
- `target_month`
- `created_by`, `updated_by`, `created_at`, `updated_at`

### AIQueryVariant

- `id`
- `query_target_id`
- `query_text`
- `platform`
- `language`
- `is_active`
- `query_matrix_id` nullable, 기존 `QueryMatrix`와 연결

### MeasurementRun

- `id`
- `hospital_id`
- `run_type`: BASELINE/WEEKLY/MONTHLY/MANUAL
- `platform`
- `measurement_method`
- `model_name`
- `repeat_count`
- `started_at`, `finished_at`
- `status`
- `failure_summary`
- `cost_estimate`

### SovRecord Enhancements

- `measurement_run_id`
- `query_target_id`
- `query_variant_id`
- `status`: SUCCESS/FAILED/TIMEOUT/PARSER_ERROR
- `source_urls`
- `mentioned_entities`
- `measurement_method`
- `parser_confidence`
- `response_alignment_summary`

### ExposureGap

- `id`
- `hospital_id`
- `query_target_id`
- `gap_type`
- `severity`
- `evidence`
- `diagnosed_at`
- `status`

### ExposureAction

- `id`
- `hospital_id`
- `query_target_id`
- `gap_id`
- `action_type`: CONTENT/WEBBLOG_IA/SCHEMA/LLMS/SITEMAP/PROFILE/SOURCE/MEASUREMENT/REPORT_NOTE
- `title`
- `description`
- `owner`
- `due_month`
- `status`: TODO/IN_PROGRESS/DONE/SKIPPED
- `linked_content_id`
- `linked_report_id`
- `completed_at`

### ContentBrief / Query-Linked Plan

- `id`
- `hospital_id`
- `content_item_id`
- `query_target_id`
- `exposure_action_id`
- `philosophy_id`
- `target_queries`
- `brief`
- `required_messages`
- `avoid_messages`
- `internal_links`
- `approval_status`
- `approved_by`, `approved_at`

### ReportInsight

- `id`
- `monthly_report_id`
- `query_target_id`
- `insight_type`
- `summary`
- `recommended_action_id`
- `customer_visible`

## 11. API / Service Additions

### Admin APIs

- `GET/POST /admin/hospitals/{id}/query-targets`
- `GET/PATCH/DELETE /admin/hospitals/{id}/query-targets/{target_id}`
- `POST /admin/hospitals/{id}/query-targets/{target_id}/variants`
- `POST /admin/hospitals/{id}/measurements/baseline`
- `GET /admin/hospitals/{id}/measurements/runs`
- `GET /admin/hospitals/{id}/query-targets/{target_id}/sov`
- `POST /admin/hospitals/{id}/diagnosis/run`
- `GET /admin/hospitals/{id}/exposure-actions`
- `PATCH /admin/hospitals/{id}/exposure-actions/{action_id}`
- `POST /admin/hospitals/{id}/content-plan/generate`
- `GET/PATCH /admin/hospitals/{id}/content-briefs/{brief_id}`
- `GET /admin/hospitals/{id}/webblog/coverage`
- `POST /admin/hospitals/{id}/reports/{report_id}/insights/generate`

### Public APIs

- `GET /public/hospitals/{slug}/query-clusters`
- `GET /public/hospitals/{slug}/query-clusters/{cluster_slug}`
- Existing public hospital/content APIs must remain public-safe.

### Services

- `QueryTargetService`: target/variant CRUD, monthly priority management.
- `MeasurementRunService`: batch execution, failure capture, cost/failure summary.
- `GapDiagnosisService`: SoV/content/entity/technical/source gap classification.
- `ExposureActionEngine`: gap-to-action conversion and next-month TOP actions.
- `ContentBriefService`: query-linked brief generation using approved Essence.
- `WebblogCoverageService`: sitemap/llms/schema/content coverage checks.
- `ReportInsightService`: customer-visible summary and operator-only findings.

## 12. Admin UX Requirements

- Dashboard 상단 문구는 "AI 노출 웹블로그 운영 현황"으로 둔다. "홈페이지 빌드" 중심 표현을 줄인다.
- 병원별 다음 작업을 우선순위로 보여준다: Query Target 설정, baseline 측정, gap diagnosis, Essence 승인, content brief 승인, 발행, re-measure, report review.
- Query Target 화면은 운영자가 고객 상담 후 타겟 질의를 구조화하는 곳이다.
- SoV 화면은 단순 query table이 아니라 target별 플랫폼 결과, 경쟁 병원, 실패율, source URL을 보여준다.
- Exposure Action 화면은 월간 운영 체크리스트다.
- Content 화면은 각 콘텐츠가 어떤 Query Target과 ExposureAction을 해결하는지 표시한다.
- Reports 화면은 원장 전달용 PDF와 내부 운영 메모를 분리한다.
- Readiness score는 homepage readiness가 아니라 AI exposure operations readiness로 명명한다.

## 13. Public Webblog Requirements

- Public webblog는 병원 공식 정보와 AI 질문형 콘텐츠를 공개하는 지식 기반이어야 한다.
- 첫 화면에서 병원명, 지역, 진료과목, 주소, 전화, 진료시간, 지도/외부 공식 채널, 주요 진료항목, 최신 콘텐츠가 SSR HTML에 포함되어야 한다.
- 콘텐츠 상세는 첫 문단에 환자 질문에 대한 짧은 답을 제공하고, 이후 근거 있는 설명을 제공한다.
- Query cluster page는 동일 AI Query Target에 대응하는 콘텐츠들을 묶어 AI crawler와 환자가 주제를 이해하기 쉽게 한다.
- 모든 페이지는 canonical, OpenGraph, JSON-LD, breadcrumb, internal link를 가진다.
- public 페이지는 approved/aligned/published 콘텐츠만 노출한다.
- 의료적 효능 보장, 경쟁 비방, 내부 source excerpt, 운영자 메모는 노출하지 않는다.
- 고객에게는 "웹블로그 운영"으로 설명하고, "새 홈페이지를 만들어 드립니다"를 주된 가치로 말하지 않는다.

## 14. Report Requirements

리포트 명칭:

- V0: "AI 노출 웹블로그 운영 진단 리포트"
- 월간: "AI 노출 웹블로그 운영 리포트"

V0 리포트:

- 병원 프로파일/엔티티 준비도
- baseline SoV
- Query Target별 현재 상태
- 경쟁 병원 언급 현황
- 웹블로그/콘텐츠/source gap
- 첫 달 운영 액션

월간 리포트:

- 이번 달 핵심 요약
- Query Target별 SoV 변화
- 플랫폼별 결과와 실패율
- 발행 콘텐츠와 연결 Query Target
- 완료된 ExposureAction
- 남은 gap과 리스크
- 다음 달 Query Target priority와 콘텐츠 계획
- 의료광고/근거 검수 상태
- "노출 보장 아님" caveat

리포트 원칙:

- 원장에게 전달되는 문구는 숫자보다 의사결정에 집중한다.
- 성과가 낮은 달에도 "무엇을 발견했고 다음 달 무엇을 할지"를 보여준다.
- 내부용 finding과 고객용 설명은 분리한다.

## 15. Success Metrics

제품/운영 지표:

- 신규 병원 onboarding 후 baseline report 생성까지 걸리는 시간
- 병원당 active AI Query Target 수
- Query Target 중 linked content가 있는 비율
- ExposureAction 월간 완료율
- approved Essence freshness 유지율
- generated content 중 `ALIGNED` 비율
- publish block된 medical risk 건수와 해결 시간
- monthly report 정시 생성/검수/전달률

AI 노출 leading indicators:

- Query Target별 SoV 변화
- 플랫폼별 mention rate 변화
- 경쟁 병원 대비 mention share 변화
- AI response source/citation에 public webblog URL이 포함된 비율
- technical coverage: sitemap/llms/schema/canonical 정상률

고객/사업 지표:

- 월간 리포트 고객 전달 후 유지율
- 첫 3개월 renewal/continue rate
- 원장 상담에서 이해되는 핵심 메시지 비율: "AI 노출 웹블로그 운영대행"으로 인식되는지

주의: 성공 지표는 노출 개선 가능성을 추적하기 위한 지표이며, AI 추천/순위 보장으로 판매하지 않는다.

## 16. MVP Scope

MVP에 포함:

- 고객-facing/관리자-facing 문구를 "AI 노출 웹블로그 운영대행"으로 정리.
- AI Query Target/Variant 모델과 Admin CRUD.
- 기존 QueryMatrix와 Query Target 연결.
- MeasurementRun 및 SoV 실패 상태 분리.
- Rule-based gap diagnosis v1.
- ExposureAction 생성/관리.
- Query Target 기반 monthly content brief 생성.
- 기존 Claude content generation에 brief context 추가.
- Public webblog IA 최소 변경: content hub, query cluster route, internal links, metadata naming.
- 병원별 `llms.txt`, sitemap, schema에 query cluster/content coverage 반영.
- Report v2 template: Query Target, gap, action, content, Essence summary 포함.
- Dashboard에 next actions와 webblog coverage 표시.

MVP에서 유지:

- 기존 Essence source input/processing/approval 구조.
- 기존 content schedule plan.
- 기존 ChatGPT/Gemini 측정 엔진을 사용하되 측정 방식 라벨과 실패 처리를 명확히 한다.
- 기존 PDF/GCS report delivery.

## 17. Later Scope / Not Now

- AI 노출 또는 상위 추천 보장 기능.
- Google Business Profile/Naver Place/YouTube/홈페이지 CMS 자동 수정.
- 자동 외부 크롤링 기반 source ingestion.
- 원장 self-serve portal.
- 환자 예약/전화/CRM 전환 추적.
- 리뷰 생성, 리뷰 대행, 허위 평판 관리.
- 의료광고 심의 제출 자동화.
- Perplexity/Claude/Google AI Overview 전체 플랫폼 확장.
- LLM citation attribution의 통계적 인과 모델링.
- 완전 자동 발행.
- 기존 병원 홈페이지 대체 목적의 디자인 빌더.

## 18. Risks and Open Questions

Risks:

- 실제 ChatGPT Search 측정 방식이 불명확하면 고객에게 잘못된 SoV 이름으로 설명할 수 있다.
- Gemini/Google grounding 결과는 위치, 시간, 계정, 검색 상태에 따라 변동성이 크다.
- AI 노출 개선과 콘텐츠 발행 사이의 인과를 단기적으로 증명하기 어렵다.
- source 품질이 낮으면 병원 고유성 없는 콘텐츠가 생성된다.
- 의료광고 표현이 고객 기대와 충돌할 수 있다.
- Public webblog가 기존 병원 홈페이지와 역할이 겹쳐 고객 혼란을 만들 수 있다.

Open questions:

- ChatGPT 측정은 실제 web search/search API를 사용할 것인가, OpenAI model response SoV로 라벨링할 것인가?
- Query Target 기본 세트는 진료과별 템플릿을 얼마나 세분화할 것인가?
- 고객용 리포트에서 경쟁 병원명을 어디까지 노출할 것인가?
- Public webblog의 canonical domain은 Re:putation subpath, 병원 subdomain, 병원 owned domain 중 무엇을 기본으로 할 것인가?
- 의료광고 리스크 검수 책임 경계는 MotionLabs 운영자와 병원 중 어떻게 문서화할 것인가?

## 19. Implementation Phases

### Phase 0. Positioning Cleanup

- 문구/네비게이션/리포트 명칭에서 홈페이지 빌더 인상을 제거한다.
- 고객-facing 기본 문장을 "AI 노출 웹블로그 운영대행"으로 통일한다.

### Phase 1. Query Strategy Foundation

- AIQueryTarget/Variant/MeasurementRun 모델과 Admin CRUD를 추가한다.
- 기존 QueryMatrix/SovRecord를 Query Target에 연결한다.
- 측정 방식 라벨과 실패 상태를 분리한다.

### Phase 2. Diagnosis and Action Loop

- GapDiagnosisService와 ExposureActionEngine v1을 구현한다.
- Dashboard에 next action과 월간 action queue를 노출한다.

### Phase 3. Query-Linked Content Operations

- ContentBrief 모델/API/Admin UX를 추가한다.
- 월간 콘텐츠 슬롯을 Query Target/ExposureAction에 연결한다.
- Claude generation prompt에 brief와 approved Essence를 함께 넣는다.

### Phase 4. Public Webblog IA and Technical Surface

- Public site를 webblog hub 중심으로 재구성한다.
- Query cluster pages, internal links, llms.txt/schema/sitemap enhancements를 적용한다.
- Public API가 public-safe content만 노출하는지 재검증한다.

### Phase 5. Report V2 and Operating QA

- V0/monthly report를 AI 노출 웹블로그 운영 리포트로 재설계한다.
- 내부 screening summary와 고객용 PDF를 분리한다.
- 로컬 demo seed/E2E에 Query Target, ExposureAction, Report v2 흐름을 포함한다.

## 20. Acceptance Criteria

제품 포지셔닝:

- Admin, report, customer-facing wording에서 Re:putation이 "AI 노출 웹블로그 운영대행"으로 설명된다.
- "홈페이지 빌더" 또는 "AI 노출 보장"으로 오해될 핵심 문구가 제거되거나 금지 문구로 분류된다.

운영 루프:

- 한 병원에서 AI Query Target을 만들고 query variant를 연결할 수 있다.
- baseline measurement run이 생성되고 성공/실패/미언급이 분리 저장된다.
- Query Target별 SoV, 경쟁 병원 언급, gap status가 Admin에서 보인다.
- gap diagnosis 결과로 ExposureAction이 생성된다.
- 월간 콘텐츠 brief가 Query Target과 approved Content Philosophy에 연결된다.
- 발행 콘텐츠가 어떤 Query Target을 커버하는지 확인할 수 있다.

웹블로그:

- Public webblog에 병원 facts, 콘텐츠 허브, query cluster, content detail이 SSR로 노출된다.
- sitemap, robots, llms.txt, JSON-LD가 query-linked webblog 구조를 반영한다.
- approved/aligned/published 콘텐츠만 public에 노출된다.

리포트:

- V0/monthly report가 SoV 수치만이 아니라 Query Target, gap diagnosis, completed action, next month strategy를 포함한다.
- 고객용 리포트에는 AI 노출 보장 문구가 없다.
- 내부 운영 finding은 Admin에서 볼 수 있으나 public/report 고객용 문구와 분리된다.

컴플라이언스/보안:

- 승인된 Content Philosophy가 없으면 자동 발행이 차단된다.
- 의료광고 금지 표현이 생성/수정/발행 경로에서 차단된다.
- raw source, evidence excerpt, operator note, API secret은 public surface에 노출되지 않는다.

릴리즈:

- 신규 PRD 기준 demo hospital이 onboarding -> query target -> baseline -> diagnosis -> content brief -> publish -> re-measure -> report 흐름을 통과한다.
- 기존 profile, Essence, content, site, report 기능은 회귀 없이 동작한다.
