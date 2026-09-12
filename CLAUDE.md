# Re:putation — 현재 프로젝트 개발 안내

문서 버전: **2.7** · 갱신일: **2026-09-12 (Asia/Seoul)**
소스 기준선: **`31129d9911910b82c1161829d922a9760fac13a1`**
구현 상태: **체크포인트 2(`a774851`) 운영 배포 완료. 그 뒤 main의 stable-base Essence(`8c59141`) 등 31개 커밋과 콘텐츠 수율 버전업 v2.7(`claude/system-performance-review-x6vtn4`, [계획](docs/plans/2026-09-12-content-yield-versionup-plan.md))은 미배포**

이 파일은 과거 제품 브리프를 현재 코드 기준의 개발 안내로 교체한 것이다. 전체 흐름과 근거 파일은 [현재 시스템 구조](docs/architecture/system-map.md), 문서의 지위는 [문서 인덱스](docs/README.md)에서 확인한다. 코드 기본값과 운영 환경, 목표 정책과 현재 구현 차이를 구분한다.

## 운영 철학

- 사람의 최소한의 개입으로 지속 운영한다. 정상 생성·측정·발행과 자동 복구에 수동 승인이나 반복 알림을 추가하지 않는다.
- 사람이 맡는 일은 계약 인수·공식 정보와 공개 주소 결정·자동 검토가 해결하지 못한 예외·고객 보고서 전달이다.
- 자동 복구 중인 작업을 운영자의 할 일로 만들지 않는다. 최종 차단은 원인별로 묶고 중복을 억제한다. 개발 문제가 운영 채널로 쏟아지지 않도록 한다.
- 상태 저장, 외부 호출, 알림 성공을 구분한다. 저장 성공 후 큐·캐시·Slack 장애가 났다고 원래 업무를 실패한 것처럼 되돌리거나 중복 생성하지 않는다.

## 현재 기술·책임 경계

- Backend: Python 3.11/FastAPI, PostgreSQL/SQLAlchemy/Alembic, Celery/Redis/RedBeat, Jinja2/WeasyPrint. API async와 Worker sync 세션이 공존한다.
- Admin/Site: Next App Router, 조사 기준 Next 16.3.1, standalone 서버. Admin은 내부 전체 병원 운영 콘솔이며 브라우저→인증 BFF→Backend 구조다. 사람이 일으키는 admin 변경(POST/PATCH/PUT/DELETE)은 BFF가 서명한 actor 단언(`X-Admin-Actor-Assertion`, `BFF_ACTOR_SECRET`, 120초)을 요구하며, 배치·CLI는 `X-Admin-Actor-System`으로 감사에 `system:<job>`으로 남는다. 공유 `X-Admin-Key`만으로 actor를 고르는 경로는 없다.
- 운영 배포: API, Worker, Beat, Admin, Site 모두 GCP Cloud Run. Cloud SQL, Memorystore, GCS, HTTPS Load Balancer와 인증서 구성을 사용한다.
- 콘텐츠는 Anthropic Claude, 기본 이미지 경로는 Vertex Gemini, 측정은 OpenAI/Gemini API다. 개발 에이전트 모델과 서비스의 모델을 혼동하지 않는다. 실제 모델은 `backend/app/core/config.py`와 배포 설정으로 확인한다.
- `build_aeo_site`는 상태 준비·자동 활성화 작업이다. 별도의 `site_builder.py`나 병원별 HTML/CSS 생성기를 전제로 개발하지 않는다.

## 변경 시 보존할 계약

### 공개와 콘텐츠 게이트

공개 활성화의 공통 선행조건은 `profile_complete && site_built`다. V0 초기 진단은 독립 백그라운드 작업이며 공개 시작을 막지 않는다. 일정·Essence를 활성화 선행조건에 추가하지 않는다. 기본 주소는 조건 충족 시 자동 활성화하고, 자기 도메인은 기존 도메인·TLS 확인 경로를 따르며, PAUSED는 배경 작업으로 재개하지 않는다. 상태 변경은 서비스 구간·도메인 확인·감사 기록까지 검수한다.

콘텐츠의 신규 생성·발행은 일정과 현재 전체 자료에 유효한 Essence를 기준으로 한다. `EssenceReadiness.current`와 기존 승인 근거를 유지하는 `public_philosophy`의 목적을 섞지 않는다. 일반 텍스트 자료 생성·실질 변경은 durable 처리 run에 연결하고, 동일 정규화 값 PATCH는 근거와 처리 상태를 바꾸지 않는다. 사진과 원문 없는 URL 자료는 이 자동 처리 대상과 구분한다.

수동·자동 발행은 공통 콘텐츠 안전 검사를 사용하지만 현재 생애주기·예정일 조건은 완전히 같지 않다. Public API의 활성화·자료·본문·참고자료 검사를 제거하지 않는다. DB PUBLISHED만으로 공개 성공을 선언하지 않는다.

### 콘텐츠와 월간 계약

- 유형은 FAQ/DISEASE/TREATMENT/COLUMN/HEALTH/LOCAL/NOTICE, 월간 계약은 12/16/20편이다. 기본 배분은 `models/content.py`, 노출 부족에 따른 조정은 `gap_driven_slots.py`다.
- 현재 생성 분량 검사는 평문 1,800~5,200자다. FAQ의 전용 질문/답변, NOTICE의 참고자료 예외를 생성·편집·발행·공개에서 맞춘다.
- 의료광고 금지 표현은 `utils/medical_filter.py`를 정본으로 삼는다. 참고자료 제목과 공개 메타데이터 등 새 공개 필드도 검사 대상에 포함한다. 문맥 예외(역학 통계의 1위, 부정·한정이 바로 뒤따르는 완치·100%·성공률 등)는 필터 안에서만 정의하고 양방향 테스트를 함께 둔다. 경로별 허용 목록이나 우회를 만들지 않는다. 화이트리스트 도메인의 참고자료 제목이 필터에 걸리면 생성 단계에서 기관명 라벨로 치환하고, 발행·공개 게이트는 모든 제목을 그대로 검사한다.
- 작가 프롬프트와 검증기는 같은 단위를 말해야 한다. 분량은 공백·마크다운 제외 평문 1,800~5,200자이고 프롬프트도 그 단위로 요구한다. 참고자료 필수 유형은 생성과 발행이 같은 집합을 쓰며 프롬프트가 비워 두라고 지시하지 않는다. 결정적 검증기의 거절은 같은 프롬프트로 blind 재시도하지 않고 지적 내용을 다음 회차에 넘긴다. `stop_reason`이 `max_tokens`/`refusal`이면 잘림으로 처리한다.
- LLM 출력은 확률적이므로 작가·이미지·독립 검수의 실패는 `SAMPLE_RECOVERABLE`로 분류하고 KST 하루 단위 예산(본문 2세션, 이미지 4회)과 소진 3일 상한 안에서 재시도한다. 소진 뒤에만 `OPERATOR_REQUIRED`로 전이해 원인별 인시던트 1건을 OPEN으로 올린다. 모델이 HARD로 단정한 사실·안전 지적, 승인 기준 없음, 저장 본문의 금지 표현은 `INPUT_CHANGE_REQUIRED`로 남긴다. `scheduled_date`는 시도 지문에 넣지 않는다.
- 독립 AI 검수의 확신도 부족만으로 붙은 합성 UNCERTAIN은 같은 호출 안에서 상위 모델로 1회 재검수하고, 저장된 UNCERTAIN 차단은 스윕이 예산 안에서 재검수한다. HARD 사실·안전 지적은 삭제형 재작성 1회 뒤 재검수를 거친다. 미해결 HARD·UNCERTAIN이 발행을 막는 계약과 UNAVAILABLE이 PASS를 주지 않는 계약은 그대로다.
- 이미지는 업로드 전 정책 검토를 거치며 새 발행은 이미지 내용 hash·주제 hash·정책 버전에 연결된 인증을 요구한다. 생성 인물을 실제 원장 신원으로 사용하지 않는다. 2026-09-07~08 전환에서 당시 운영 공개 글 115건의 기존 이미지를 실제 바이트 재검수·content-addressed 불변 사본·CAS 저장으로 이 계약에 편입했다. 공개 GCS 이미지 프록시 URL은 인증된 내용 hash를 버전 질의값으로 포함해야 하며, 이미지 교체 때 그 값도 바뀌어 Site 이미지 캐시가 이전 바이트를 계속 제공하지 않게 한다. 비공개 기존 행도 일반 생성·발행의 엄격한 인증 gate를 통과해야 한다. 영구 레거시 우회나 합성 인증값을 만들지 않는다. 이미지 생성이 실패해 당일 예산이 소진되면 글을 빈 이미지로 내보내지 않고 같은 병원의 가장 오래된 인증 이미지를 빌려 발행한다. 빌린 행은 원본의 내용 hash·주제 hash·정책 버전을 그대로 옮기고 `image_reused_from_content_id`로 결합 대상을 명시한다. 새 제목의 주제 hash를 만들어 넣지 않는다. 재사용 인증도 내용 hash와 정책 버전은 요구하며, 교체 스윕(01:20·04:20·07:20)이 그 글의 주제 이미지를 만들어 마커를 지운다. 재사용할 인증 이미지가 없는 병원은 종전처럼 `CONTENT_IMAGE_NOT_READY`로 막힌다.
- 생성 claim 이후 외부 호출 결과를 저장할 때 현재 상태·claim token·content revision을 재확인한다. 취소·발행·최근 편집을 늦은 응답으로 덮어쓰지 않는다.
- 생성 당시 Essence와 최근 재검사 Essence를 분리한다. 전체 공개 후보 hash·검수 coverage와 unresolved HARD/UNCERTAIN finding을 발행 게이트에서 보존한다.
- 2026-09-07~08 기존 공개 글 전환은 고정된 운영 manifest와 CAS로 수행했다. FAQ 3건 구두점 수리, 본문 22건 독립 검수, 이미지 115건 인증 뒤 정확한 공개 ID 집합과 새 엄격 공개 gate를 read-only로 검증했다. AI 검수 메타데이터가 레거시인 글을 그 이유만으로 유료 재검수하지 않는 원칙을 유지한다. 상세 수치와 증거는 [운영 전환 기록](docs/releases/2026-09-07-eb55518-partial.md)을 본다.
- 지연 발행은 원 계약 월을 보존한다. `published_at`·`published_by`는 현재 공개 판을 나타내고, 처음 공개한 사실은 `first_published_at`·`first_published_by`에 한 번만 기록해 실제 발행·계약 이행·귀속의 닫힌 월 집계에 사용한다. 반려나 근거 철회로 현재 공개 판이 내려가도 최초 사실을 지우지 않으며 재발행은 현재 판 시각만 갱신한다. 마이그레이션 전에 반려가 이미 지운 발행일은 추정해 복원하지 않는다.
- 후행 검수는 조건부 표본 확인이다. 모든 글의 수동 승인이나 월간 보고 차단으로 확대하지 않는다.

### Admin 화면과 사람의 일

- 병원 화면은 `/hospitals/{id}` 아래 탭 4개(`현황 · 병원 정보 · 콘텐츠 · 보고서`)뿐이다. 옛 8개 경로(`dashboard, onboarding, profile, schedule, wiki, essence, query-targets, exposure-actions`)는 `admin/lib/route-redirects.ts`의 매핑으로 새 탭에 redirect되며 2026-10-09에 제거한다. 그 전에 백엔드가 만드는 admin 딥링크도 새 경로로 옮긴다. 새 탭·화면·전용 lib를 만들 때 옛 경로를 되살리지 않는다.
- 병원 상태는 `hospital_states.py`의 3상태(`준비 중 · 운영 중 · 일시정지`)와 `hospital_overview`의 예외 카드로만 표현한다. 사람의 할 일은 `requires_operator_action`(운영센터 직렬화기) 한 규칙으로 판정하고 현황·콘텐츠·운영센터·Slack이 같은 판정을 쓴다. 기한 안의 자동 재시도(`RETRYING`)와 `RUNNING`을 사람의 일로 표시하지 않으며, 스윕이 소유한 복구(예: 사이트 준비 재시도)는 시도마다 인시던트를 열지 않고 예산 소진 시 원인별 인시던트 하나만 연다.
- 사람이 하는 일은 계약 등록(한 화면 `/hospitals/new` → 병원 생성·계약 기록·인수 수락 한 트랜잭션), 병원 정보·공개 주소 결정, 예외 카드의 서버 허용 행동, 보고서 전달 기록이다. 운영자 문구는 `admin/lib/admin-copy.ts`의 `ADMIN_COPY`만 쓰고, `scripts/check_user_facing_terms.py`가 `admin/app`·`admin/lib`·`admin/types` 전체에서 통일 전 용어를 막는다. 백엔드가 만드는 운영자 문구(`readiness_operator_copy.py` 등)는 가드 밖이므로 존재하는 탭 이름만 쓰는지 검토 때 확인한다.
- 계약 등록으로 태어난 인수 기록은 `HANDOFF_ACCEPTED`이며 `sla_due_at`은 인수 기한이라 수락 뒤에는 온보딩 큐·마일스톤에서 기한 초과로 읽지 않는다.

### 측정·리포트

- API 답변 측정은 소비자 ChatGPT/Gemini 화면의 노출과 동일하지 않다. 실패·미확정과 미언급을 분리한다.
- 월간/V0 고정 관측 슬롯의 질문·플랫폼·반복·protocol과 답변·판정 체크포인트를 보존한다. V0는 짧은 실행 구간마다 같은 작업·측정 lineage로 자동 이어가고, 이미 성공한 공급자 단계는 다시 구매하지 않는다. 정상 이어가기 횟수와 실제 실패 재시도 예산을 섞지 않는다. COMPLETE/LIMITED/UNAVAILABLE와 legacy lineage를 구분하며 비교 불가한 기간을 상승/하락으로 표현하지 않는다.
- 보고 대상은 과거 서비스 구간과 계약 월로 결정한다. 현재 ACTIVE 목록만 사용하지 않는다.
- 내부용과 원장용 PDF를 분리하고 원장용 artifact의 실제 파일·hash·검증 결과를 확인한다. 내부 오류·명령어·검수 항목을 원장용에 넣지 않는다.
- 월간/V0 전달 API는 검증된 현재 원장용 artifact에 결합하여 AE가 전달한 사실을 기록한다. 무료 진단의 자동 Resend 이메일과 다르다.

### 자율 작업·알림·비용

- OperationRun, Incident, NotificationOutbox와 자료 처리 run·관측 슬롯의 멱등성·lease·에피소드, 서명된 worker dispatch를 유지한다.
- 새 태스크는 명시적 큐 라우팅·서명 목적·시간 제한·실패와 복구 연결·canary/readiness 영향을 확인한다. 현재 7개 큐는 한 Worker 서비스가 소비하며 `control`은 다음 빈 슬롯의 우선순위만 제공한다. 전용 용량이나 선점을 보장한다고 설명하지 않는다. RedBeat 변경은 영속 스케줄 재조정도 필요하다.
- 재시도는 일시 오류와 표본 실패에 한정하고 업무별 최대 횟수를 따른다. 공급자 재시도, 생성 개선 반복, 배치 재실행, 이메일 재발송을 일률적인 3회 규칙으로 바꾸지 않는다. 자동 복구가 예산 안에서 소유한 상태(RETRYING)는 운영자 큐·현황 카드·Slack에 사람의 일로 올리지 않는다.
- Essence 자동 검수가 보류하면 초안에 복구 사이클과 시각을 남기고 24h × 2^(cycle-1) 백오프로 최대 4회 자동 재검수한다. 사람이 손댄 초안은 자동 재시도하지 않는다. 72시간 넘게 ERROR인 필수 자료는 상태를 바꾸지 않고 이번 합성 입력에서만 제외하며 그 사실을 gap에 남긴다. Essence 합성·검수는 `essence` 비용 카테고리를 쓴다.
- 비용 예약 영수증과 공급자 HTTP attempt 원장을 분리한다. usage 누락은 0으로 바꾸지 않고 DB 실패 시 bounded Redis spool로 복구한다. Redis 비용 가드와 single-flight는 장애 시 fail-open이므로 절대 지출 상한이라고 설명하지 않는다.
- 무료 진단 single-flight는 동일 cache key의 중복 구매만 프로세스 간 합친다. provider semaphore는 프로세스 내부 한도이며 같은 API key의 전역 동시 호출 상한이 아니다.
- 공개 commit 뒤 IndexNow 제출은 durable intent와 제한된 재시도로 처리한다. 정상 제출·재시도 성공·usage spool 복구를 Slack 알림으로 만들지 않는다.
- Slack은 [알림 정책](docs/ops/slack-notification-policy.md)을 따른다. 정상 발행 알림을 다시 추가하지 않는다. outbox 실패가 도메인 트랜잭션을 되돌리지 않게 한다.

## 검증과 배포

변경 범위에 맞는 테스트·타입·lint·계약 검사를 수행한다. 기본 진입점은 `make test-backend-local`, `make test-frontend`, `make copy-guard`, `make db-budget-guard`다. 통합 테스트의 DB/Redis/PDF 의존성과 skip 여부를 함께 기록한다. `make test`의 컨테이너 경로 제약, `make setup`의 기존 `.env` 덮어쓰기 동작에 유의한다.

배포는 [현재 배포 안내](docs/ops/deployment-runbook.md)를 따른다. 병원별 헬스는 HTTP 200만 보지 않고 hospital ID·canonical host·현재 리비전까지 확인한다. 배포 후 페이지·sitemap·llms·콘텐츠·이미지·큐 canary·스키마를 변경 범위에 맞게 확인한다. 문서만 변경했다면 런타임이 바뀐 것처럼 새 배포 성공을 주장하지 않는다.

## 문서 유지 규칙

현재 안내에는 문서 버전·갱신일·코드 기준을 남긴다. 수정 시 실제 서비스/Worker/API/Public 경로를 따라가고 코드 주석도 검증한다. 과거 PRD·계획·검수 기록은 그 시점의 기록으로 보존한다. 사용자 지시가 이 안내보다 우선한다.
