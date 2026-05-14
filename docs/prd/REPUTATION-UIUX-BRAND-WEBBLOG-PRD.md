# Re:putation UIUX 전면 개편 PRD

작성일: 2026-05-15
제품명: Re:putation
운영사: MotionLabs Inc.
문서 목적: Re:putation의 Admin 운영 콘솔과 Public Webblog를 "AI 검색 가능성을 높이는 병원 웹블로그"라는 제품 명제에 맞게 전면 개편하고, 병원별 브랜드가 신뢰 가능한 방식으로 드러나도록 UIUX 요구사항을 정의한다.

## 1. Executive Summary

Re:putation의 UIUX 개편은 예쁜 병원 홈페이지를 만드는 작업이 아니다. 목표는 병원별 AI 노출 운영 과정을 Admin에서 명확히 관리하고, Public Webblog에서는 AI crawler와 환자가 병원의 공식 정보, 의료진 신뢰, 진료 콘텐츠, 지역성을 쉽게 이해하도록 만드는 것이다.

이번 개편의 핵심은 두 가지다.

- Admin은 병원별 AI 노출 운영을 실행하는 command center가 되어야 한다.
- Public Webblog는 병원별 브랜드가 드러나는 AI-readable medical knowledge hub가 되어야 한다.

브랜드 표현은 시각 장식이 아니라 검증 가능한 병원 엔티티, 의료진, 진료 철학, 실제 사진, 공식 채널, 지역 정보, 콘텐츠 톤으로 구현한다. 색상·레이아웃·사진 톤은 브랜드 레이어로 허용하되, HTML 텍스트, schema, sitemap, llms.txt, canonical, 검수 정보와 충돌해서는 안 된다.

## 2. Product Positioning

### One-Sentence Definition

Re:putation UIUX는 운영자가 병원별 AI 노출을 계획·실행·검증하고, 환자와 AI가 병원의 전문성·지역성·브랜드를 신뢰 가능한 공개 정보로 이해하게 만드는 운영형 웹블로그 경험이다.

### What It Is / What It Is Not

| 구분 | 정의 |
| --- | --- |
| It is | AI 노출 운영 콘솔, 병원 엔티티 기반 웹블로그, 검수형 의료 콘텐츠 허브, 병원별 브랜드 레이어, query-linked content UX, local trust UX |
| It is not | 병원 홈페이지 제작, 브랜드 랜딩페이지, 단순 블로그 스킨, 검색 순위 보장 UI, 과장 광고형 의료 마케팅 페이지 |

### Customer-Facing Wording

권장 문구:

- "AI가 참고할 수 있는 병원 정보와 검수형 건강 콘텐츠를 운영합니다."
- "병원의 진료 철학과 전문성을 환자 질문 중심의 콘텐츠로 축적합니다."
- "공식 정보, 의료진 검수, 진료 콘텐츠, 지역 정보를 한곳에 정리해 AI 검색 환경에 대응합니다."

금지 문구:

- "AI 검색 1위 디자인"
- "ChatGPT 추천 보장"
- "병원 홈페이지 자동 리뉴얼"
- "브랜드만 바꾸면 AI에 노출됩니다."
- "전문성을 가장 강하게 포장합니다."

## 3. Reference Strategy

이번 개편은 하나의 레퍼런스를 복제하지 않는다. 역할별 reference stack을 조합한다.

| 역할 | 레퍼런스 | 채택 이유 | 적용 영역 |
| --- | --- | --- | --- |
| 의료 콘텐츠 신뢰 | Cleveland Clinic Health Library / Health Essentials | 전문가 검수, 쉬운 설명, 환자 질문 예측, 의료 콘텐츠 톤이 강함 | 콘텐츠 상세, 검수 표시, 관련 질문, 출처 UX |
| 의료 정보 정책 | Mayo Clinic Health Information Policy | plain language, evidence, metadata/findability, review schedule이 명확함 | 콘텐츠 품질 표기, 업데이트 기준, AI findability |
| 브랜드 경험 | One Medical | 인간적 진료 경험, 접근성, 의료진 관계, 실제 공간 경험을 브랜드로 표현함 | 병원별 hero, 진료 철학, 사진/공간 섹션 |
| 지역 엔티티 신뢰 | Google Business Profile / LocalBusiness | 주소, 전화, 시간, 사진, 리뷰, 지역 관련성 신호가 명확함 | ContactCard, schema, 공식 채널, local trust panel |
| 내부 운영 UX | Stripe Dashboard / Linear | 복잡한 운영 상태를 조용하고 밀도 있게 보여줌 | Admin dashboard, action queue, state management |

레퍼런스 검증 원칙:

- Public Webblog는 Cleveland Clinic처럼 읽히되, 각 병원 고유 브랜드가 One Medical처럼 느껴져야 한다.
- Admin은 Stripe/Linear처럼 조용하고 명확해야 하며, 마케팅 페이지처럼 보이면 안 된다.
- 지역 병원 브랜드는 LocalBusiness 사실 구조와 일치해야 한다.
- 레퍼런스의 시각 스타일보다 정보 구조와 신뢰 장치를 우선한다.

## 4. Current System Baseline

현재 유지할 자산:

- `admin`: 병원별 profile, dashboard, content, schedule, reports, essence 운영 화면.
- `site`: 병원별 SSR public page, content index/detail, doctor/treatments/visit pages, sitemap, robots, llms.txt.
- `motionlabs-ui`: Admin 화면의 상위 디자인 시스템. `@motionz-kr/ui`, `ui-ground`, `packages/library/llms.txt`, MCP 데이터를 Admin UI 구현의 기준으로 사용한다.
- `Hospital` profile: 병원명, 주소, 전화, 진료시간, 지역, specialties, keywords, 원장 정보, 사진, 외부 공식 채널.
- `director_credentials`: 원장 학력, 보드 인증, 학회, 주요 자격 정보.
- `photos`: 원장, 병원 외관, 병원 내부, 진료실/시술실 등 실제 이미지.
- `google_business_profile_url`, `google_maps_url`, `naver_place_url`, `kakao_place_url`, `wikidata_qid`, `hira_org_id`.
- approved Content Philosophy를 콘텐츠 기준으로 삼는 정책.
- Public content detail의 Article, FAQPage, MedicalProcedure, HowTo, MedicalWebPage, Breadcrumb JSON-LD.
- 병원별 `llms.txt`와 sitemap/robots 기반 AI crawler 접근 구조.

유지하되 재정의할 자산:

- `director_philosophy` 자유 입력은 public brand claim의 직접 원천으로 쓰지 않는다. 승인된 Content Philosophy에서 public-safe care principle로 정제한다.
- Public Webblog의 첫 화면은 병원 소개 랜딩이 아니라 병원 엔티티와 AI 질문형 콘텐츠 허브의 entry page다.
- 병원 공식 홈페이지 링크는 보조 CTA로 둔다. Re:putation webblog가 병원 공식 홈페이지처럼 오해되면 안 된다.

## 5. Target Users

### Admin

- 운영자: 병원별 AI 노출 상태, 다음 작업, 콘텐츠 품질, 발행 상태, 리포트 전달 상태를 관리한다.
- AE/CS: 병원 원장에게 현재 상태와 다음 액션을 설명한다.
- 콘텐츠 에디터: Query Target, Content Philosophy, source evidence를 기준으로 brief와 콘텐츠를 검수한다.
- 관리자: 병원별 readiness, 운영 리스크, 월간 진행률을 확인한다.

### Public Webblog

- 환자: 지역 병원, 진료 영역, 원장, 콘텐츠 신뢰도, 방문 정보를 확인한다.
- AI crawler/search engine: 병원 facts, 의료 콘텐츠, 검수 주체, 내부 링크, 구조화 데이터를 읽는다.
- 병원 원장/스태프: 자사 브랜드가 과장 없이 신뢰감 있게 표현되는지 확인한다.

## 6. UX Principles

### 공통 원칙

- AI-readable first: 중요한 정보는 이미지 안 텍스트가 아니라 SSR HTML 텍스트로 노출한다.
- Evidence-backed brand: 브랜드 주장은 source, official channel, Content Philosophy, 의료진 정보에 근거해야 한다.
- Low-noise medical UX: 의료 서비스는 과도한 애니메이션, 과장된 hero, 감성 카피보다 명확성과 신뢰가 우선이다.
- Consistent entity facts: 병원명, 주소, 전화, 시간, 지도, 외부 링크는 UI, schema, llms.txt에서 같은 값이어야 한다.
- No homepage confusion: Public Webblog는 병원 공식 홈페이지가 아니라 AI 노출용 지식 기반임을 구조적으로 유지한다.

### Admin 원칙

- Action over decoration: 운영자가 다음 작업을 바로 판단할 수 있어야 한다.
- Status is the interface: readiness, gaps, approvals, failures, publish state가 UI의 핵심이다.
- Dense but calm: dashboard는 정보 밀도가 높아도 시각적으로 조용해야 한다.
- Every metric needs an action: 수치만 보여주지 말고 연결된 gap/action을 보여준다.

### Public Webblog 원칙

- Hospital identity first: 첫 viewport에서 병원명, 지역, 진료영역, 검수 주체가 즉시 보여야 한다.
- Trust before persuasion: 예약/전화 CTA보다 검수, 출처, 공식 정보, 실제 사진이 먼저 신뢰를 만든다.
- Brand as care evidence: 병원 브랜드는 색상보다 의료진, 진료 철학, 콘텐츠 관점, 공간 사진, 지역 정보로 표현한다.
- Content clusters over feeds: 최신글 나열보다 환자 질문 묶음과 대표 진료 주제가 중요하다.

## 7. Information Architecture

### Admin IA

기본 병원 상세 IA:

1. Overview
2. AI Query Targets
3. Exposure Actions
4. Content Plan
5. Content Review
6. Webblog Coverage
7. Brand Layer
8. Profile / Entity Facts
9. Essence / Content Philosophy
10. Reports
11. Settings

탭별 핵심 역할:

| 탭 | 목적 |
| --- | --- |
| Overview | 병원별 AI 노출 운영 현황과 다음 액션 표시 |
| AI Query Targets | 타겟 질의, 플랫폼, 경쟁 병원, 우선순위 관리 |
| Exposure Actions | gap diagnosis 결과를 실행 가능한 작업으로 관리 |
| Content Plan | Query Target 기반 월간 brief와 콘텐츠 계획 관리 |
| Content Review | 생성 콘텐츠 검수, 의료광고 리스크, 발행 승인 |
| Webblog Coverage | sitemap, schema, llms.txt, page coverage 점검 |
| Brand Layer | 병원별 public-safe 브랜드 원칙, 사진, 톤, visual token 관리 |
| Profile / Entity Facts | 주소, 전화, 영업시간, 지도, 공식 채널 등 사실 데이터 관리 |
| Essence / Content Philosophy | source-backed 병원 철학과 콘텐츠 기준 승인 |
| Reports | 원장 전달용 리포트와 내부 운영 메모 분리 |

### Public Webblog IA

병원별 public entry page:

1. Clinic Header
2. Brand-Aware Hero
3. AI-Readable Hospital Facts
4. Representative Answer Clusters
5. Featured Medical Content
6. Care Principles
7. Doctor / Reviewer Identity
8. Clinic Photo Evidence
9. Local Trust / Contact
10. Footer with official channels and caveat

콘텐츠 상세 page:

1. Breadcrumb
2. Question-led title
3. Short answer / TLDR
4. Reviewed-by and updated date
5. Main medical explanation
6. Clinic-specific care perspective
7. FAQ / related patient questions
8. Related treatment cluster
9. Sources / references
10. Local contact module

Query cluster page:

1. Cluster title based on patient intent
2. Covered questions
3. Representative answer
4. Linked articles
5. Related treatments
6. Doctor/reviewer identity
7. Entity facts and official channels

## 8. Brand Layer Requirements

### 8.1 Brand Layer Definition

Brand Layer는 병원별로 다음 정보를 public-safe하게 표현하는 구조다.

- Brand statement: 병원명, 지역, 대표 진료영역, 검수 주체를 포함한 한 문장.
- Care principles: 승인된 Content Philosophy에서 추출한 3-4개 진료 원칙.
- Tone rules: 환자에게 말하는 방식, 피해야 할 표현, 설명의 깊이.
- Visual tokens: accent color, secondary color, neutral background, CTA style, badge style.
- Photo policy: 원장 사진, 외관, 내부, 진료공간, 장비 사진 사용 우선순위.
- Proof points: 자격, 학회, 공식 채널, 지역 정보, 주요 콘텐츠 클러스터.

### 8.2 Brand Layer Constraints

- 브랜드 주장은 visible content, schema, llms.txt와 충돌하면 안 된다.
- "최고", "유일", "완치", "부작용 없음", 경쟁 비방성 표현은 사용할 수 없다.
- 근거 없는 브랜드 문구는 Admin에서 unsupported claim으로 표시한다.
- 병원별 색상은 WCAG AA contrast를 통과해야 한다.
- 병원별 폰트 변경은 기본적으로 허용하지 않는다. 가독성과 운영 일관성을 우선한다.
- 이미지는 실제 병원/의료진/공간 사진을 우선한다. stock-like 의료 이미지는 보조로만 사용한다.

### 8.3 Brand Layer Admin Fields

- `brand_statement`
- `care_principles`
- `tone_keywords`
- `avoid_tone_keywords`
- `brand_accent_color`
- `brand_secondary_color`
- `brand_surface_color`
- `cta_label_policy`
- `photo_priority`
- `hero_layout_variant`
- `brand_claim_approval_status`
- `brand_claim_source_notes`

### 8.4 Brand Layer Public Rendering

- Hero는 병원명과 검수 주체를 최상위 정보로 둔다.
- Care Principles는 콘텐츠 클러스터와 연결한다.
- DoctorIntro는 원장 소개 카드가 아니라 검수/진료 관점의 신뢰 장치로 강화한다.
- ClinicGallery는 분위기 사진 갤러리가 아니라 실제 공간 증거로 사용한다.
- ContactCard는 CTA 카드가 아니라 local entity trust panel로 설계한다.

## 9. Admin UX Requirements

### 9.0 MotionLabs UI Governance

Admin은 MotionLabs 내부 운영 제품이므로 `motionlabs-ui` 규칙을 엄격히 적용한다. Admin UI 구현에서 이 PRD의 UX 요구사항과 `motionlabs-ui`가 충돌하면, 컴포넌트/토큰/타이포그래피/아이콘/폼 패턴은 `motionlabs-ui`를 우선한다. 단, AI Slop 금지, 접근성, 의료광고 컴플라이언스, 운영 정보 구조 요구사항은 완화할 수 없다.

Source of truth:

- Repository: `https://github.com/motionz-kr/motionlabs-ui`
- Component package: `@motionz-kr/ui`
- Component source: `packages/library/src/components/`
- Custom component source: `packages/library/src/custom-components/`
- Icon source: `packages/library/src/icons/`
- Preview/documentation app: `apps/ui-ground/`
- AI-readable component reference: `packages/library/llms.txt`
- MCP component/token/icon data: `packages/mcp-server/`

Strict requirements:

- Admin 신규 UI는 가능한 한 `@motionz-kr/ui`의 공식 컴포넌트를 먼저 사용한다.
- 공식 컴포넌트가 없을 때만 `custom-components` 패턴을 검토하고, 임시 bespoke 컴포넌트는 사용 이유를 PR/작업 문서에 남긴다.
- 색상은 디자인 토큰 CSS 변수만 사용한다. 하드코딩 색상은 금지한다.
- typography는 `body1~4`, `title1~3`, `heading1~3`, `details1~3`처럼 MotionLabs UI에 정의된 클래스만 사용한다.
- form 컴포넌트는 controlled usage를 기본으로 한다.
- Button, Input, SearchInput, Dropdown, MultiSelectDropdown, DatePicker, DateRangePicker, Modal, Drawer, PopUp, Toast, Tooltip, Badge, Table, Pagination, ProgressBar 등 기존 컴포넌트가 있는 영역은 직접 재구현하지 않는다.
- Admin icon은 `@motionz-kr/ui/icons`를 우선 사용한다.
- Admin CSS는 `@motionz-kr/ui/styles` 또는 reset 충돌 시 `@motionz-kr/ui/styles/tokens`를 기준으로 import한다.
- Admin 화면 변경은 `ui-ground` 또는 equivalent preview/screenshot으로 시각 검증해야 한다.
- `motionlabs-ui` 컴포넌트/API/아이콘/토큰 변경이 필요한 경우, 변경은 해당 repo의 workflow를 따른다: `pnpm extract-props`, `pnpm sync:docs`, `pnpm type-check`, `pnpm build`, `pnpm validate:mcp`, `pnpm mcp:build`.
- `generated/props.json`, `packages/library/llms.txt`, `packages/mcp-server/src/data.ts`의 동기화 상태가 맞지 않으면 Admin UI 구현 기준으로 삼지 않는다.

### 9.1 Overview Dashboard

상단 요약:

- AI Exposure Readiness score
- Active Query Targets
- Open Exposure Actions
- Content waiting for review
- Webblog coverage status
- Report due status

필수 UI:

- Next Actions queue: 우선순위, owner, due date, blocker, expected impact.
- Query Coverage snapshot: target별 content/schema/llms/status.
- Recent measurement summary: platform별 SoV, failure rate, competitor mentions.
- Risk alerts: approval missing, unsupported brand claim, stale content, schema mismatch.

성공 기준:

- 운영자가 병원 상세 진입 후 10초 안에 가장 중요한 다음 작업을 알 수 있어야 한다.
- 지표 클릭 시 관련 target/action/content/report로 이동해야 한다.

### 9.2 AI Query Targets

필수 UI:

- Target list with priority, intent, platform, region, treatment, status.
- Target detail: variants, competitor names, linked contents, latest SoV, gaps.
- Bulk priority edit.
- Target health badge: covered, weak, missing, blocked.

UX 기준:

- 질의 variant만 보여주는 테이블이 아니라, 운영 전략 단위인 target을 중심으로 설계한다.
- target별 "왜 중요한가"와 "이번 달 무엇을 해야 하는가"를 표시한다.

### 9.3 Exposure Actions

필수 UI:

- Kanban 또는 grouped list: TODO, IN_PROGRESS, DONE, BLOCKED.
- Gap type filter: content, entity, technical, source, brand, local.
- Action detail: evidence, linked target, linked content, expected impact.

UX 기준:

- Action은 체크리스트가 아니라 월간 운영 계약의 실행 단위다.
- DONE 처리 시 evidence 또는 linked artifact가 필요하다.

### 9.4 Content Plan / Review

필수 UI:

- Monthly content calendar.
- Query Target linked brief.
- Approved Content Philosophy badge.
- Required messages / avoid messages.
- Medical ad risk panel.
- Internal link recommendations.

UX 기준:

- 콘텐츠 카드에는 제목보다 target, intent, approval state, risk state가 먼저 보여야 한다.
- "발행" CTA는 모든 gate 통과 전 비활성화한다.

### 9.5 Webblog Coverage

필수 UI:

- Hospital facts completeness.
- Schema validation status.
- llms.txt presence and last updated.
- Sitemap inclusion.
- Canonical status.
- Content cluster coverage.
- Local channel consistency.

UX 기준:

- Public page preview와 technical checklist를 함께 보여준다.
- 불일치 값은 "UI value / schema value / llms value"로 비교한다.

### 9.6 Brand Layer

필수 UI:

- Brand statement editor.
- Care principles editor sourced from approved Content Philosophy.
- Visual token controls with contrast validation.
- Photo inventory with usage tags.
- Unsupported claim warning.
- Public preview of Hero, Care Principles, DoctorIntro, ContactCard.

UX 기준:

- 브랜드 편집은 자유 마케팅 카피 입력이 아니라 검증 가능한 claim 관리여야 한다.
- 색상보다 문장, 증거, 사진, 콘텐츠 연결을 더 중요한 UI로 배치한다.

## 10. Public Webblog UX Requirements

### 10.1 Clinic Header

- 병원명, "AI 진료 블로그" 성격, 지역/진료영역, 핵심 nav, 전화 CTA, 공식 홈페이지 링크를 표시한다.
- header의 nav는 콘텐츠 허브 탐색을 우선하고, 공식 홈페이지는 보조 링크로 둔다.
- 모바일에서는 전화, 블로그, 진료영역, 방문 정보가 1 depth 안에서 접근 가능해야 한다.

### 10.2 Brand-Aware Hero

필수 정보:

- H1: 병원명 또는 병원명 중심 문장.
- Subcopy: 지역, 대표 진료영역, 검수 주체, 콘텐츠 허브 성격.
- Trust chips: 원장/의료진 검수, 콘텐츠 수, 주요 진료영역, 지역.
- Primary CTA: 콘텐츠 전체 보기 또는 대표 질문 보기.
- Secondary CTA: 전화 또는 방문 정보.

금지:

- 추상 gradient hero.
- stock-like 의사 이미지 중심 hero.
- "프리미엄", "최고", "1위" 등 근거 없는 브랜드 카피.
- 병원 공식 홈페이지처럼 오해되는 full marketing hero.

### 10.3 AI-Readable Hospital Facts

필수 정보:

- 병원명
- 주소
- 전화
- 진료시간
- 진료과/주요 진료영역
- 원장/대표 의료진
- 공식 홈페이지
- Google Maps / Google Business Profile
- Naver Place / Kakao Place
- HIRA or other public-safe official IDs when available

UX 기준:

- fact table은 사용자가 읽기 쉬워야 하고, AI crawler가 파싱하기 쉬운 HTML이어야 한다.
- schema, llms.txt, visible UI 값이 동일해야 한다.

### 10.4 Representative Answer Clusters

필수 클러스터:

- 증상/질환 질문
- 치료/시술 질문
- 방문/비용/준비 질문
- 지역 병원 선택 질문
- 원장 칼럼/진료 관점

UX 기준:

- "최신글"보다 "이 병원이 답하는 대표 질문"을 먼저 보여준다.
- 각 cluster는 linked articles, treatment, reviewer, updated date를 가진다.

### 10.5 Care Principles

필수 UI:

- 3-4개 원칙.
- 각 원칙은 approved Content Philosophy와 연결된 public-safe 문장.
- 각 원칙은 관련 콘텐츠 또는 treatment cluster로 연결.

예시 구조:

- "증상을 먼저 설명합니다" → 관련 FAQ cluster.
- "시술 전후 과정을 구체적으로 안내합니다" → treatment cluster.
- "불필요한 과장 표현을 피합니다" → editorial policy note.

### 10.6 Doctor / Reviewer Identity

필수 UI:

- 원장 사진 또는 의료진 사진.
- 이름, 직함, 주요 진료영역.
- public-safe credentials.
- "이 콘텐츠 허브의 의료 검수 주체" 설명.
- 관련 콘텐츠 수와 대표 콘텐츠.

주의:

- 과도한 인물 브랜딩보다 콘텐츠 신뢰의 anchor로 사용한다.
- 자격 정보는 source-backed 값만 표시한다.

### 10.7 Clinic Photo Evidence

필수 UI:

- 외관, 접수/대기 공간, 진료실, 시술실 등 실제 공간 사진.
- 사진별 caption과 source type.
- 모바일에서도 이미지가 콘텐츠를 밀어내지 않도록 stable aspect ratio 적용.

주의:

- 원장 사진은 DoctorIntro에서 우선 표시하고 갤러리에서는 중복 노출을 제한한다.
- 의료광고 리스크가 있는 전후 사진은 별도 정책 없이는 사용하지 않는다.

### 10.8 Local Trust / Contact

필수 UI:

- 주소, 전화, 진료시간.
- 지도 링크.
- 공식 홈페이지, 네이버 플레이스, 카카오맵, Google Business Profile.
- 방문 전 확인 caveat.

UX 기준:

- 이 섹션은 conversion CTA가 아니라 local entity trust를 강화하는 구조다.
- 전화 CTA는 명확하되, 의료 상담/진단을 암시하지 않는다.

### 10.9 Content Detail

필수 UI:

- 질문형 제목 또는 환자 intent가 드러나는 제목.
- 첫 화면 안에 short answer.
- reviewed by / updated date.
- related hospital/treatment context.
- FAQ block.
- sources/references.
- related questions and internal links.

UX 기준:

- 브랜드 톤은 "병원다운 설명 방식"으로 드러나야 하며, 의학적 사실 설명을 흐리면 안 된다.
- 구조화 데이터는 visible content와 일치해야 한다.

## 11. Visual Design Requirements

### 11.1 Overall Direction

- 의료 신뢰, 운영 명확성, 지역 병원 친밀감을 함께 표현한다.
- Public은 따뜻하지만 과장되지 않은 editorial medical hub.
- Admin은 차분하고 밀도 높은 operating dashboard.
- AI Slop으로 보이는 시각 패턴은 엄격히 금지한다. 그라데이션 배경, 과도한 쉐도우, glow, glassmorphism, floating orb, bokeh blob, 불필요한 blur, 장식용 3D, 의미 없는 oversized illustration을 기본 디자인 언어로 사용하지 않는다.
- 시각적 완성도는 장식 효과가 아니라 정보 위계, 여백, 타이포그래피, 실제 사진, 명확한 상태 표현, 일관된 컴포넌트로 만든다.
- Admin의 시각 언어는 독자적으로 새로 만들지 않는다. MotionLabs UI의 token, typography, component rhythm, icon set을 그대로 따른다.

### 11.2 Layout

- Cards는 반복 항목, modal, framed tool에만 사용한다.
- 페이지 전체를 카드 묶음처럼 만들지 않는다.
- Admin은 table, split pane, status rail, action queue를 적극 사용한다.
- Public은 full-width section과 constrained content column을 사용한다.

### 11.3 Typography

- 본문 가독성을 우선한다.
- hero-scale typography는 Public Hero에만 제한적으로 사용한다.
- Admin panel/card 내부 heading은 작고 명확하게 유지한다.
- viewport width 기반 font scaling은 사용하지 않는다.

### 11.4 Color

- 기본 palette는 medical neutral 기반.
- 병원별 accent는 Brand Layer token으로 제한한다.
- 단일 hue 계열로 화면 전체를 지배하지 않는다.
- CTA와 badge는 contrast validation을 통과해야 한다.
- 그라데이션은 기본 금지한다. 예외는 데이터 시각화에서 연속값을 표현해야 하거나, 브랜드 가이드에 명시된 경우로 제한하며 PRD/디자인 리뷰에서 사유를 남겨야 한다.
- 배경은 solid color, subtle border, section band, 실제 사진 기반으로 구성한다. 장식용 radial gradient, mesh gradient, aurora gradient는 사용하지 않는다.

### 11.5 Shadow / Depth

- 쉐도우는 기본 금지한다. 구분이 필요하면 border, background contrast, spacing, typography hierarchy를 우선 사용한다.
- 예외적으로 modal, dropdown, popover처럼 실제 z-axis가 필요한 overlay에만 낮은 강도의 단일 shadow를 허용한다.
- card hover에서 떠오르는 효과, 강한 drop shadow, colored shadow, glow outline, neumorphism-style depth는 사용하지 않는다.
- Public Webblog에서 병원 신뢰를 강조하기 위해 그림자나 glow를 쓰지 않는다. 신뢰는 검수 정보, 실제 사진, 공식 링크, 출처로 표현한다.

### 11.6 Iconography

- Admin actions, status, filters에는 lucide icon 또는 기존 icon system을 사용한다.
- 의미 없는 장식 아이콘은 사용하지 않는다.
- unfamiliar icon에는 tooltip을 제공한다.

### 11.7 Motion

- Admin motion은 상태 전환과 feedback에만 제한한다.
- Public motion은 content reveal, hover, tab transition 수준으로 제한한다.
- hero나 background에 과도한 animated decoration을 사용하지 않는다.
- scroll-triggered spectacle, parallax decoration, floating decorative objects, animated gradient background는 사용하지 않는다.

## 12. Accessibility Requirements

- 모든 interactive target은 최소 44px touch target을 만족한다.
- keyboard focus가 명확히 보여야 한다.
- color만으로 상태를 전달하지 않는다.
- 병원별 brand color 적용 후 WCAG AA contrast를 검사한다.
- 이미지에는 의미 있는 alt를 제공한다.
- 콘텐츠 구조는 heading hierarchy를 지킨다.
- mobile에서 button/card text가 overflow되지 않아야 한다.

## 13. Technical Requirements

### 13.0 Admin Design System Integration

Admin은 `@motionz-kr/ui`를 디자인 시스템 의존성으로 사용한다.

설치 및 CSS 기준:

```css
@import "tailwindcss";
@source "./node_modules/@motionz-kr/ui/src";
@import "@motionz-kr/ui/styles";
```

reset 충돌이 있는 경우:

```css
@import "tailwindcss";
@source "./node_modules/@motionz-kr/ui/src";
@import "@motionz-kr/ui/styles/tokens";
```

사용 기준:

```tsx
import { StandardButton, Input, Modal } from "@motionz-kr/ui";
import { SearchIcon } from "@motionz-kr/ui/icons";
import { cn } from "@motionz-kr/ui/utils/cn";
import "@motionz-kr/ui/styles";
```

Admin 구현자는 `packages/library/llms.txt`의 Component Reference와 Props 설명을 기준으로 컴포넌트 API를 확인한다. Props description/default의 단일 진실 원천은 component source의 Props type과 JSDoc이다.

### 13.1 SSR / Crawlability

- Public Webblog 주요 정보는 SSR HTML에 포함한다.
- client-only rendering으로 병원 facts, content title, answer, source, reviewer 정보를 숨기지 않는다.
- lazy loading 이미지는 가능하나 핵심 텍스트는 즉시 노출한다.

### 13.2 Schema / Metadata

지원 schema:

- `MedicalClinic`
- `LocalBusiness`
- `Physician`
- `Article`
- `FAQPage`
- `MedicalProcedure`
- `MedicalWebPage`
- `BreadcrumbList`
- `CollectionPage`

요구사항:

- visible content와 JSON-LD 값은 일치해야 한다.
- 병원 facts 변경 시 schema, llms.txt, sitemap lastmod가 갱신되어야 한다.
- query cluster page는 CollectionPage 또는 적절한 WebPage schema를 가진다.

### 13.3 llms.txt

- 병원 facts, official links, director credentials, treatments, public content URLs, cluster URLs를 포함한다.
- unsupported claim, internal note, raw evidence excerpt는 노출하지 않는다.
- Brand Layer의 care principles는 승인된 public-safe 문장만 포함한다.

### 13.4 Design System

필수 token:

- `brandAccent`
- `brandSecondary`
- `brandSurface`
- `trustBadge`
- `riskBadge`
- `contentCluster`
- `entityFact`
- `adminStatus`

컴포넌트 우선 개선 대상:

- `ClinicHeader`
- `ClinicHero`
- `FeaturedContent`
- `DoctorIntro`
- `ClinicGallery`
- `ContactCard`
- content detail header/source/FAQ modules
- admin dashboard cards/action queue/tables

## 14. Data Requirements

추가 또는 정규화할 데이터:

- `brand_statement`
- `care_principles`
- `tone_keywords`
- `avoid_tone_keywords`
- `brand_visual_tokens`
- `photo_usage_priority`
- `query_cluster_summary`
- `public_brand_claims`
- `brand_claim_source_links`
- `brand_claim_approval_status`

기존 데이터 활용:

- `director_credentials`는 Doctor/Reviewer trust에 사용한다.
- `photos`는 Gallery와 Hero visual에 사용한다.
- `google_business_profile_url`, `naver_place_url`, `kakao_place_url`, `hira_org_id`는 local trust와 llms.txt에 사용한다.
- approved Content Philosophy는 Care Principles와 content tone에 사용한다.

## 15. Implementation Phases

### Phase 1: UX Foundation

- Admin IA 재정리.
- Public Webblog IA 확정.
- Brand Layer 데이터 contract 정의.
- existing components inventory.
- accessibility and crawlability checklist 작성.

완료 기준:

- 화면별 wireframe 또는 low-fidelity mockup.
- Brand Layer field contract.
- schema/llms visible value consistency matrix.

### Phase 2: Public Webblog Redesign

- ClinicHeader, ClinicHero, Hospital Facts, Answer Clusters, Care Principles, DoctorIntro, Gallery, ContactCard 개편.
- content detail short answer/reviewer/source/internal link 구조 강화.
- query cluster page가 없는 경우 추가.
- schema/llms/sitemap 반영.

완료 기준:

- 병원 main page에서 병원명, 지역, 진료영역, 의료진, 공식 채널, 대표 질문이 첫 화면과 HTML에 노출.
- Lighthouse accessibility baseline 통과.
- schema validator에서 critical error 없음.
- llms.txt에 public-safe brand facts 반영.

### Phase 3: Admin Redesign

- Overview dashboard 개편.
- AI Query Targets, Exposure Actions, Webblog Coverage, Brand Layer 탭 추가 또는 재구성.
- Content Plan/Review에 query target, brand claim, risk state 연결.
- Report review UX 개선.

완료 기준:

- 운영자가 병원별 next action을 dashboard에서 바로 처리 가능.
- Brand claim approval과 unsupported warning 동작.
- Webblog Coverage에서 UI/schema/llms mismatch 확인 가능.

### Phase 4: Visual System and QA

- 병원별 brand token 적용.
- responsive QA.
- accessibility QA.
- crawlability QA.
- design regression screenshot QA.

완료 기준:

- desktop/mobile 주요 breakpoint에서 overlap 없음.
- brand color contrast 통과.
- Public page text overflow 없음.
- Admin dense table/action queue 사용성 검증.

## 16. Success Metrics

### Product Metrics

- 병원별 AI Exposure Readiness 평균 상승.
- Query Target coverage 상승.
- Webblog Coverage critical issue 감소.
- 콘텐츠 발행 전 approval blocker 감소.
- 월간 report review time 감소.

### Public Webblog Metrics

- 병원 main page crawlable fact completeness.
- content detail structured data completeness.
- internal link depth 개선.
- representative query cluster coverage.
- official channel click-through.

### Admin Metrics

- 병원별 next action 식별 시간.
- overdue action 수.
- unsupported brand claim 수.
- schema/llms mismatch 수.
- content review cycle time.

### Qualitative Metrics

- 원장이 "우리 병원답다"고 느끼는지.
- 운영자가 "다음에 뭘 해야 하는지" 즉시 이해하는지.
- 환자가 "누가 검수했고 어디 병원인지" 즉시 이해하는지.
- AI crawler가 병원 facts와 콘텐츠 관계를 명확히 읽을 수 있는지.

## 17. Acceptance Criteria

### Admin

- 병원 상세 첫 화면에서 next action, readiness, open gaps, pending review가 보인다.
- Query Target과 Content, Exposure Action, Report가 서로 연결된다.
- Brand Layer에서 care principles와 visual token을 관리할 수 있다.
- unsupported brand claim은 public publish 대상에서 제외된다.
- Webblog Coverage에서 schema, llms.txt, sitemap, visible facts 불일치를 탐지한다.
- 모든 Admin 신규 UI는 `motionlabs-ui`의 공식 컴포넌트, 토큰, typography, icon 기준을 따른다.
- Admin에서 색상 하드코딩, 임의 typography class, 기존 컴포넌트 재구현이 없어야 한다.
- Admin UI 변경은 `ui-ground` 또는 equivalent preview/screenshot 검증 증거를 남긴다.

### Public Webblog

- 첫 viewport에서 병원명, 지역, 진료영역, 검수 주체, 콘텐츠 허브 성격이 보인다.
- 병원 facts가 HTML, schema, llms.txt에서 일치한다.
- 대표 질문 cluster가 최신글보다 우선 노출된다.
- Care Principles가 approved Content Philosophy 기반으로 표시된다.
- 원장/의료진 정보와 실제 공간 사진이 신뢰 장치로 배치된다.
- 콘텐츠 상세에는 short answer, reviewer, updated date, FAQ, source, internal links가 있다.

### Compliance

- 과장 표현과 의료 효능 보장성 표현은 차단된다.
- public page는 내부 메모와 raw evidence excerpt를 노출하지 않는다.
- 병원 공식 홈페이지로 오해될 표현을 사용하지 않는다.

### Visual Anti-Slop

- 그라데이션 배경, 과도한 쉐도우, glow, glassmorphism, floating orb, bokeh blob, 의미 없는 blur/3D/illustration이 없어야 한다.
- 화면 구분은 border, spacing, typography, solid surface, 실제 사진, 명확한 상태 컴포넌트로 해결해야 한다.
- shadow가 필요한 경우 modal/dropdown/popover 같은 overlay에만 제한적으로 사용되고, 사용 이유가 명확해야 한다.

## 18. Open Questions

- 병원별 Brand Layer 필드는 기존 Hospital profile에 둘 것인가, 별도 `hospital_brand_profiles`로 분리할 것인가?
- query cluster route를 현재 treatment route와 통합할 것인가, 별도 `/questions/{cluster}`로 둘 것인가?
- 병원별 accent color를 운영자가 직접 입력할 것인가, 제한된 palette에서 선택하게 할 것인가?
- 원장/의료진 검수 정보가 다수 의료진으로 확장될 경우 DoctorIntro를 어떻게 일반화할 것인가?
- Naver/Kakao/Google local channel 값의 최신성 검증을 자동화할 것인가?
- Public Webblog에서 "Re:putation 운영 콘텐츠" 표기를 어느 위치에 둘 것인가?

## 19. Risks

- 브랜드 표현이 강해지면 의료광고성 과장으로 흐를 수 있다.
- 병원별 visual customization이 커지면 디자인 시스템 유지비가 증가한다.
- 그라데이션, 과한 쉐도우, glow, glassmorphism 같은 AI Slop 시각 패턴이 의료 신뢰도와 제품 고유성을 떨어뜨릴 수 있다.
- 이미지 중심 hero는 AI-readable text 구조를 약화시킬 수 있다.
- Public Webblog가 병원 공식 홈페이지로 오해될 수 있다.
- Admin 기능이 많아지면 운영자가 핵심 action을 놓칠 수 있다.

대응:

- Brand Layer는 approval, source note, compliance gate를 가진다.
- visual customization은 token과 layout variant로 제한한다.
- Anti-slop visual checklist를 디자인 리뷰와 구현 QA의 release gate로 둔다.
- 핵심 텍스트는 SSR HTML로 유지한다.
- 공식 홈페이지 링크는 보조 CTA로 두고 webblog 성격을 명확히 한다.
- Admin dashboard는 next action 중심으로 유지한다.

## 20. Verification Plan

### Design QA

- Desktop, tablet, mobile 주요 viewport screenshot 검증.
- text overflow, overlap, card nesting, CTA visibility 확인.
- 병원별 brand token 적용 후 contrast 확인.
- gradient, excessive shadow, glow, glassmorphism, decorative blur/orb/bokeh/3D 사용 여부 확인.
- Admin은 `motionlabs-ui` token/component/typography/icon 준수 여부를 확인한다.
- Admin에서 기존 `@motionz-kr/ui` 컴포넌트를 직접 재구현한 경우, 예외 사유와 대체 계획을 확인한다.

### Technical QA

- Typecheck, lint, build.
- Public page SSR HTML에 핵심 facts 포함 여부 확인.
- sitemap, robots, llms.txt fetch 확인.
- JSON-LD visible content consistency 확인.

### Accessibility QA

- keyboard navigation.
- focus visible.
- color contrast.
- touch target.
- heading hierarchy.
- alt text.

### Content QA

- approved Content Philosophy 없는 care principle 노출 차단.
- unsupported brand claim 차단.
- medical ad risky language 차단.
- content detail source/reviewer/update date 확인.

## 21. Source References

- MotionLabs UI: https://github.com/motionz-kr/motionlabs-ui
- MotionLabs UI component reference: https://github.com/motionz-kr/motionlabs-ui/blob/main/packages/library/llms.txt
- Cleveland Clinic Editorial Policy: https://my.clevelandclinic.org/about/website/editorial-policy
- Mayo Clinic Health Information Policy: https://www.mayoclinic.org/about-this-site/health-information-policy
- One Medical About: https://www.onemedical.com/about-us/
- Google Business Profile local ranking: https://support.google.com/business/answer/7091
- Google LocalBusiness structured data: https://developers.google.com/search/docs/appearance/structured-data/local-business
