# Re:putation — Claude Code Project Guide

> **이 파일을 먼저 읽어라.** 모든 개발 결정의 기준이 된다.

---

## 제품 개요

**Re:putation**은 병원이 ChatGPT·Gemini 답변에서 더 잘 이해되고 언급되도록 돕는 AI 노출 컨설팅·콘텐츠 운영 서비스다.
운영사: **MotionLabs Inc.**

핵심 가치: 병원이 이미 보유한 정보를 AI가 읽기 쉬운 구조로 정리하고,
근거 기반 콘텐츠를 꾸준히 발행해 ChatGPT·Gemini 답변 안에서 병원이 언급될 가능성을 높인다.

---

## 전체 플로우 (완전 숙지 필수)

```
[STEP 1] 계약 체결
    ↓
[STEP 2] 병원 프로파일 입력 (사람 — Admin에서 AE가 직접)
    입력 항목: 원장명, 원장 약력, 진료 철학, 진료 항목,
              병원 주소, 전화번호, 진료시간, 홈페이지 URL, 블로그 URL
              지역, 진료과목, 핵심 키워드, 경쟁 병원명, 요금제
    ↓
[STEP 3] V0 리포트 자동 생성 (시스템 — 프로파일 저장 즉시 트리거)
    • 현재 AI 답변 안에서 병원이 얼마나 언급되는지 즉시 분석
    • PDF 리포트 자동 생성
    • Slack → AE: V0 리포트 완료 (원장 보고 전 확인 요망)
    • AE가 직접 원장에게 보고 (시스템이 보내지 않음)
    ↓
[STEP 4] AI 노출 콘텐츠 허브 노출 준비 (시스템 — 프로파일 기반)
    • Next.js /site 공개 표면이 승인된 병원 정보와 콘텐츠를 읽어 노출할 수 있게 상태를 준비
    • Schema.org MedicalClinic 마크업, FAQ/진료 안내, 콘텐츠 목록은 /site가 동적으로 제공
    • 별도 홈페이지/HTML 납품물이 아니라 AI와 검색엔진이 참고할 병원 정보·콘텐츠 허브 운영 상태를 만든다
    • 준비 완료 시 STEP 5 활성화 게이트를 즉시 평가한다 (아래 참조)
    • 자동 활성화가 불가능한 경우에만 Slack → AE: "콘텐츠 허브 노출 준비 완료 — Admin에서 공개 정보와 도메인 상태 확인 필요"
    ↓
[STEP 5] 공개 노출 상태 전환 (시스템 기본, 사람은 자기 도메인만)
    • 활성화 게이트: profile_complete·v0_report_done·site_built 세 가지 — 모두 시스템 플래그
    • 자기 도메인이 없는 병원은 STEP 4 허브 준비가 끝나는 즉시 게이트를 평가해
      기본 플랫폼 주소로 자동 ACTIVE 전환(site_live=true) — AE 클릭 없음
      Slack → AE: "운영 시작됨 — 기본 주소 {url}" 1건 (SITE_BUILT 재촉 알림을 대체)
    • 자기 도메인(aeo_domain)이 지정된 병원만 AE가 Admin에서 도메인을 입력하고
      DNS/TLS 확인 후 직접 운영을 시작한다 (DNS는 병원 소유라 시점을 시스템이 정할 수 없다)
      이때만 SITE_BUILT 알림이 남고, 자동 시작이 불가능했던 사유를 함께 알린다
    • PAUSED·ACTIVE 등 자동 전환 대상이 아닌 상태는 어떤 재실행에도 자동으로 되살리지 않는다
      (`services/hospital_activation.py:AUTO_ACTIVATABLE_STATUSES`)
    • 기본 플랫폼 주소는 DNS 없이 활성화하며, 자기 도메인의 DNS/TLS 확인 결과는 별도 소프트 상태로 표시한다
    • schedule_set·자기 도메인 DNS/TLS는 ACTIVE 전환의 선행조건이 아니다
    • 콘텐츠 스케줄은 STEP 6이므로 공개 활성화 선행조건이 아니다
    ↓
[STEP 6] 콘텐츠 운영 기준 자동 준비 및 스케줄 설정 (시스템 기본, 사람은 예외만)
    • 콘텐츠 준비 품질 게이트:
      공식 자료 수집 → 모든 텍스트 자료 처리 → 현재 자료 snapshot과 일치하는 콘텐츠 운영 기준 생성
      → essence_auto_review 시스템 검수자가 안전 규칙과 근거를 통과한 기준을 APPROVED로 자동 승인
    • 자동 검수가 보류한 예외만 AE가 근거를 확인해 수정·재검토하거나 기존 override 경로로 처리
    • 요금제 선택: 스타터 12편/월 60만원, 그로워 16편/월 90만원,
      리더 20편/월 120만원 (모두 부가세 별도)
    • 발행 요일 설정 (예: 화·목 or 월·수·금 등)
    • 저장 시 첫 달 콘텐츠 캘린더 자동 생성 — 슬롯 유형은 요금제 배분표를 상한으로 하되,
      측정된 미언급 격차(`MISSING_MENTION`)가 FAQ·LOCAL·DISEASE·TREATMENT 슬롯의 최대 절반까지
      재배정한다 (`services/gap_driven_slots.py`, 콘텐츠 유형표 참고)
    • 자료가 추가·수정되면 최신 snapshot과의 일치("current")가 깨져 새 승인이 필요해진다.
      다만 생성은 새 승인이 나올 때까지 **마지막으로 승인된 스냅샷("approved")을 그대로 계속 쓰고**,
      **발행만 현재("current") 승인본을 강제**한다 — 생성이 완전히 멈추지 않으면서도
      공개 표면에는 항상 최신 승인 기준만 나가게 하는 절충이다 (`services/essence_readiness.py`,
      `workers/tasks.py:_generation_philosophy_sync` vs `get_current_approved_philosophy_sync`)
    ↓
[STEP 7] 콘텐츠 자동 생성 사이클 (시스템 — 이후 지속)
    발행일 전날 밤 23:00 (nightly_content_generation)
        → 병원별 최신 승인 콘텐츠 운영 기준(STEP6, "approved" 스냅샷) 확인
        → Claude Sonnet(`CLAUDE_MODEL`)으로 본문 자동 생성 — 프롬프트 캐싱 구조는
          "콘텐츠 생성 프롬프트 설계" 절 참고
        → 설정된 이미지 공급자(`IMAGE_PROVIDER`)로 대표 이미지 자동 생성
        → DB에 초안 저장 (status: DRAFT)
        → 01·04·07시 overnight 복구, 07:45 prepublish 복구가 승인 대기·비용 차단·공급자 일시
          오류로 남은 항목을 발행 전까지 재수거한다
    발행일 당일 아침 08:00 (morning_content_auto_publish)
        → 현재("current") 승인 운영 기준·참고 자료·의료광고 금지 표현 자동 안전검사
        → 통과 시 AI가 참고할 콘텐츠 허브에 자동 게재 (status: PUBLISHED) — 성공은
          병원별 건별 Slack 없이 조용히 진행된다
        → 소진된(자동 복구를 넘긴) 차단만 실행 배치당 요약 최대 1건으로 Slack
          (콘텐츠 건별 알림 없음 — `docs/ops/slack-notification-policy.md`)
        → AE가 공개 글/Admin에서 소규모 사후검수 표본(발행 순번 1번 + 발행 후 본문을 수정한
          건만, `services/post_publish_review_policy.py`)을 확인, 문제 발견 시 즉시 수정 또는
          비공개 후 재생성 — 이 표본은 관찰용이며 월간 리포트 전달을 막지 않는다
    ↓
[STEP 8] 월간 AI 답변 언급 리포트 (시스템 — 매월 24일~말일 측정 완주, 1~7일 마감·자동 복구)
    • 측정: 매월 24일부터 말일까지 6시간마다(`monthly-sov-measurement`) 전환 코호트의
      고정 tracking set(N=15, 주 5회 반복)을 완주한다. 같은 기간 주간 측정(월 02:00)은
      이 코호트만 제외하고 나머지 ACTIVE 병원은 그대로 측정한다(전면 스킵 아님)
    • 마감: 매월 1~7일 6시간마다(`monthly-reports`) 직전 달 자료가 모두 들어온 병원부터
      순차로 리포트를 마감 — 실패분은 이 창 안에서 자동 재시도
    • 헤드라인: 셀(질문×AI 서비스)당 1회 대표값이 아니라 **반복 측정 빈도**(k/5)를 쓰고,
      Wilson 구간으로 "의미 있는 변화"와 "표본 노이즈"를 코드가 판정한다. 헤드라인과
      전월 대비 델타는 항상 같은 매칭 셀 집합을 쓴다 (`services/sov_statistics.py`)
    • 인용 귀속: AI 답변이 실제로 인용한 URL을 병원의 공개 표면(플랫폼 경로·서브도메인·
      자기 도메인 전부)에 매칭해 "AI가 인용한 우리 글" 건수를 글 단위 1급 사실로 리포트에
      싣는다 (`services/content_citations.py`, `services/report_attribution.py`)
    • 게이트: "현재 승인된 운영 기준 없음"·"미처리 자료 존재"만 전달을 막는 진짜 blocker다.
      사후검수 표본 미완료, 운영 기준 버전 갱신(essence version drift)은 경고(warning)로만
      표시되고 원장 전달을 막지 않는다 (`api/admin/reports.py`)
    • 원장용 PDF: 1쪽은 3막(이번 달 한 일 → 무엇이 달라졌나(새로 나온·빠진 질문, V0 대비) →
      다음 달 계획), 질문별 표는 조건부 2쪽 부록. 검증은 1쪽 또는 부록 포함 2쪽만 허용
      (`services/report_artifact_validation.py`). AE용 내부 PDF에는 "원장 미팅 토킹 포인트"가 실린다
    • Slack → AE: 월간 리포트 완료 (헤드라인 "언급 N번(전월 대비 ±Δ, 유의성)" 한 줄 포함)
    • AE가 원장에게 보고 (시스템이 보내지 않음)

### 보조 배치 (전체 목록은 `backend/app/core/celery_app.py` 참고)

| 시각 | 태스크 | 역할 |
|---|---|---|
| 매일 22:30 | stranded-content-recovery | 오래된 미생성 슬롯을 병원별 하루 한 편으로 재배치 |
| 매일 01·04·07시 | overnight-content-generation-recovery | 밤사이 승인·비용 차단·공급자 오류 회수 |
| 매일 07:45 | prepublish-content-generation-recovery | 발행 직전 마지막 회수 |
| 매주 월 02:00 | weekly-sov-monitoring | 전체 ACTIVE 병원 측정 (월말 창엔 월간 코호트만 제외) |
| 매월 24~31일 */6h | monthly-sov-measurement | 전환 코호트 월말 측정 완주 |
| 매월 25~31일 */6h | monthly-slot-generation | 다음 달 콘텐츠 슬롯 자동 생성(격차 재배정 포함) |
| 매월 1~7일 */6h | monthly-reports | 월간 리포트 마감 |
| 매주 화 03:00 | weekly-naver-source-sync | 병원 네이버 블로그 신규 글 자산 인입 |
| 매일 04:00 | purge-expired-leads | 보관기간 만료 리드 파기 |
| 1분마다 (4종) | drain-lead-diagnoses / dispatch-notification-outbox / reconcile-monthly-artifact-incidents / reconcile-autonomous-workflows | 무료 진단 폴러, Slack outbox 발송, 산출물·워크플로 유실 복구 |
| 5분마다 (6종) | canary-default/content/sov/reports/leadgen/certificates | 경로별 헬스 체크 |
```

---

## 콘텐츠 유형 정의 (7가지)

| 코드 | 유형 | 설명 | AI 답변 노출 도움 | FAQ인지 |
|------|------|------|----------|---------|
| FAQ | FAQ | 환자 질문 형태 Q&A | ★★★ | ✅ |
| DISEASE | 질환 가이드 | 원인·증상·진단·치료 심층 | ★★★ | ❌ |
| TREATMENT | 시술·치료 안내 | 과정·회복·주의사항 | ★★★ | ❌ |
| COLUMN | 원장 칼럼 | 원장명+전문성 co-occurrence | ★★ | ❌ |
| HEALTH | 건강 정보 | 계절·생활습관 예방 | ★★ | ❌ |
| LOCAL | 지역 특화 | "[지역] [질환]" 로컬 타겟 | ★★★ | ❌ |
| NOTICE | 병원 공지 | 장비·진료시간·이벤트 | ★ | ❌ |

### 요금제별 월간 편수 배분

| 유형 | 리더 20편/월<br>120만원 | 그로워 16편/월<br>90만원 | 스타터 12편/월<br>60만원 |
|------|:------------------------:|:------------------------:|:-------------------------:|
| FAQ | 5 | 4 | 3 |
| DISEASE | 4 | 3 | 3 |
| TREATMENT | 4 | 3 | 2 |
| COLUMN | 2 | 2 | 2 |
| HEALTH | 2 | 2 | 1 |
| LOCAL | 2 | 1 | 1 |
| NOTICE | 1 | 1 | 0 |

> 모든 가격은 부가세 별도다. 이 표는 요금제별 **월간 편수 상한**(`models/content.py:PLAN_DISTRIBUTION`)이다.
> 실제 슬롯 생성 시 FAQ·LOCAL·DISEASE·TREATMENT 네 유형은 측정된 미언급 격차에 따라
> 유형 배분의 최대 절반까지 서로 재배정될 수 있다. 월 총 편수와 COLUMN·HEALTH·NOTICE는
> 절대 바뀌지 않는다 (`services/gap_driven_slots.py`).

---

## 기술 스택

### Backend
- **FastAPI** (Python 3.11) — Admin API + Public API, **slowapi**로 레이트리밋
- **Celery** + **Redis** + **celery-redbeat** — 비동기 태스크/스케줄러. RedBeat이 Redis 분산 락으로 단일 dispatcher를 보장
- **PostgreSQL** — 메인 DB
- **SQLAlchemy** (async) + **Alembic** — ORM / 마이그레이션
- **Anthropic SDK** — 콘텐츠 생성은 `CLAUDE_MODEL`(기본 `claude-sonnet-4-5`), 빠른 작업은 `CLAUDE_MODEL_FAST`(Haiku), 프로파일 자동완성은 `AUTOFILL_MODEL`(비우면 FAST 사용)
- **이미지 생성** — `IMAGE_PROVIDER`가 `google`(기본, `google-genai` SDK, `GOOGLE_IMAGE_MODEL=gemini-3.1-flash-image`)이거나 `openai`(`gpt-image-2`, 실패 시 google로 폴백)
- **OpenAI SDK** — SoV 측정. `OPENAI_MODEL_QUERY`(답변 모델=측정 대상)·`OPENAI_MODEL_PARSE`(판정 모델)는 둘 다 settings로 관리하며, `scripts/deploy.sh`가 배포 시 부동 계열명(`gpt-4o`, `gpt-5` 등)을 거부하므로 날짜 스냅샷·변종 고정명만 쓴다
- **google-genai SDK** — Gemini 측정(`GEMINI_MODEL`)
- **WeasyPrint** + **Jinja2** — PDF 리포트
- **Sentry** (`sentry-sdk[fastapi,celery]`) — 오류 추적

### Frontend
- **Admin** (`/admin`): Next.js 16 App Router, React 19 — AE 운영 도구
- **Site** (`/site`): Next.js 16 App Router(SSG/ISR), React 18 — 병원별 정보·콘텐츠 허브 공개 표면 (미들웨어는 Next 16 규약대로 `proxy.ts`)

### Infrastructure
- **Docker Compose** — 로컬 개발
- **GCP Cloud Run** — API + Worker + Admin + Site(Next.js standalone 컨테이너), LB 호스트 라우팅
- **GCP Cloud Storage** — 생성 이미지·리포트 저장
- **GCP Certificate Manager** — 병원 자기 도메인 인증서 자동 프로비저닝(`CERTIFICATE_MANAGER_AUTO_PROVISION`)
- **Resend** — 리드 알림·전달 이메일 발송(`RESEND_API_KEY`, `services/mailer.py`)
- **IndexNow** — 콘텐츠 발행 직후 검색엔진에 URL 제출(`INDEXNOW_ENABLED`/`INDEXNOW_KEY`, `services/indexnow.py`)

---

## 프로젝트 구조

```
reputation/
├── CLAUDE.md
├── docker-compose.yml
├── .env.example / .env.production.example
├── scripts/deploy.sh              ← 배포(모델 고정명 검증 등 프로덕션 게이트 포함)
│
├── backend/app/
│   ├── main.py
│   ├── core/
│   │   ├── config.py              ← 환경변수 전부(~95개) + 프로덕션 fail-fast 검증
│   │   ├── database.py            ← async DB 세션
│   │   └── celery_app.py          ← Celery + RedBeat + task_routes + beat_schedule 전체
│   ├── models/ (13개)             ← hospital·content·sov·report·essence·handoff·lead·
│   │                                 lead_diagnosis·monthly_control·operations·admin_user·audit·usage
│   ├── schemas/                   ← Pydantic 스키마 (일부는 api/ 내 인라인 BaseModel과 병존)
│   ├── api/
│   │   ├── admin/ (34파일)        ← hospitals·content(스케줄 설정 포함, 별도 schedule.py 없음)·
│   │   │                             essence·query_targets·exposure_actions·operations_center*·
│   │   │                             reports·sov·domain*·leads·handoffs·accounts·auth
│   │   └── public/                ← site.py(병원 데이터·JSON-LD·FAQ), diagnosis.py(무료 진단),
│   │                                 leads.py, assets.py
│   ├── services/ (110여 개, 도메인별) ← content_engine·image_engine·image_direction·sov_engine·
│   │   │                             sov_statistics·report_engine·report_attribution·
│   │   │                             content_citations·gap_driven_slots·hospital_activation·
│   │   │                             essence_engine·essence_auto_review·essence_readiness·
│   │   │                             notifier·notification_messages·notification_delivery·
│   │   │                             onboarding_notifications·content_publish_notifications·
│   │   │                             cost_guard
│   │   └── (utils/medical_filter.py — 금지 표현 필터, services/가 아닌 utils/)
│   ├── workers/ (37 모듈)          ← tasks.py(메인) + canary_tasks·autonomous_recovery·
│   │                                 content_backlog_recovery·monthly_slots·
│   │                                 weekly_sov_incident_control·domain_certificate_tasks·
│   │                                 lead_diagnosis_tasks·notification_tasks 등
│   │                                 (인시던트·복구·마일스톤 전담 모듈이 대다수)
│   └── templates/report.html
│
├── admin/app/                     ← Next.js 16 Admin (AE 운영 도구)
│   ├── hospitals/[id]/
│   │   ├── onboarding/ profile/ essence/ wiki/ query-targets/ exposure-actions/
│   │   ├── content/ schedule/ dashboard/ reports/ (+ DomainSetupPanel 등)
│   ├── operations/ leads/ accounts/ login/
│   └── lib/                       ← report-review·hospital-activation·onboarding-lifecycle 등
│
└── site/app/                      ← Next.js 16 정보·콘텐츠 허브 공개 표면
    ├── [slug]/ (doctor·treatments·visit·contents·llms.txt)
    ├── ai-diagnosis/ api/ llms.txt robots.ts sitemap.ts
    └── indexnow-key.txt
```

---

## 핵심 데이터 모델

전체 필드는 `backend/app/models/`가 소스다. 여기서는 자주 참조하는 것만 요약한다.

### Hospital (병원 프로파일)
```
기본: id, name, slug, plan, status, created_at (모델 필드는 60개 이상 — profile/onboarding 확장)

status (HospitalStatus): ONBOARDING → ANALYZING → BUILDING → PENDING_DOMAIN → ACTIVE / PAUSED

상태 플래그 (필드명은 legacy를 그대로 씀 — 코드 전반에서 이 이름을 그대로 참조한다):
  profile_complete: bool  (프로파일 입력 완료)
  v0_report_done: bool    (V0 리포트 생성 완료)
  site_built: bool        (콘텐츠 허브 노출 준비 완료)
  site_live: bool         (공개 도메인/노출 활성화 완료)
  schedule_set: bool      (콘텐츠 스케줄 설정 완료 — 활성화 선행조건 아님)

aeo_domain: 병원 자기 도메인(있으면 STEP5가 수동 경로로 전환)
```

### ContentItem (콘텐츠 아이템)
```
id, hospital_id, schedule_id
content_type: FAQ|DISEASE|TREATMENT|COLUMN|HEALTH|LOCAL|NOTICE
status: DRAFT|READY(레거시, 신규 플로우는 DRAFT→PUBLISHED)|PUBLISHED|REJECTED|CANCELLED

title, body(마크다운), image_url, image_prompt
scheduled_date, generated_at, published_at
published_by (정상 경로 SYSTEM_AUTO_PUBLISH, 운영 복구 시 AE 이름)

주요 확장 필드: content_brief/brief_status/brief_approved_at/by (STEP6 운영 기준 브리프),
faq_question (FAQ·격차 슬롯의 질문 원문), content_focus_topic (병원별 편집 범위 커스터마이즈),
generation_claimed_at (생성 잠금), post_publish_notified_at/reviewed_at/reviewed_by (사후검수 표본)
```

### SovRecord (SoV 측정)
```
id, hospital_id, query_id (레거시 QueryMatrix)
measurement_run_id, ai_query_target_id, ai_query_variant_id (신규 구조화 질의)
ai_platform: chatgpt|gemini
mention_verdict: MATCHED|NOT_MATCHED|AMBIGUOUS (구버전 이진 판정 행은 NULL)
is_mentioned, mention_rank, mention_sentiment, mention_context
source_urls (AI 답변이 인용한 URL 목록 — content_citations.py가 귀속에 사용)
raw_response, competitor_mentions
```

### MonthlyReport (월간 리포트)
```
id, hospital_id, period_year, period_month, report_type (V0|MONTHLY)
manifest_id (측정 manifest), version, supersedes_report_id (버전 계보)
quality, planned_count/success_count/failed_count/excluded_count
pdf_path, doctor_pdf_path, sent_at
```

---

## 콘텐츠 생성 프롬프트 설계

`backend/app/services/content_engine.py`가 Anthropic 프롬프트 캐싱을 쓰는 3블록 구조로
호출한다 (breakpoint 순서를 지켜야 캐시가 산다):

1. **STATIC_SYSTEM_BLOCK** — 모든 병원·모든 아이템에 동일. 작성 규칙 + 금지 표현
   (`utils/medical_filter.FORBIDDEN_EXPRESSIONS`에서 렌더링, 하드코딩 아님) + 출처 화이트리스트.
2. **병원 시스템 블록** — 병원 프로파일 + 최신 승인 콘텐츠 운영 기준. 병원마다 다르지만
   같은 병원의 월간 배치(12~20편) 안에서는 동일해 캐시가 재사용된다.
3. **user 메시지** — 유형 프롬프트·브리프·최근 제목·큐레이션 후보·재작성 지적 등
   아이템마다 바뀌는 값만 여기 몰아둔다.

작성 규칙 요지:
1. 첫 문단에서 환자 질문에 대한 핵심 답변을 먼저 제시(BLUF)
2. 환자의 실제 언어로 작성 (의학 용어 최소화)
3. 지역명·병원명·원장명을 자연스럽게 포함, 격차 슬롯은 target 질의의 임상 키워드를
   제목·첫 H2·faq_question에 포함
4. 의료광고법 준수 — 금지 표현은 `utils/medical_filter.py`가 유일한 소스
5. 분량: **1800~5200자**(1800자 미만은 저장 안 함, 5200자 초과 금지), 목표는 2200~4200자.
   H2 4~6개, listicle/표 1개 이상 포함. `max_tokens=5500`
6. 마크다운 형식

생성 후 금지 표현 자동 검사에 걸리면 최대 `AUTO_REMEDIATION_MAX_GENERATIONS`(2)회까지
재생성한다.

대표 이미지는 `services/image_direction.py`(병원 정체성 컨텍스트 정제) +
`services/image_engine.py`(`IMAGE_PROVIDER` 분기, 실패 시 폴백)가 담당한다. 콘텐츠 유형별
고정 프롬프트 문자열 목록은 코드에 없다 — 매번 병원 정체성 기반으로 합성한다.

---

## Slack 알림 메시지 규격

전체 정책과 정확한 문구는 **`docs/ops/slack-notification-policy.md`가 유일한 소스**다.
요지만 요약한다.

- **즉시 알림**: 무료 진단/일반 문의 접수, 진단·PDF·이메일 최종 실패, Celery 최종 실패·보안/비용
  가드, V0 리포트 완료
- **운영 요약** (배치당 최대 1건류): 23:00 야간 생성, 08:00 발행(성공/차단/생성 누락 각 최대
  1건, 콘텐츠 건별 알림 없음), 주간 네이버 자산 수집, 주간 측정 시작, 15분 온보딩·월간 마일스톤
- **로그 전용**: 자동 재시도가 담당자 배정·기한 지정 없이 30분 안에 스스로 복구한 인시던트는
  Slack을 보내지 않는다 (DB 인시던트·감사 로그만 남김). 이미 사람에게 도달한 건만 정보 전달용
  복구 메시지를 보낸다
- **인프라 인시던트** (`BACKGROUND_TASK`/`BROKER`/`UNSAFE_DISPATCH`/`NOTIFICATION_DELIVERY`/
  `DOCTOR_PDF_BLOCKED`/`CACHE_REVALIDATION` 등)는 AE 채널이 아니라 `SLACK_WEBHOOK_URL_DEV`
  개발 채널로 분리

구현은 `services/onboarding_notifications.py`(온보딩·월간 마일스톤 durable intent),
`services/notification_messages.py`(Block Kit 렌더링), `services/content_publish_notifications.py`
(발행 알림), `services/notifier.py`(레거시 `notify_*` — 대부분 호출부 없음, 살아있는 함수는
리드 관련 3개뿐) 순으로 나뉜다.

---

## 의료광고 금지 표현 필터 (모든 콘텐츠 생성 시 필수 적용)

목록·정규식·정규화 규칙은 **`backend/app/utils/medical_filter.py`가 유일한 소스**다
(여기에 다시 나열하지 않는다 — 두 곳에 있으면 반드시 드리프트한다). NFKC 정규화 + zero-width
문자 제거로 우회를 막고, 표시용 목록(`FORBIDDEN_EXPRESSIONS`) 외에 변형을 잡는 정규식
패턴이 별도로 있다.

적용 지점 세 곳이 같은 함수를 호출한다:
1. **생성 검증** (`content_engine.py`) — 위반 시 `AUTO_REMEDIATION_MAX_GENERATIONS`(2)회까지 재생성
2. **발행 게이트** (`content_publication.py`)
3. **공개 표면 직렬화** (`api/public/site.py`)

---

## 환경변수

전체 목록(~95개)은 `.env.example`(로컬)·`.env.production.example`(프로덕션)이 소스다.
자주 건드리는 핵심만:

```
DATABASE_URL / SYNC_DATABASE_URL         # PostgreSQL
REDIS_URL                                 # Celery broker/backend + RedBeat

ANTHROPIC_API_KEY
CLAUDE_MODEL / CLAUDE_MODEL_FAST / AUTOFILL_MODEL

IMAGE_PROVIDER=google|openai
GOOGLE_IMAGE_MODEL / OPENAI_IMAGE_MODEL

OPENAI_API_KEY
OPENAI_MODEL_QUERY / OPENAI_MODEL_PARSE   # deploy.sh가 부동 계열명 배포를 거부

GEMINI_API_KEY / GEMINI_MODEL

SLACK_WEBHOOK_URL                         # AE 채널
SLACK_WEBHOOK_URL_DEV                     # 인프라 인시던트 채널
RESEND_API_KEY                            # 리드 이메일

GCP_PROJECT_ID / GCP_STORAGE_BUCKET
CERTIFICATE_MANAGER_AUTO_PROVISION        # 프로덕션 필수 true

WORKER_DISPATCH_SECRET                    # 32자 이상 필수
ADMIN_SECRET_KEY / ADMIN_SESSION_SECRET
REPUTATION_RELEASE_REVISION               # 로컬에서 직접 넣지 않음 — scripts/deploy.sh가 배포마다 커밋 SHA로 주입

COST_GUARD_*                              # 유형별 일/월 유료 호출 한도
```

`core/config.py`는 프로덕션 부팅 시 fail-fast로 검증한다 — 핵심 시크릿의 빈 값·dev 기본값
(예: `dev-lock-pepper-change-me`), `ALLOWED_ORIGINS`/`TRUSTED_PROXY_IPS`의 `localhost`/`127.0.0.1`
잔존, `REPUTATION_RELEASE_REVISION` 미설정 등을 감지하면 즉시 기동을 막는다. 값을 채웠는지가
아니라 실제 프로덕션 값인지를 검사하므로 `change-me` placeholder만으로는 통과하지 않는다.

---

## 개발 우선순위 (Phase 1) — 참고용 스냅샷, 현재 상태 아님

아래는 착수 시점(2026년 초) 계획이다. 이후 SoV 통계(Wilson 구간)·인용 귀속·격차 기반 슬롯·
STEP5 활성화 자동화·비용 가드·프롬프트 캐싱 등 다수의 후속 작업이 이 최초 범위를 넘어섰다.
현재 상태는 이 문서의 다른 절과 `docs/reviews/`의 최신 리뷰를 기준으로 판단한다.

```
Week 1-2: DB 모델 + 마이그레이션 / 병원 프로파일 Admin API / SoV 엔진 / V0 리포트 + Slack
Week 3-4: 콘텐츠 허브 공개 표면 / 콘텐츠 스케줄 API / Claude 콘텐츠 생성 엔진 / 이미지 생성 엔진
Week 5-6: 콘텐츠 Celery 스케줄러 / 콘텐츠 발행 API / 월간 SoV 리포트 자동화 / Admin UI 핵심 페이지
```

---

## 코드 규칙

1. **비동기 우선** — 모든 DB/API 호출은 async (Celery 워커는 sync 세션이 원칙 — `SyncSessionLocal`)
2. **타입 힌트 필수** — 모든 함수
3. **의료광고 필터 필수** — 콘텐츠 생성·발행·공개 직렬화 세 지점 모두 `utils/medical_filter.py`를
   통과해야 한다 (위 절 참고)
4. **외부 API 재시도** — tenacity로 최대 3회
5. **비용 절약** — OpenAI parse는 mini 모델, 프롬프트 캐싱(콘텐츠 생성 STATIC/병원 블록) 적극 활용
6. **비용 가드 필수** — 유료 외부 호출(Claude/이미지/OpenAI/Gemini) 앞에는 AE가 Admin에서 직접
   트리거하는 경로(autofill, 운영 기준 초안 생성 등)를 포함해 예외 없이
   `cost_guard.check_and_increment`/`record_provider_call`을 통과한 뒤 호출한다
   (`tests/test_cost_guard_admin_bypass.py`가 회귀를 잡는다)
7. **신규 Celery 태스크는 `celery_app.py`의 `task_routes`에 반드시 등록** — 누락하면 기본
   `celery` 큐로 떨어져 영원히 실행되지 않는다.
   `tests/test_celery_routing.py::test_every_registered_worker_task_has_a_task_routes_entry`가
   이를 강제한다
8. **`workers/`는 `api/`를 import하지 않는다** — 계층 역전이며 순환 의존을 만든다.
   (현재 `tasks.py`의 `seed_query_targets_from_matrix`, `milestone_monthly_facts.py`의
   reports import 2건이 알려진 예외이자 정리 대상이다 — 새 코드에서 늘리지 않는다)
9. **Slack 알림** — 사람이 행동해야 하는 이벤트만 즉시 보내고, 배치 결과는 실행당 1건 요약으로
   묶는다 (`docs/ops/slack-notification-policy.md`). 콘텐츠·병원 건별 반복 알림을 새로 만들지 않는다
10. **단일 소스 오브 트루스를 지킨다** — 금지 표현은 `utils/medical_filter.py`, Slack 문구는
    `docs/ops/slack-notification-policy.md`, beat 전체 스케줄은 `core/celery_app.py`, 환경변수는
    `.env.example`/`.env.production.example`이 유일한 정의처다. 이 문서에 값을 다시 나열해
    두 번째 소스를 만들지 않는다
