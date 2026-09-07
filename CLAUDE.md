# Re:putation — 현재 프로젝트 개발 안내

문서 버전: **2.0** · 갱신일: **2026-09-07 (Asia/Seoul)**
구현 기준: **`345a6420998bcba21169519cf5ad77600cbfa94b`**

이 파일은 과거 제품 브리프를 현재 코드 기준의 개발 안내로 교체한 것이다. 전체 흐름과 근거 파일은 [현재 시스템 구조](docs/architecture/system-map.md), 문서의 지위는 [문서 인덱스](docs/README.md)에서 확인한다. 코드 기본값과 운영 환경, 목표 정책과 현재 구현 차이를 구분한다.

## 운영 철학

- 사람의 최소한의 개입으로 지속 운영한다. 정상 생성·측정·발행과 자동 복구에 수동 승인이나 반복 알림을 추가하지 않는다.
- 사람이 맡는 일은 계약 인수·공식 정보와 공개 주소 결정·자동 검토가 해결하지 못한 예외·고객 보고서 전달이다.
- 자동 복구 중인 작업을 운영자의 할 일로 만들지 않는다. 최종 차단은 원인별로 묶고 중복을 억제한다. 개발 문제가 운영 채널로 쏟아지지 않도록 한다.
- 상태 저장, 외부 호출, 알림 성공을 구분한다. 저장 성공 후 큐·캐시·Slack 장애가 났다고 원래 업무를 실패한 것처럼 되돌리거나 중복 생성하지 않는다.

## 현재 기술·책임 경계

- Backend: Python 3.11/FastAPI, PostgreSQL/SQLAlchemy/Alembic, Celery/Redis/RedBeat, Jinja2/WeasyPrint. API async와 Worker sync 세션이 공존한다.
- Admin/Site: Next App Router, 조사 기준 Next 16.3.1, standalone 서버. Admin은 내부 전체 병원 운영 콘솔이며 브라우저→인증 BFF→Backend 구조다.
- 운영 배포: API, Worker, Beat, Admin, Site 모두 GCP Cloud Run. Cloud SQL, Memorystore, GCS, HTTPS Load Balancer와 인증서 구성을 사용한다.
- 콘텐츠는 Anthropic Claude, 기본 이미지 경로는 Vertex Gemini, 측정은 OpenAI/Gemini API다. 개발 에이전트 모델과 서비스의 모델을 혼동하지 않는다. 실제 모델은 `backend/app/core/config.py`와 배포 설정으로 확인한다.
- `build_aeo_site`는 상태 준비·자동 활성화 작업이다. 별도의 `site_builder.py`나 병원별 HTML/CSS 생성기를 전제로 개발하지 않는다.

## 변경 시 보존할 계약

### 공개와 콘텐츠 게이트

공개 활성화는 `profile_complete && v0_report_done && site_built`다. 일정·Essence를 이 선행조건에 추가하지 않는다. 기본 주소는 조건 충족 시 자동 활성화하고 PAUSED는 배경 작업으로 재개하지 않는다. 상태 변경은 서비스 구간·도메인 확인·감사 기록까지 검수한다.

콘텐츠의 신규 생성·발행은 일정과 현재 전체 자료에 유효한 Essence를 기준으로 한다. `EssenceReadiness.current`와 기존 승인 근거를 유지하는 `public_philosophy`의 목적을 섞지 않는다. 일반 자료 생성/수정은 현재 PENDING 저장까지만 수행하는 경로가 있으므로 모든 자료가 자동 처리된다고 가정하지 않는다.

수동·자동 발행은 공통 콘텐츠 안전 검사를 사용하지만 현재 생애주기·예정일 조건은 완전히 같지 않다. Public API의 활성화·자료·본문·참고자료 검사를 제거하지 않는다. DB PUBLISHED만으로 공개 성공을 선언하지 않는다.

### 콘텐츠와 월간 계약

- 유형은 FAQ/DISEASE/TREATMENT/COLUMN/HEALTH/LOCAL/NOTICE, 월간 계약은 12/16/20편이다. 기본 배분은 `models/content.py`, 노출 부족에 따른 조정은 `gap_driven_slots.py`다.
- 현재 생성 분량 검사는 평문 1,800~5,200자다. FAQ의 전용 질문/답변, NOTICE의 참고자료 예외를 생성·편집·발행·공개에서 맞춘다.
- 의료광고 금지 표현은 `utils/medical_filter.py`를 정본으로 삼는다. 참고자료 제목과 공개 메타데이터 등 새 공개 필드도 검사 대상에 포함한다.
- 이미지는 업로드 전 정책 검토를 거치며 새 발행은 검증 시각을 요구한다. 생성 인물을 실제 원장 신원으로 사용하지 않는다.
- 생성 claim 이후 외부 호출 결과를 저장할 때 현재 상태·lease·대상 제목 등을 재확인한다. 취소되거나 이미 발행된 행을 늦은 응답으로 덮어쓰지 않는다.
- 지연 발행은 원 계약 월을 보존하고 실제 `published_at`을 기록한다. 과거 발행일 변경으로 편수 이행을 꾸미지 않는다.
- 후행 검수는 조건부 표본 확인이다. 모든 글의 수동 승인이나 월간 보고 차단으로 확대하지 않는다.

### 측정·리포트

- API 답변 측정은 소비자 ChatGPT/Gemini 화면의 노출과 동일하지 않다. 실패·미확정과 미언급을 분리한다.
- 월간 고정 manifest의 질문·플랫폼·시도 근거를 보존한다. COMPLETE 커버리지와 반복 전체 성공을 구분하며 비교 불가한 기간을 상승/하락으로 표현하지 않는다.
- 보고 대상은 과거 서비스 구간과 계약 월로 결정한다. 현재 ACTIVE 목록만 사용하지 않는다.
- 내부용과 원장용 PDF를 분리하고 원장용 artifact의 실제 파일·hash·검증 결과를 확인한다. 내부 오류·명령어·검수 항목을 원장용에 넣지 않는다.
- 월간 전달 API는 AE가 전달한 사실을 기록한다. 무료 진단의 자동 Resend 이메일과 다르다. V0의 단일 PDF와 월간 doctor artifact 계약 차이는 구조 문서의 미해결 항목을 본다.

### 자율 작업·알림·비용

- OperationRun, Incident, NotificationOutbox의 멱등성·lease·에피소드와 서명된 worker dispatch를 유지한다.
- 새 태스크는 명시적 큐 라우팅·서명 목적·시간 제한·실패와 복구 연결·canary/readiness 영향을 확인한다. RedBeat 변경은 영속 스케줄 재조정도 필요하다.
- 재시도는 일시 오류에 한정하고 업무별 최대 횟수를 따른다. 공급자 재시도, 생성 개선 반복, 배치 재실행, 이메일 재발송을 일률적인 3회 규칙으로 바꾸지 않는다.
- 비용 예약과 실제 호출 기록을 분리한다. 현재 Redis 비용 가드는 장애 시 fail-open이다. 이 한계를 절대 지출 상한이라고 설명하지 않는다.
- Slack은 [알림 정책](docs/ops/slack-notification-policy.md)을 따른다. 정상 발행 알림을 다시 추가하지 않는다. outbox 실패가 도메인 트랜잭션을 되돌리지 않게 한다.

## 검증과 배포

변경 범위에 맞는 테스트·타입·lint·계약 검사를 수행한다. 기본 진입점은 `make test-backend-local`, `make test-frontend`, `make copy-guard`, `make db-budget-guard`다. 통합 테스트의 DB/Redis/PDF 의존성과 skip 여부를 함께 기록한다. `make test`의 컨테이너 경로 제약, `make setup`의 기존 `.env` 덮어쓰기 동작에 유의한다.

배포는 [현재 배포 안내](docs/ops/deployment-runbook.md)를 따른다. 병원별 헬스는 HTTP 200만 보지 않고 hospital ID·canonical host·현재 리비전까지 확인한다. 배포 후 페이지·sitemap·llms·콘텐츠·이미지·큐 canary·스키마를 변경 범위에 맞게 확인한다. 문서만 변경했다면 런타임이 바뀐 것처럼 새 배포 성공을 주장하지 않는다.

## 문서 유지 규칙

현재 안내에는 문서 버전·갱신일·코드 기준을 남긴다. 수정 시 실제 서비스/Worker/API/Public 경로를 따라가고 코드 주석도 검증한다. 과거 PRD·계획·검수 기록은 그 시점의 기록으로 보존한다. 사용자 지시가 이 안내보다 우선한다.
