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
    • Slack → AE: "장편한외과의원 V0 리포트 생성 완료 — 원장 보고 전 확인 요망"
    • AE가 직접 원장에게 보고 (시스템이 보내지 않음)
    ↓
[STEP 4] AI 노출 콘텐츠 허브 노출 준비 (시스템 — 프로파일 기반)
    • Next.js /site 공개 표면이 승인된 병원 정보와 콘텐츠를 읽어 노출할 수 있게 상태를 준비
    • Schema.org MedicalClinic 마크업, FAQ/진료 안내, 콘텐츠 목록은 /site가 동적으로 제공
    • 별도 홈페이지/HTML 납품물이 아니라 AI와 검색엔진이 참고할 병원 정보·콘텐츠 허브 운영 상태를 만든다
    • 준비 완료 시 Slack → AE: "콘텐츠 허브 노출 준비 완료 — Admin에서 공개 정보와 도메인 상태 확인 필요"
    ↓
[STEP 5] 콘텐츠 운영 기준(essence/philosophy) 승인 (사람 — Admin에서 AE가)
    • Admin에서 병원 근거 자료(홈페이지·블로그·인터뷰·브로슈어 등)를 업로드/크롤링
    • 근거 자료로부터 원장 톤·핵심 의료 지식·가치(essence)를 담은 콘텐츠 운영 기준 초안을 생성 후 승인(APPROVED)
    • STEP8 콘텐츠 자동 생성의 필수 게이트 — 승인된 운영 기준이 없으면 야간 배치가 해당 병원 생성을 건너뛰고
      Slack으로 "운영 기준 미승인" 알림을 발송한다 (발행 게이트로도 재사용됨)
    ↓
[STEP 6] 콘텐츠 스케줄 설정 (사람 — Admin에서 AE가)
    • 요금제 선택: 16편/12편/8편 (월간)
    • 발행 요일 설정 (예: 화·목 or 월·수·금 등)
    • 저장 시 첫 달 콘텐츠 캘린더 자동 생성
    • ACTIVE 전환(STEP7)의 필수 사전 조건 중 하나 — STEP7보다 먼저 완료되어 있어야 한다
    • 예외: 이미 ACTIVE(공개 노출 중)인 병원의 스케줄을 재설정하는 경우, 저장 즉시 자동으로 ACTIVE 상태를 유지한다
    ↓
[STEP 7] 공개 노출 상태 확인 및 ACTIVE 전환 (사람 — Admin에서 AE가)
    • Admin에서 병원 도메인/공개 URL 정보를 입력
    • 활성화 게이트: profile_complete·v0_report_done·site_built·schedule_set(STEP6) 네 가지가 모두 충족되어야 하며,
      자기 도메인을 연결한 경우 DNS 확인도 통과해야 한다
    • 게이트 통과 시 공개 노출 상태(site_live)를 켜고 병원 상태를 ACTIVE로 전환
    ↓
[STEP 8] 콘텐츠 자동 생성 사이클 (시스템 — 이후 지속)
    발행일 전날 밤 23:00
        → 병원별 승인된 콘텐츠 운영 기준(STEP5) 확인 — 없으면 생성을 건너뛰고 Slack 알림
        → Claude Sonnet으로 본문 자동 생성
        → Google Imagen 3으로 대표 이미지 자동 생성
        → DB에 초안 저장 (status: DRAFT)
    발행일 당일 아침 08:00
        → 최신 승인 운영 기준·참고 자료·의료광고 금지 표현 자동 안전검사
        → 통과 시 AI가 참고할 콘텐츠 허브에 자동 게재 (status: PUBLISHED)
        → Slack → AE: "자동 발행 완료 — 후행 확인 필요"
        → AE가 공개 글/Admin에서 후행 확인, 문제 발견 시 즉시 수정 또는 비공개 후 재생성
        → 자동 검사 실패 시 공개하지 않고 차단 사유 Slack
    ↓
[STEP 9] 월말 AI 답변 언급 리포트 (시스템 — 매월 마지막 날)
    • 월간 AI 답변 언급률 집계 → PDF 자동 생성
    • Slack → AE: "장편한외과의원 월간 리포트 생성 완료"
    • AE가 원장에게 보고 (시스템이 보내지 않음)
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

| 유형 | 16편/월 | 12편/월 | 8편/월 |
|------|:-------:|:-------:|:------:|
| FAQ | 4 | 3 | 2 |
| DISEASE | 3 | 3 | 2 |
| TREATMENT | 3 | 2 | 2 |
| COLUMN | 2 | 2 | 1 |
| HEALTH | 2 | 1 | 1 |
| LOCAL | 1 | 1 | 0 |
| NOTICE | 1 | 0 | 0 |

---

## 기술 스택

### Backend
- **FastAPI** (Python 3.11) — Admin API + Public API
- **Celery** + **Redis** — 비동기 태스크 / 스케줄러
- **PostgreSQL** — 메인 DB
- **SQLAlchemy** (async) + **Alembic** — ORM / 마이그레이션
- **Anthropic SDK** — Claude Sonnet 3.5 (콘텐츠 생성)
- **Google Cloud Vertex AI** — Imagen 3 (이미지 생성)
- **OpenAI SDK** — GPT-4o (SoV 쿼리 발송·파싱)
- **google-generativeai** — Gemini API (SoV 측정)
- **WeasyPrint** — PDF 리포트
- **Jinja2** — HTML 템플릿

### Frontend
- **Admin** (`/admin`): Next.js 16 App Router — AE 운영 도구
- **Site** (`/site`): Next.js 15 SSG/ISR — 병원별 정보·콘텐츠 허브 공개 표면

### Infrastructure
- **Docker Compose** — 로컬 개발
- **GCP Cloud Run** — API + Worker 프로덕션
- **GCP Cloud Storage** — 생성 이미지 저장
- **GCP Cloud Run** — Admin + Site (Next.js standalone 컨테이너, LB 호스트 라우팅)

---

## 프로젝트 구조

```
reputation/
├── CLAUDE.md
├── docker-compose.yml
├── .env.example
├── Makefile
│
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── app/
│       ├── main.py
│       ├── core/
│       │   ├── config.py          ← 모든 환경변수
│       │   ├── database.py        ← async DB 세션
│       │   └── celery_app.py      ← Celery + Beat 스케줄
│       ├── models/
│       │   ├── hospital.py        ← 병원 프로파일 (확장형)
│       │   ├── content.py         ← 콘텐츠 아이템 + 스케줄
│       │   ├── sov.py             ← SoV 측정 기록
│       │   └── report.py          ← 리포트
│       ├── schemas/               ← Pydantic 스키마
│       ├── api/
│       │   ├── admin/             ← AE용 Admin API
│       │   │   ├── hospitals.py   ← 프로파일 CRUD
│       │   │   ├── content.py     ← 콘텐츠 검토·발행
│       │   │   ├── schedule.py    ← 스케줄 설정
│       │   │   └── reports.py     ← 리포트 조회
│       │   └── public/            ← 정보·콘텐츠 허브 공개 표면용
│       │       └── site.py        ← 병원 데이터 제공
│       ├── services/
│       │   ├── content_engine.py  ← Claude Sonnet 콘텐츠 생성
│       │   ├── image_engine.py    ← Google Imagen 3
│       │   ├── sov_engine.py      ← 환자 질문 발송·파싱·AI 답변 언급률 계산
│       │   ├── report_engine.py   ← PDF 리포트
│       │   └── notifier.py        ← Slack 알림
│       ├── workers/
│       │   └── tasks.py           ← 모든 Celery 태스크
│       └── templates/
│           └── report.html        ← 리포트 Jinja2 템플릿
│
├── admin/                         ← Next.js Admin 패널
│   ├── app/
│   │   ├── hospitals/             ← 병원 목록·상세
│   │   ├── hospitals/[id]/
│   │   │   ├── profile/           ← 프로파일 편집
│   │   │   ├── content/           ← 콘텐츠 목록·검토·발행
│   │   │   ├── schedule/          ← 스케줄 설정
│   │   │   └── reports/           ← 리포트 조회
│   │   └── layout.tsx
│   └── package.json
│
└── site/                          ← Next.js 정보·콘텐츠 허브 공개 표면
    ├── app/
    │   └── [hospital-slug]/       ← 병원별 동적 라우팅
    └── package.json
```

---

## 핵심 데이터 모델

### Hospital (병원 프로파일)
```
기본: id, name, slug, plan (PLAN_16|PLAN_12|PLAN_8), status, created_at

연락처: address, phone, business_hours (JSON)
URL: website_url, blog_url, kakao_channel_url, aeo_domain (연결된 도메인)

타겟: region[], specialties[], keywords[], competitors[]

원장: director_name, director_career, director_philosophy
진료: treatments[] (JSON - 진료 항목 목록)

상태 플래그:
  profile_complete: bool  (프로파일 입력 완료)
  v0_report_done: bool    (V0 리포트 발송 완료)
  site_built: bool        (콘텐츠 허브 노출 준비 완료 — legacy 필드명)
  site_live: bool         (공개 도메인/노출 상태 확인 완료 — legacy 필드명)
  schedule_set: bool      (콘텐츠 스케줄 설정 완료)
```

### ContentSchedule (콘텐츠 스케줄)
```
id, hospital_id
plan: PLAN_16|PLAN_12|PLAN_8
publish_days: [0,2,4]  (월=0, 화=1, ... 일=6)
active_from: date
is_active: bool
```

### ContentItem (콘텐츠 아이템)
```
id, hospital_id, schedule_id
content_type: FAQ|DISEASE|TREATMENT|COLUMN|HEALTH|LOCAL|NOTICE
sequence_no: int    (이번 달 N번째)
total_count: int    (이번 달 전체 편수)

title: str
body: text          (마크다운)
image_url: str      (GCS URL)
image_prompt: str   (생성에 사용한 프롬프트)

scheduled_date: date    (발행 예정일)
status: DRAFT|READY|PUBLISHED|REJECTED
generated_at: datetime  (생성 시각)
published_at: datetime  (실제 발행 시각)
published_by: str       (발행 AE 이름)
```

### SovRecord (SoV 측정)
```
id, hospital_id, query_id
ai_platform: chatgpt|gemini
measured_at: datetime
is_mentioned: bool
mention_rank: int|null
mention_sentiment: positive|neutral|negative|null
raw_response: text
```

### MonthlyReport (월간 리포트)
```
id, hospital_id
period_year: int, period_month: int
pdf_path: str
sov_summary: JSON
content_summary: JSON
sent_at: datetime|null
```

---

## 콘텐츠 생성 프롬프트 설계

### Claude Sonnet 시스템 프롬프트
```
당신은 병원 의료 콘텐츠 전문 작가입니다.
아래 병원 정보를 바탕으로 ChatGPT·Gemini가 병원을 잘 이해할 수 있는 콘텐츠를 작성합니다.

[병원 프로파일]
{hospital_profile}

작성 규칙:
1. 첫 문단에서 환자 질문에 대한 핵심 답변을 먼저 제시
2. 환자의 실제 언어로 작성 (의학 용어 최소화)
3. 지역명·병원명·원장명을 자연스럽게 포함
4. 의료광고법 준수 — 금지 표현 절대 불가:
   1등, 최고, 최우수, 유일, 완치, 100%, 성공률, 부작용 없는, 가장 잘하는, 국내 최초
5. 분량: 600~900자 (한국어 기준)
6. 마크다운 형식으로 작성 (소제목 H2 활용)
```

### Imagen 3 프롬프트 패턴
```
콘텐츠 유형별 기본 프롬프트:
FAQ / DISEASE: "Clean medical infographic, soft blue and white tones, Korean hospital, professional healthcare illustration, no text"
TREATMENT: "Modern hospital treatment room, clean white aesthetic, medical equipment, soft lighting, professional photography style"
COLUMN: "Professional Korean doctor portrait setting, warm clinic atmosphere, trustworthy medical professional"
HEALTH: "Healthy lifestyle, Korean context, clean bright illustration, prevention healthcare"
LOCAL: "Korean local clinic exterior, neighborhood healthcare, welcoming entrance, daytime"
NOTICE: "Modern Korean hospital interior, clean white and blue, professional medical environment"
```

---

## Slack 알림 메시지 규격

```
V0 리포트 완료:
"🔍 [V0 리포트] *{병원명}* AI 답변 노출 진단 리포트 생성 완료\n현재 ChatGPT 답변 내 병원 언급률: {sov}%\n원장 보고 전 확인 후 전달해 주세요."

콘텐츠 허브 노출 준비 완료:
"🏗️ [AI 노출 콘텐츠 허브] *{병원명}* 병원 정보와 콘텐츠 허브 노출 준비 완료\nAdmin에서 공개 정보와 도메인 상태를 확인해 주세요."

콘텐츠 자동 발행 완료 (당일 08:00):
"✅ [자동 발행 완료] *{병원명}* {total}편 중 {seq}번째 콘텐츠 공개 완료\n유형: {type} | 발행일: {date}\n공개 글과 Admin에서 후행 확인해 주세요."

월간 리포트 완료:
"📊 [월간 리포트] *{병원명}* {year}년 {month}월 AI 답변 언급 리포트 생성 완료\nAI 답변 내 병원 언급률: {sov}% | 전월 대비: {change:+.1f}%p\n원장 보고 자료를 확인해 주세요."
```

---

## 의료광고 금지 표현 필터 (모든 콘텐츠 생성 시 필수 적용)

```python
FORBIDDEN_EXPRESSIONS = [
    "1등", "최고", "최우수", "유일", "완치", "100%",
    "성공률", "부작용 없는", "검증된", "가장 잘하는",
    "국내 최초", "세계 최초", "특허", "독보적"
]
```
GPT/Claude 생성 후 반드시 자동 필터 → 포함 시 재생성 트리거

---

## 환경변수

```
# DB
DATABASE_URL=postgresql+asyncpg://...
SYNC_DATABASE_URL=postgresql://...

# Redis
REDIS_URL=redis://localhost:6379/0

# Anthropic (콘텐츠 생성)
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-sonnet-4-5          # 콘텐츠 생성
CLAUDE_MODEL_FAST=claude-haiku-4-5-20251001   # 빠른 작업

# Google Cloud (이미지 생성 + 저장)
GCP_PROJECT_ID=...
GCP_LOCATION=us-central1
GCP_STORAGE_BUCKET=reputation-images
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

# OpenAI (SoV 측정)
OPENAI_API_KEY=sk-...
OPENAI_MODEL_QUERY=gpt-4o
OPENAI_MODEL_PARSE=gpt-4o-mini

# Gemini (SoV 측정)
GEMINI_API_KEY=REPLACE_ME

# Slack
SLACK_WEBHOOK_URL=https://hooks.slack.com/...

# Report
REPORT_OUTPUT_DIR=/tmp/reports

# App
APP_ENV=development
ADMIN_SECRET_KEY=change-me
```

---

## 개발 우선순위 (Phase 1)

```
Week 1-2:
  ✅ DB 모델 + 마이그레이션
  ✅ 병원 프로파일 Admin API (CRUD)
  ✅ SoV 엔진 (쿼리 자동 생성·발송·파싱)
  ✅ V0 리포트 생성 + Slack 알림

Week 3-4:
  ✅ 병원 정보·콘텐츠 허브 공개 표면 준비
  ✅ 콘텐츠 스케줄 설정 API
  ✅ Claude Sonnet 콘텐츠 생성 엔진
  ✅ Imagen 3 이미지 생성 엔진

Week 5-6:
  ✅ 콘텐츠 Celery 스케줄러 (전날 밤 생성 + 당일 아침 Slack)
  ✅ 콘텐츠 발행 API
  ✅ 월간 SoV 리포트 자동화
  ✅ Admin UI (Next.js) 핵심 페이지
```

---

## 코드 규칙

1. **비동기 우선** — 모든 DB/API 호출은 async
2. **타입 힌트 필수** — 모든 함수
3. **의료광고 필터 필수** — 콘텐츠 생성 후 항상 검사
4. **외부 API 재시도** — tenacity로 최대 3회
5. **비용 절약** — OpenAI parse는 mini 모델, 캐싱 적극 활용
6. **Slack 알림** — 모든 주요 이벤트에 반드시 발송
