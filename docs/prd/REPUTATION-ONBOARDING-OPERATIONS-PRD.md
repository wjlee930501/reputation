# Re:putation 온보딩·운영 전환 UX PRD

작성일: 2026-05-15
제품명: Re:putation
운영사: MotionLabs Inc.
문서 목적: Atlas 기반 E2E QA에서 확인한 운영자·고객 관점의 마찰을 개선하기 위해, 상담 리드부터 신규 병원 온보딩, 자료 인입, 운영 기준 승인, 콘텐츠 운영 시작까지의 end-to-end 업무 흐름을 하나의 매끄러운 운영 프로세스로 재정의한다.

## 1. Executive Summary

현재 Re:putation은 병원별 프로파일, 온보딩 체크리스트, 자료 인입, 운영 기준, 콘텐츠, 스케줄, 리드 관리 등 핵심 화면을 갖추고 있다. 그러나 운영자가 실제 신규 병원을 온보딩할 때는 화면들이 느슨하게 연결되어 있어, 다음 작업을 스스로 판단하거나 같은 정보를 여러 화면에 다시 입력해야 하는 마찰이 남아 있다.

이번 PRD의 목표는 기능을 많이 추가하는 것이 아니라, 이미 존재하는 운영 기능을 "리드 → 병원 생성 → 프로파일 → 자료 인입 → 근거 처리 → 운영 기준 승인 → 콘텐츠 운영 시작 → 고객 공유"의 단일 업무 흐름으로 묶는 것이다.

핵심 개선 방향:

- 상담 리드를 병원 온보딩으로 즉시 전환한다.
- 신규 병원 등록 후 운영자를 프로파일 화면이 아니라 온보딩 허브로 안내한다.
- 온보딩 단계별 완료 기준을 실제 운영 품질 기준으로 강화한다.
- 프로파일에 입력된 공식 URL을 자료 인입 후보로 자동 재사용한다.
- 비동기 작업, 실패, 알림 상태를 온보딩 허브에서 한눈에 볼 수 있게 한다.
- 고객(의사)에게는 진행 상태와 미리보기, 환자에게는 신뢰 가능한 공개 정보와 CTA를 제공한다.

## 2. Problem Statement

### 운영자 문제

운영자는 신규 리드가 들어온 뒤 병원 계정을 만들고, 프로파일을 채우고, 자료를 인입하고, 근거를 처리하고, 운영 기준을 승인하고, 콘텐츠 운영을 시작해야 한다. 현재는 각 화면이 존재하지만 아래 문제가 있다.

- 상담 리드 목록에서 바로 병원 생성/온보딩을 시작할 수 없다.
- 신규 병원 등록 첫 화면이 병원명과 플랜만 받고 곧바로 프로파일로 이동해 전체 온보딩 맥락이 약하다.
- 프로파일에 입력한 홈페이지, 블로그, 네이버 플레이스, 구글 지도 URL을 자료 인입 단계에서 다시 입력해야 한다.
- 온보딩 5단계 완료 상태가 "최소 동작" 기준에 가까워 실제 운영 품질을 보장하기 어렵다.
- 프로파일 완료 후 자동으로 시작되는 리포트/허브 준비/알림 작업의 진행 상태가 명확히 보이지 않는다.
- 처리 불가능한 자료에도 처리 버튼이 노출되어 실패를 눌러본 뒤에야 이유를 알 수 있다.
- Slack/webhook 같은 운영 알림 실패가 사용자 화면 성공과 분리되어 조용히 누락될 수 있다.

### 고객 문제

의사 고객은 "우리 병원이 AI 답변에서 어떻게 보이기 시작하는지"를 알고 싶어 한다. 환자 고객은 "이 병원이 믿을 만한지, 어떻게 방문/상담할지"를 빠르게 판단하고 싶어 한다. 현재 public webblog는 주요 페이지가 잘 렌더링되지만 아래 개선 여지가 있다.

- 의사 고객에게 온보딩 진행률, 공개 미리보기, AI 질문 커버리지, 근거 기반 콘텐츠 상태를 설명하는 공유 화면이 부족하다.
- 깨진 외부 링크가 public site에 노출될 수 있다.
- 환자 CTA가 전화 중심이며, 지도/상담/예약 같은 실제 행동 경로가 더 세분화될 필요가 있다.
- 콘텐츠 상세에서 의료진 검토, 수정일, 답변하는 환자 질문, 주의 문구 등 신뢰 장치가 더 명확해야 한다.

## 3. Goals

### Product Goals

- 운영자가 신규 상담 리드를 2클릭 이내에 병원 온보딩으로 전환할 수 있게 한다.
- 신규 병원 생성 후 "다음에 무엇을 해야 하는지"가 온보딩 허브에서 즉시 보이게 한다.
- 온보딩 완료 판단을 실제 AI 노출 운영에 필요한 품질 기준과 맞춘다.
- 자료 인입과 근거 처리 과정을 반복 입력 없이 진행하게 한다.
- 비동기 작업 실패와 운영 알림 실패를 화면에서 추적 가능하게 한다.
- 의사 고객에게 진행 상태와 공개 결과물을 설명할 수 있는 공유 가능한 상태 뷰를 제공한다.
- 환자 public experience에서 신뢰, 공식 정보, CTA, 외부 링크 안정성을 강화한다.

### Non-Goals

- AI 노출 보장 문구를 추가하지 않는다.
- 병원 홈페이지 제작 서비스처럼 보이도록 public site를 전환하지 않는다.
- 의료 주장 자동 생성을 human approval 없이 public에 발행하지 않는다.
- 신규 CMS나 별도 운영 도구를 도입하지 않는다.
- 이번 범위에서 전체 디자인 시스템을 전면 교체하지 않는다.

## 4. Target Users

| 사용자 | 핵심 니즈 |
| --- | --- |
| AE/CS 운영자 | 리드 확인, 병원 전환, 진행 상태 설명, 원장 커뮤니케이션 |
| 콘텐츠 운영자 | 자료 인입, 근거 처리, 운영 기준 검토, 콘텐츠 시작 |
| 운영 관리자 | 병목/실패/알림 누락 확인, SLA 관리 |
| 의사 고객 | 내 병원의 온보딩 진행률, 공개 미리보기, AI 질문 대응 상태 확인 |
| 환자 | 병원 정보, 의료진, 진료 영역, 콘텐츠 신뢰도, 방문/상담 경로 확인 |

## 5. Current Baseline

QA에서 확인된 현재 동작:

- Public site 주요 라우트는 정상 렌더링된다.
- 무료 진단 리드 폼 제출은 성공하고 admin 상담 리드 목록에 즉시 반영된다.
- Admin 로그인, 병원 목록, 대시보드, 환자 질문, 콘텐츠, 운영 기준, 노출 보완 작업, 스케줄, 상담 리드 화면은 접근 가능하다.
- 온보딩 허브는 5단계 구조를 제공한다.
- 신규 병원 등록은 병원명과 월간 운영량만 받고 프로파일 화면으로 이동한다.
- 운영 기준에서 본문/URL 없는 자료 처리 시 validation error가 표시된다.
- 리드 생성 후 Slack 알림 경로에서 webhook URL 형식 오류가 로그에 남을 수 있다.
- 데모 병원 공식 홈페이지 외부 링크가 DNS 실패 링크로 열릴 수 있다.

## 6. Target Operating Flow

### 6.1 Lead Intake to Onboarding

1. 환자/의사 고객이 public lead form을 제출한다.
2. Admin 상담 리드 목록에 새 리드가 생성된다.
3. 운영자는 리드 행에서 `병원 온보딩 시작`을 클릭한다.
4. 시스템은 중복 병원 후보를 검색해 보여준다.
5. 운영자는 기존 병원에 연결하거나 신규 병원을 생성한다.
6. 리드의 병원명, 지역/진료과, 연락처, 문의 내용은 신규 병원 프로파일 초안과 onboarding note로 복사된다.
7. 생성 완료 후 운영자는 온보딩 허브로 이동한다.

### 6.2 Onboarding Hub

온보딩 허브는 병원별 초기 운영의 command center가 된다.

기본 단계:

1. 프로파일/엔티티 사실 입력
2. 공식 채널 및 자료 후보 확인
3. 자료 인입 및 공개 자산 업로드
4. 근거 처리 및 evidence note 생성
5. 운영 기준 초안 생성/검토/승인
6. 콘텐츠 스케줄 및 초기 콘텐츠 운영 시작
7. 고객 공유 미리보기 확인

각 단계는 아래 정보를 제공해야 한다.

- 현재 상태: 완료, 진행 필요, 차단됨, 실패, 대기
- 완료 기준: 필수 조건과 권장 조건 분리
- 다음 액션: 가장 먼저 눌러야 할 버튼 1개
- 실패 사유: 운영자가 이해할 수 있는 메시지
- 재시도/수정 경로
- 마지막 갱신 시각과 담당자

### 6.3 Profile to Source Reuse

프로파일에서 입력한 URL은 온보딩 자료 인입 후보가 된다.

후보 예시:

- 홈페이지 URL → 병원 홈페이지 자료
- 블로그 URL → 공식 블로그 자료
- 네이버 플레이스 URL → 네이버 플레이스 자료
- 구글 지도/GBP URL → 구글 비즈니스 자료
- 카카오 채널 URL → 상담 채널 자료

운영자는 각 후보에서 `자료로 추가`, `제외`, `나중에`를 선택할 수 있다. 이미 추가된 URL은 중복으로 생성되지 않아야 한다.

## 7. Functional Requirements

### 7.1 Lead Conversion

- 상담 리드 행에 `온보딩 시작` CTA를 추가한다.
- 리드 상세 또는 inline drawer에서 다음 정보를 확인할 수 있어야 한다.
  - 병원명
  - 진료과/지역
  - 연락처
  - 문의 내용
  - 유입 경로
  - 접수 시각
  - 개인정보 동의 버전
- CTA 클릭 시 중복 병원 후보를 보여준다.
- 신규 생성 시 리드 정보를 병원 draft profile에 매핑한다.
- 생성된 병원에는 원본 lead id가 연결되어야 한다.
- 연결 후 리드 목록에는 `온보딩 전환됨` 상태와 연결 병원 링크를 보여준다.

Acceptance Criteria:

- 신규 리드에서 병원 온보딩 허브까지 2클릭 이내로 도달한다.
- 같은 병원명/연락처로 이미 병원이 있으면 중복 후보가 표시된다.
- 전환된 리드는 admin leads 화면에서 연결 병원으로 이동할 수 있다.

### 7.2 New Hospital Entry

- `신규 병원 등록`은 독립 폼으로 유지하되, 생성 후 기본 이동지는 `/hospitals/{id}/onboarding`이어야 한다.
- 등록 성공 화면 또는 toast에서 "다음: 프로파일 입력"을 명확히 안내한다.
- 병원 생성 시 source note 또는 onboarding note에 생성 경로를 남긴다.
- 월간 운영량은 콘텐츠 스케줄 기본값으로 연결되어야 한다.

Acceptance Criteria:

- 병원명만 입력해 생성해도 온보딩 허브에서 현재 부족한 항목이 보인다.
- 생성 직후 프로파일 미완료, 자료 미인입, 운영 기준 미승인 상태가 명확히 표시된다.

### 7.3 Onboarding Completion Rules

온보딩 단계 완료 기준을 아래처럼 강화한다.

프로파일 필수:

- 원장명
- 원장 약력
- 진료 철학
- 주소
- 전화번호
- 진료시간 1개 이상
- 홈페이지 또는 블로그 URL
- 네이버 플레이스 URL
- 구글 지도 또는 GBP URL
- 위도/경도
- 지역 태그 1개 이상
- 전문과목 1개 이상
- 키워드 1개 이상
- 진료 항목 1개 이상

자료 인입 필수:

- 공식 채널 source 1개 이상
- 의료진/병원 소개 source 1개 이상
- public 노출 가능 사진 1개 이상 권장

근거 처리 필수:

- 처리 가능한 text source가 1개 이상 processed
- 처리 실패 source가 있으면 실패 사유 확인 또는 제외 처리

운영 기준 필수:

- draft 생성
- evidence 기반 검토
- approved philosophy 존재

콘텐츠 시작 필수:

- 스케줄 설정
- query target 또는 환자 질문 1개 이상
- 첫 content brief 또는 content item 생성

Acceptance Criteria:

- 각 단계는 완료 조건과 부족 조건을 같은 카드 안에서 보여준다.
- 일부 자료만 추가된 상태를 무조건 완료로 보지 않는다.
- 처리 불가능한 자료는 완료율 계산에서 명확히 제외 또는 차단 사유로 표시한다.

### 7.4 Source Intake UX

- 프로파일 URL에서 자료 후보를 자동 생성한다.
- URL 후보 카드에는 source type, 원본 필드, URL, 중복 여부, 최근 크롤 상태를 보여준다.
- `모든 공식 URL 자료로 추가` 버튼을 제공한다.
- URL 자동 크롤 실패 시 실패 원인과 수동 자료 입력 대안을 제공한다.
- 파일 업로드는 사진/문서 목적을 더 명확히 분리한다.
- 자료 목록에서 처리 불가 자료는 `근거 추출` 버튼을 숨기거나 disabled 처리한다.

Acceptance Criteria:

- 프로파일에 홈페이지/블로그/지도 URL이 있으면 온보딩 자료 인입 화면에 후보로 표시된다.
- 동일 URL은 중복 source로 생성되지 않는다.
- raw_text가 없는 URL 직접 자료는 처리 버튼이 disabled되고 이유가 보인다.

### 7.5 Processing and Job Timeline

- 온보딩 허브에 작업 타임라인을 추가한다.
- 추적 대상:
  - profile saved
  - v0 report generation
  - source crawl/upload
  - source process
  - philosophy draft generation
  - philosophy approval
  - content schedule setup
  - first content generation
  - public site build/revalidate
  - Slack/webhook/customer notification
- 각 작업은 status, started_at, finished_at, error_message, retry action을 가진다.
- Slack/webhook 알림 실패는 backend 로그에만 남지 않고 admin UI에 표시되어야 한다.

Acceptance Criteria:

- 리드 제출 후 운영 알림 실패가 발생하면 admin에 `알림 실패` 상태가 보인다.
- 실패한 작업은 재시도 가능하거나 설정 수정 경로를 제공한다.
- 온보딩 허브에서 "현재 막힌 이유"를 한 문장으로 확인할 수 있다.

### 7.6 Customer Preview and Handoff

- 의사 고객에게 공유 가능한 preview/status view를 제공한다.
- 최소 정보:
  - 병원 프로파일 준비 상태
  - 수집된 공식 자료 목록
  - 승인된 콘텐츠 운영 기준 요약
  - 첫 콘텐츠/스케줄 상태
  - public webblog preview URL
  - 다음 운영 예정 작업
- 내부 운영 메모와 raw evidence는 고객 공유 화면에 노출하지 않는다.
- 공개 전 preview는 인증된 token URL 또는 admin-only preview로 제공한다.

Acceptance Criteria:

- 운영자는 온보딩 허브에서 고객 공유 링크를 생성하거나 복사할 수 있다.
- 고객 공유 화면은 내부 실패 로그나 민감한 운영 메모를 노출하지 않는다.
- approved 상태가 아닌 의료 주장/콘텐츠는 public-ready로 표시되지 않는다.

### 7.7 Public Patient Trust Improvements

- public site 외부 링크 health check 결과를 기반으로 깨진 링크를 숨기거나 warning 처리한다.
- 환자 CTA를 전화, 지도, 상담/예약, 공식 채널로 분리한다.
- 콘텐츠 상세에 신뢰 메타데이터를 추가한다.
  - 의료진/운영 검토 상태
  - 최종 수정일
  - 이 글이 답하는 환자 질문
  - 관련 진료 항목
  - 의료 정보 주의 문구
- 병원 facts, schema, llms.txt, sitemap의 값 불일치를 감지한다.

Acceptance Criteria:

- DNS 실패 또는 invalid URL은 public CTA로 노출되지 않는다.
- 콘텐츠 상세에서 검토/수정/관련 질문 정보가 SSR HTML로 표시된다.
- 전화 CTA 외에 지도/상담 CTA가 병원 데이터 존재 여부에 따라 표시된다.

## 8. Data Requirements

신규 또는 확장 모델 후보:

### Sales Lead

- `status`: NEW, CONTACTED, CONVERTED, DISMISSED
- `converted_hospital_id`
- `converted_at`
- `conversion_note`

### Hospital Onboarding

- `onboarding_status`: NOT_STARTED, IN_PROGRESS, BLOCKED, READY_FOR_CUSTOMER_REVIEW, LIVE
- `onboarding_source`: MANUAL, LEAD_CONVERSION, IMPORT
- `source_lead_id`
- `current_step`
- `blocked_reason`

### Onboarding Task / Job Timeline

- `hospital_id`
- `task_type`
- `status`
- `title`
- `started_at`
- `finished_at`
- `error_message`
- `retry_payload`
- `actor`

### Source Candidate

- `hospital_id`
- `source_field`
- `source_type`
- `url`
- `status`: CANDIDATE, ADDED, EXCLUDED, FAILED
- `linked_source_asset_id`

### Link Health

- `hospital_id`
- `url`
- `source_field`
- `status_code`
- `dns_ok`
- `last_checked_at`
- `last_error`
- `public_visible`

## 9. UX Requirements

### Admin IA Changes

Recommended hospital detail tabs:

1. 대시보드
2. 온보딩
3. 환자 질문
4. 노출 보완 작업
5. 콘텐츠
6. 스케줄
7. 운영 기준
8. Wiki
9. 프로파일
10. 리포트

Rationale:

- 초기 운영에서는 온보딩이 가장 중요하다.
- 운영 기준/Wiki/프로파일은 필요하지만, 신규 운영자의 첫 판단 화면은 아니다.
- 환자 질문과 노출 보완 작업은 콘텐츠 운영의 앞단에 있어야 한다.

### Onboarding Hub Layout

- 상단: 병원명, 플랜, 전체 진행률, 현재 차단 사유, 다음 추천 액션
- 좌측: 단계 progress nav
- 중앙: 현재/전체 단계 카드
- 우측 또는 하단: 작업 타임라인, 알림 상태, 고객 공유 상태

### Copy Guidelines

권장:

- "다음 작업"
- "완료 기준"
- "차단 사유"
- "고객에게 공유 가능"
- "공식 URL에서 자료 후보를 찾았습니다"
- "이 자료는 본문이 없어 근거 추출할 수 없습니다"

피할 표현:

- "AI 노출 보장"
- "자동으로 다 됩니다"
- "홈페이지 제작 완료"
- "상위 노출 준비 완료"

## 10. Metrics

운영 효율:

- lead to hospital conversion rate
- lead submission to onboarding start time
- hospital creation to profile completion time
- profile completion to approved philosophy time
- approved philosophy to first content generated time
- onboarding blocked hospital count
- average failed job recovery time
- duplicate source creation rate

품질:

- hospitals with broken public links
- hospitals with approved philosophy
- sources with process errors unresolved
- content items linked to query targets
- customer preview generated rate

고객 경험:

- public CTA click-through by type
- doctor preview visits
- patient visit page CTA clicks
- content trust block visibility rate

## 11. Release Plan

### Phase 1: Flow Repair

- Lead row `온보딩 시작` CTA
- Sales lead conversion state
- New hospital creation redirects to onboarding hub
- Onboarding hub next action header
- Processing button disabled reasons
- Slack/webhook failure surfaced in admin

### Phase 2: Source Automation

- Profile URL source candidates
- Bulk add source candidates
- Duplicate URL prevention
- Source processing readiness rules
- Link health check for public CTA URLs

### Phase 3: Customer Handoff

- Doctor/customer preview status page
- Public trust metadata on content detail
- CTA segmentation for phone/map/channel
- Public broken-link hiding

### Phase 4: Operations Maturity

- Job timeline with retries
- SLA/bottleneck dashboard
- Onboarding task ownership
- Customer-facing monthly handoff alignment

## 12. Risks and Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| 완료 기준이 너무 엄격해져 온보딩이 막힘 | 운영 속도 저하 | 필수/권장 조건 분리, override with reason 제공 |
| 리드 전환이 중복 병원을 많이 만듦 | 데이터 오염 | 중복 후보 검색, 전환 전 확인, merge path 제공 |
| 고객 preview에 내부 정보가 노출됨 | 신뢰/보안 리스크 | allowlist 기반 공유 데이터 구성 |
| link health check가 느리거나 외부 사이트에 의존 | public UI 불안정 | 비동기 주기 체크, 마지막 정상 상태 캐시 |
| Slack 알림 실패를 과도하게 노출 | 운영자 피로 | 온보딩 관련 critical 알림만 표시, 설정 페이지로 연결 |
| 자료 자동 크롤이 실패함 | 온보딩 지연 | 수동 입력/업로드 fallback 제공 |

## 13. Open Questions

- 리드 전환 시 병원명 유사도 기준은 무엇으로 할 것인가?
- 고객 preview는 로그인 기반인가, 만료 token URL 기반인가?
- Slack/webhook 실패는 hospital onboarding task로 저장할 것인가, notification log로 별도 관리할 것인가?
- public broken-link hiding은 즉시 숨김인가, warning 후 운영자 승인 숨김인가?
- 온보딩 override 권한은 누구에게 줄 것인가?
- 첫 콘텐츠 생성 조건은 approved philosophy만으로 충분한가, query target도 필수로 할 것인가?

## 14. Definition of Done

- 상담 리드에서 신규 병원 온보딩 시작까지 운영자가 다시 입력해야 하는 정보가 최소화된다.
- 신규 병원 생성 후 운영자는 온보딩 허브에서 다음 작업을 즉시 이해한다.
- 각 온보딩 단계는 완료/차단/실패/재시도 상태를 명확히 표시한다.
- 프로파일에 입력된 공식 URL은 자료 후보로 자동 제안된다.
- 처리 불가능한 자료는 실패를 눌러본 뒤 알게 되는 구조가 아니다.
- 리드 제출, 운영 알림, 자료 처리, 콘텐츠 시작의 주요 비동기 작업이 추적 가능하다.
- 의사 고객에게 공유 가능한 진행 상태와 public preview가 제공된다.
- 환자 public site에는 깨진 외부 링크가 노출되지 않고, 주요 CTA와 콘텐츠 신뢰 정보가 명확히 표시된다.
