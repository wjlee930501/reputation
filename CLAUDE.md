# Re:putation — 현재 프로젝트 개발 안내

## 최신 운영 배포 — 2026-10-01 미결 PR 정리·원장 보고서 개편

main `a8a86b57`(PR #163·#168·#177·#179·#181~#193)을 5개 서비스에, 이어 #194·#196·#197을 API·Worker·Beat에
배포했다(최종 main `ce731db7`). 현재 리비전은 api `00215-mtl`, worker `00204-q9m`, beat `00199-gq7`,
site `00143-mvd`, admin `00099-fc4`, **DB head는 `0082_add_content_reference_checks`**다. 원장용 월간 보고서가
쉬운 말·새 디자인으로 바뀌었고, 9월 보고서 9곳 전부를 `TEMPLATE_REFRESH`로 숫자 그대로 v2를 만들었다
(postcheck 9/9 PASS, 측정 미완료 4곳은 운영자 지정 `allow_recovery_pending`). 새 버전은 원장에게 재전달해야 한다.
일일 운영 요약이 9/24부터 `fallback_text` 1000자 초과로 저장에 실패하던 기존 장애를 #197로 고쳤다.
배포 전 `.env.production`을 운영 API env로 다시 만들었다(작업 PC 파일이 10/1 모델 변경 이전 값이었다). 상세는
[원장 보고서 개편 배포 기록](docs/releases/2026-10-01-report-plain-language-production.md)을 본다.

## 이전 운영 배포 — 2026-09-28 도입문의 Slack 채널 분리

main `7266674`(PR #164·#165)를 API·Worker·Beat에 배포했다. 현재 리비전은 api `00206-8hf`,
worker `00195-7dt`, beat `00190-bqz`이고 Site·Admin과 DB head `0080_lead_diagnosis_supersede`는 그대로다.
공개 도입문의 접수 알림은 선택 secret `SLACK_WEBHOOK_URL_INQUIRY`(`#noti-도입문의-뉴비짓`)로 가며,
비어 있으면 `SLACK_WEBHOOK_URL`로 간다. Aside로 실제 문의 2건을 제출해 새 채널 수신을 확인했다.
요약은 처리방침의 Slack 고지 범위(병원명·진료과·지역·마스킹 연락처·알려진 유입 경로·접수 시각·
자동 처리)만 싣고 제목에 `[Re:putation]` 출처를 붙인다. 상세는 [도입문의 Slack 채널 분리 배포 기록](docs/releases/2026-09-28-inquiry-slack-channel-production.md)을 본다.

## 최신 운영 배포 — 2026-09-23~24 통합 정합성 보완·파비콘

main `38cff0e`(PR #153 런타임)를 5개 서비스에, 이어 Site만 `fad966d`(PR #155·#156 파비콘)까지 배포했다.
현재 리비전은 api `00201-g5w`, worker `00190-f9b`, beat `00186-8sg`, site `00142-xh2`, admin `00096-rrh`,
DB head는 `0080_lead_diagnosis_supersede` 그대로다. readiness Job, 공개 9개 병원 헬스, 새 리비전 오류 0건을
확인했고 Admin 신규 화면 조작·도입문의 실제 제출은 남아 있다. 배포 전 `.env.production`을 운영 env와
대조한다 — `deploy.sh`가 서비스 env를 파일로 통째로 바꾸며, 이번에는 오래된 파일을 운영 값으로 다시 만들었다.
상세는 [통합 정합성 보완 배포 기록](docs/releases/2026-09-23-integration-hardening-production.md)을 본다.

- 병원 페이지 탭 아이콘은 Re:putation 심볼이 아니라 `/favicon/{slug}`의 병원 모노그램(대표색 바탕 + 병원명
  첫 글자, `lib/clinic-favicon.ts`)이다. 병원 조회 실패 시에도 플랫폼 심볼로 떨어지지 않는다.
  플랫폼 `favicon.ico`는 `site/public/`에 둔다(`app/`에 두면 Next가 병원 페이지에도 링크한다).

PR #153이 더한 계약:

- 갈음된 리드 진단의 복구 요청은 HTTP 경계에서 409다(워커 claim과 같은 집합). 갈음은 옛 진단의
  lead 인시던트를 같은 트랜잭션에서 닫고, 실행 도중 갈음된 진단은 새 인시던트를 열지 않는다.
  값 고쳐 다시 만들기는 고친 병원명을 리드·판정 대상에 반영하고 연락처는 덮지 않는다.
- `POST /admin/lead-diagnoses`는 `Idempotency-Key`로 멱등하다(`CREATE_LEAD_DIAGNOSIS`
  OperationRun 영수증). Admin BFF는 키 없는 생성 요청을 막는다.
- 원장용 PDF는 내부 전용 표식("내부 검수용"·"원장 전달 불가"·"토킹 포인트"·"AE 전용")이
  읽히면 검증에서 거절된다.
- 백엔드 Admin 링크는 병원 4개 탭 경로만 만든다. 옛 경로로 저장된 인시던트 `admin_path`는
  읽는 시점에 새 탭으로 바뀐다(`incident_safety.current_admin_path`, `route-redirects.ts`와 동기).
- Admin의 V0 판정은 `report_type`이다. 서버의 `delivery_tracked`는 V0에도 true다.
- `site/app` 최상위 정적 라우트는 모두 백엔드 예약 slug여야 한다(`contact`·`brochure` 추가).
- NHN 문자 secret은 `INQUIRY_SMS_PROVIDER=nhn`일 때만 조회하고, readiness `facts.inquiry_sms`가
  문자 설정 상태를 참고 사실로 보여준다.
- 미결: 랜딩 병합(#151)이 `/ai-diagnosis` 셀프 신청을 Site에서 종료했고 폼 앵커가 `#contact`로
  바뀌었다(`#lead`는 `#contact`로 보낸다). 아래 도입문의 절의 `#lead`·`/ai-diagnosis 유지` 문구는
  대표 결정 뒤 함께 고친다. 처리방침 버전·수집 항목, `/brochure` 저장 경로도 같은 상태다.

## 최신 운영 배포 — 2026-09-22 노출 진단 수동 생성

main tip `300a6636aee6d5fb73141848f34189194a86c9e5`(기능은 PR #143 `c104bad`)를 5개 서비스에 배포했다.
**DB head는 `0080_lead_diagnosis_supersede`다** — 리드당 진단이 '1건'에서 '활성 1건'이 되고
갈음된 진단이 폴러·운영자 큐에서 빠진다. Admin에 `노출 진단 생성` 탭과 상담 요청의
`값 고쳐 다시 만들기`가 생겼다. CI 10/10, readiness 7개 큐, 공개 9개 병원 헬스 200,
새 리비전 오류 0건을 확인했다. 새 화면의 실제 조작 검증은 남아 있다.
상세는 [노출 진단 수동 생성 배포 기록](docs/releases/2026-09-22-manual-diagnosis-production.md)을 본다.
아래 날짜별 ‘최신/현재’ 표시는 각 배포 당시의 이력이다.

## 최신 운영 배포 — 2026-09-22 도입문의 CTA 전환

PR #141의 main 소스 `8f40f49590345ccd333ff85d786d9fcfe41848f2`를 5개 서비스에 배포했다.
DB head는 `0079_topic_swap_fallback` 그대로이며 schema 변경이 없다.
랜딩의 모든 CTA가 도입문의 폼(`#lead`)을 가리키고, 보고서 재생성이 정상 완료 상태까지 열렸다.
CI 10/10, readiness 7개 큐, 공개 9개 병원 헬스 200, 새 리비전 오류 0건을 확인했다.
도입문의 성공 경로(진단 자동 생성·안내 SMS)의 실제 1건 검증은 남아 있다.
상세는 [도입문의 CTA 배포 기록](docs/releases/2026-09-22-inquiry-cta-production.md)을 본다.
아래 날짜별 ‘최신/현재’ 표시는 각 배포 당시의 이력이다.

## 최신 운영 배포 — 2026-09-22 통합 안정성 보완

PR #138의 main 소스 `a35b7f2a0c8d7ee568abbd9a7abdda75a231576f`를 5개 서비스에 배포했다.
현재 suffix는 `integrity-a35b7f2`, DB head는 `0079_topic_swap_fallback`이다.
main CI 9/9와 별도 큐 리허설을 통과했고, 운영에서 5개 서비스의 digest·트래픽·설정 보존,
16개 readiness 검사·새 release 7개 큐, 공개 9개 병원·기존 글 보존을 확인했다.
2026-09-22 10:05 KST까지 새 리비전 오류 조회 0건이다. 전체 사용자 E2E와 향후 주기 관측은
별도 범위이며, 이 문서의 갱신 커밋은 런타임 이미지 소스가 아니다.
실제 digest·롤백 좌표·검증 범위는 [통합 안정성 배포 기록](docs/releases/2026-09-22-integrity-production.md)을 따른다.
아래 날짜별 ‘최신/현재’ 표시는 각 배포 당시의 이력이다.

## 최신 운영 배포 — 2026-09-17 통합 보완

PR #120의 main 소스 `2381566883cfcfcf87d317a2c9671b96817499a8`를 API·Worker·Beat에 배포했다.
Backend suffix는 `ops-235758-2381566`, DB head는 `0079_topic_swap_fallback`, RedBeat는 `2026-09-17.1`이다.
계약 이행·월간 PDF·도메인 감시 보완이 포함됐다. Site 이미지 최적화와 기존 Admin 리비전은 유지했다.
main CI 9/9, 4,326개 Backend 테스트, 운영 readiness와 공개 9개 병원·151개 글 검증을 통과했다.
개발 Slack 미설정과 기존 NHN SMS 설정 경고는 별도 후속 사항으로 남았다.
아래 날짜별 상태는 당시 이력이며 현재 실행 정본은 [통합 운영 배포 기록](docs/releases/2026-09-17-integrated-production.md)이다.

## 현재 운영 배포 — 2026-09-16 리팩터링

`18531d3452287afa012e28ec256d920708c90a57`(PR #116)이 실제 운영에 배포됐다.
5개 서비스는 `ref-151613-18531d3` 리비전, DB head는 `0079_topic_swap_fallback`이다.
main CI 9/9, 새 release 7개 큐 readiness, 병원 9곳·공개 글 128개·이미지·Admin 로그인
사후 검증을 통과했다. 환경·secret·IAM·자원은 보존했다.
[리팩터링 운영 배포 기록](docs/releases/2026-09-16-refactor-production.md)이 최신 실행 정본이며,
아래 날짜별 상태는 당시 이력이다. 문서 후속 커밋은 런타임 이미지의 소스 SHA가 아니다.

## 이전 운영 배포 — 2026-09-16 GEO

`012aeeb68e1965ea2194300989140ab9b8c4f155`가 실제 운영에 배포됐다(PR #114).
API/Worker/Beat/Site/Admin은 `geo-125451-012aeeb` 리비전, DB head는 `0079_topic_swap_fallback`이다.
9개 병원·128개 공개 글, 현재 릴리스 7개 큐, production Admin 로그인 화면을 사후 확인했다.
Site/Admin 전용 SA로 전환했고 기존 frontend grants는 롤백을 위해 유지했다.
증거·범위·롤백 좌표는 [운영 배포 기록](docs/releases/2026-09-16-geo-autonomy-production.md)을 본다.
아래 날짜별 절의 ‘운영 배포 전’은 해당 시점의 이력이며 최신 배포 상태는 위 리팩터링 절이 우선한다.

## 2026-09-15 GEO 자율 운영 보강 — 로컬 구현 우선 계약

상태: `codex/geo-autonomy-hardening-20260915`, d96bd21 기반 로컬 구현·선별 검증. 운영 배포 전.
기존 문구와 충돌할 때 아래의 새 계약을 적용한다. 상세 구현·한계·검증은 [GEO 보강 기록](docs/releases/2026-09-15-geo-autonomy-hardening.md)을 본다.
정상 생성마다 메시지를 보내지 않되, 하루 한 건의 GEO 운영 요약은 Slack outbox로 보고한다. 기본 18시 KST 이후이며 누락 tick은 당일 다음 hourly tick이 회수한다.
원장 상담 피드백은 다음 신규 생성부터 반영하며 기존 글 일괄 재생성을 하지 않는다. 활성 피드백은 총 6,000자, 중복은 합치고 사람의 기록 종료만 허용한다.
BaseEssence의 일반 자료 추가 drift는 재합성하지 않는다. 명시적 근거 제외·수정은 새 생성만 보류하고 의존 원고를 선택적으로 철회·수리한다. 철회 표시를 단순 발행 판정으로 지우지 않는다.
일정 재설정으로 원고/최초 발행 이력을 삭제하지 않는다. 병원·계약 월 단위로 남은 할당만 채우며 다음 달 plan 변경은 append-only다.
주요 공개 변경과 SITE_REVALIDATION intent는 한 transaction이다. cache invalidation 수락을 실제 환자 페이지 공개 확인으로 표시하지 않는다.
새 생성 task는 소유권이 검증된 OperationRun을 바탕으로 실행 시 reservation token을 execution token으로 바꾸며, 대기 시간을 실행 lease로 오인하지 않는다.
Site/Admin IAM 분리는 단계적으로 적용한다. 구 revision 종료 전 legacy grants를 회수하지 않는다.


문서 버전: **2.13** · 갱신일: **2026-10-05 (Asia/Seoul)**
소스 기준선: **`4db1b69` 이후 커밋 이력 정합성 보완**
구현 상태: **체크포인트 2(`a774851`) 운영 배포 완료. 그 뒤 main의 stable-base Essence(`8c59141`) 등 31개 커밋과 콘텐츠 수율 버전업 v2.7(`claude/system-performance-review-x6vtn4`, [계획](docs/plans/2026-09-12-content-yield-versionup-plan.md))은 미배포**

이 파일은 과거 제품 브리프를 현재 코드 기준의 개발 안내로 교체한 것이다. 전체 흐름과 근거 파일은 [현재 시스템 구조](docs/architecture/system-map.md), 문서의 지위는 [문서 인덱스](docs/README.md)에서 확인한다. 코드 기본값과 운영 환경, 목표 정책과 현재 구현 차이를 구분한다.

## 운영 철학

- 사람의 최소한의 개입으로 지속 운영한다. 정상 생성·측정·발행과 자동 복구에 수동 승인이나 반복 알림을 추가하지 않는다.
- 사람이 맡는 일은 계약 인수·공식 정보와 공개 주소 결정·자동 검토가 해결하지 못한 예외·고객 보고서 전달이다.
- 자동 복구 중인 작업을 운영자의 할 일로 만들지 않는다. 최종 차단은 원인별로 묶고 중복을 억제한다. 개발 문제가 운영 채널로 쏟아지지 않도록 한다.
- 상태 저장, 외부 호출, 알림 성공을 구분한다. 저장 성공 후 큐·캐시·Slack 장애가 났다고 원래 업무를 실패한 것처럼 되돌리거나 중복 생성하지 않는다.

## 현재 기술·책임 경계

- Backend: Python 3.11/FastAPI, PostgreSQL/SQLAlchemy/Alembic, Celery/Redis/RedBeat, Jinja2/WeasyPrint. API async와 Worker sync 세션이 공존한다.
- Admin/Site: Next App Router, 저장소 선언 기준 Next 16.3.8, standalone 서버. Admin은 내부 전체 병원 운영 콘솔이며 브라우저→인증 BFF→Backend 구조다. 사람이 일으키는 admin 변경(POST/PATCH/PUT/DELETE)은 BFF가 서명한 actor 단언(`X-Admin-Actor-Assertion`, `BFF_ACTOR_SECRET`, 120초)을 요구하며, 배치·CLI는 `X-Admin-Actor-System`으로 감사에 `system:<job>`으로 남는다. 공유 `X-Admin-Key`만으로 actor를 고르는 경로는 없다.
- 운영 배포: API, Worker, Beat, Admin, Site 모두 GCP Cloud Run. Cloud SQL, Memorystore, GCS, HTTPS Load Balancer와 인증서 구성을 사용한다.
- 모든 LLM·이미지 호출은 `OPENROUTER_API_KEY` 하나로 OpenRouter 게이트웨이를 거친다 — 콘텐츠는 Claude 계열, 이미지는 Gemini/OpenAI 계열, 측정은 OpenAI/Gemini 계열 모델을 `vendor/model` 슬러그로 호출한다. 공급자 직결 SDK(Anthropic·OpenAI·google-genai·Vertex)는 쓰지 않는다. 개발 에이전트 모델과 서비스의 모델을 혼동하지 않는다. 실제 모델은 `backend/app/core/config.py`와 배포 설정으로 확인한다.
- `build_aeo_site`는 상태 준비·자동 활성화 작업이다. 별도의 `site_builder.py`나 병원별 HTML/CSS 생성기를 전제로 개발하지 않는다.

## 변경 시 보존할 계약

### 공개와 콘텐츠 게이트

공개 활성화의 공통 선행조건은 `profile_complete && site_built`다. V0 초기 진단은 독립 백그라운드 작업이며 공개 시작을 막지 않는다. 일정·Essence를 활성화 선행조건에 추가하지 않는다. 기본 주소는 조건 충족 시 자동 활성화하고, 자기 도메인은 기존 도메인·TLS 확인 경로를 따르며, PAUSED는 배경 작업으로 재개하지 않는다. 상태 변경은 서비스 구간·도메인 확인·감사 기록까지 검수한다.

콘텐츠 신규 생성·발행은 일정과 승인된 BaseEssence를 사용한다. 일반 자료 추가·노이즈 hash 변화는 진단 정보이며 자동 운영을 막지 않는다. 명시적 승인 근거 철회·수정만 재승인 전 신규 생성을 보류한다. `EssenceReadiness.current`와 기존 승인 근거를 유지하는 `public_philosophy`의 목적을 섞지 않는다. 일반 텍스트 자료 생성·실질 변경은 durable 처리 run에 연결하고, 동일 정규화 값 PATCH는 근거와 처리 상태를 바꾸지 않는다. 사진과 원문 없는 URL 자료는 이 자동 처리 대상과 구분한다.

수동·자동 발행은 공통 콘텐츠 안전 검사를 사용하지만 현재 생애주기·예정일 조건은 완전히 같지 않다. Public API의 활성화·자료·본문·참고자료 검사를 제거하지 않는다. DB PUBLISHED만으로 공개 성공을 선언하지 않는다.

### 콘텐츠와 월간 계약

- 유형은 FAQ/DISEASE/TREATMENT/COLUMN/HEALTH/LOCAL/NOTICE, 월간 계약은 12/16/20편이다. 기본 배분은 `models/content.py`, 노출 부족에 따른 조정은 `gap_driven_slots.py`다.
- 현재 생성 분량 검사는 평문 1,800~5,200자다. FAQ의 전용 질문/답변, NOTICE의 참고자료 예외를 생성·편집·발행·공개에서 맞춘다. 참고자료 필수 판정은 `services/reference_requirement.py` 한 규칙이다 — 의료 안내 유형은 항상, NOTICE는 측정 질문이 연결됐을 때(`query_target_id`·브리프의 `query_target`·`exposure_action.query_target_id`) 필수이고, 질문 연결 없는 순수 운영 공지만 면제다(2af00d02 0개 공개·5821409e 영구 미발행 양쪽). 브리프의 `target_keyword`·`treatment_narrative`·계획 모드만으로는 판정하지 않는다. 생성(규칙·스키마·치유·GEO 거절)과 발행 게이트(07:45·08:00·catch-up·수동 발행·restore)가 이 규칙을 쓰고, 필수 글이 비면 수기 목록 치유를 먼저 한 뒤 `MISSING_REFERENCES`로 보류한다. 공개 가시성(`content_visibility`)만 이미 공개된 글을 배포로 내리지 않으려고 유형 규칙을 유지한다(`public_surface_has_required_references`).
- 의료광고 금지 표현은 `utils/medical_filter.py`를 정본으로 삼는다. 참고자료 제목과 공개 메타데이터 등 새 공개 필드도 검사 대상에 포함한다. 문맥 예외(역학 통계의 1위, 부정·한정이 바로 뒤따르는 완치·100%·성공률 등)는 필터 안에서만 정의하고 양방향 테스트를 함께 둔다. 경로별 허용 목록이나 우회를 만들지 않는다. 화이트리스트 도메인의 참고자료 제목이 필터에 걸리면 생성 단계에서 기관명 라벨로 치환하고, 발행·공개 게이트는 모든 제목을 그대로 검사한다.
- 작가 프롬프트와 검증기는 같은 단위를 말해야 한다. 분량은 공백·마크다운 제외 평문 1,800~5,200자이고 프롬프트도 그 단위로 요구한다. 참고자료 필수 유형은 생성과 발행이 같은 집합을 쓰며 프롬프트가 비워 두라고 지시하지 않는다. 결정적 검증기의 거절은 같은 프롬프트로 blind 재시도하지 않고 지적 내용을 다음 회차에 넘긴다. `stop_reason`이 `max_tokens`/`refusal`이면 잘림으로 처리한다.
- 필수 문구는 **현재 APPROVED 승인본의** `must_use_messages`뿐이다. 콘텐츠 가이드(brief)에 저장된 문구·운영자가 가이드에 덧붙인 문구는 원문 요구 대상도 검수 면제 근거도 아니며, 승인본이 없으면 요구·면제가 없다(재검수 스윕·공개 재검수 백필 포함). 필수 문구는 의역하지 않고 원문 그대로 독립된 문장으로 본문에 넣는다. 생성 후 `services/must_use_verbatim.py`가 확인하고, 빠지면 기존 재작성 루프·호출 예산 안에서 다시 쓰며 끝내 빠지면 `GENERATION_REJECTED`(본문 표본 실패)다. 의료광고 금지 표현이 든 승인본 문구는 작가 프롬프트·원문 요구·면제에서 모두 제외하고, 제외할 때마다 경고 로그를 남기며 승인본 버전당 한 건의 운영자 인시던트(`must_use_exclusions`)로 기록한다. 독립 검수 지적의 `quote`가 후보 안의 필수 문구 원문과 정규화 기준으로 완전히 같으면 그 지적은 `target=MUST_USE_MESSAGE`·SOFT로 기록만 남고 게시를 막지 않는다. 일부만 겹치거나 말을 덧붙인 문장, 인용 없는 지적, 지적 문구가 다른 문장을 함께 인용·지목한 지적은 면제하지 않고 그대로 막는다. 정규화는 숫자 사이의 공백·기호를 지우지 않는다(`1 0cm`≠`10cm`, `1.0cm`≠`10cm`). 단어·어간 목록으로 판정하지 않는다.
- LLM 출력은 확률적이므로 작가·이미지·독립 검수의 실패는 `SAMPLE_RECOVERABLE`로 분류하고 KST 하루 단위 예산(본문 2세션, 이미지 4회)과 소진 3일 상한 안에서 재시도한다. 소진 뒤에만 `OPERATOR_REQUIRED`로 전이해 원인별 인시던트 1건을 OPEN으로 올린다. 본문 표본 실패는 예외로 계단이 하나 더 있다 — 3일 소진 뒤 같은 슬롯·같은 예정일로 주제 교체를 1회 거친 뒤에만 `OPERATOR_REQUIRED`가 된다. 교체 대상은 공개 이력(`first_published_at`)과 사람 편집(`human_edited_at`)이 없는 글뿐이고, 교체는 인시던트 epoch를 새로 열어 옛 주제의 인시던트를 닫고 새 주제의 실패를 새 건으로 연다. 모델이 HARD로 단정한 사실·안전 지적 중 인용(`quote`)으로 문장을 짚은 것과 응급 안내 누락(HARD/UNCERTAIN)은 사람의 PATCH를 기다리지 않고 최소 교정 패스(`services/content_minimal_correction.py`)가 푼다 — 지적 문장만 승인 프로필대로 고치거나 지우고(새 수치·고유명사가 들면 거절하고 삭제), 응급 안내는 코드 상수 템플릿을 넣으며, 지적 문장 밖이 바뀌면 거절한다. 교정본은 반드시 독립 재검수를 받고, 게이트는 교정 기록(`auto_correction`)이 있으면 교정본 hash에 묶인 PASS 없이는 통과시키지 않는다. 첫 생성(빈 슬롯)도 이미지 구매 전에 같은 패스를 거친다. 교정 패스는 발행 이력·사람 편집이 있거나 DRAFT·READY가 아닌 글, 필수 문구 문장(여러 문장짜리 문구의 한 문장 포함)을 고치지 않으며 저장 UPDATE도 같은 술어를 건다. 검수 장애 물러서기·한도(`CONTENT_AI_REVIEW_UNAVAILABLE_MAX_RETRIES`)·비용 보류 중에는 돌지 않고, 두 계수는 서로의 기록을 쓰지 않는다. 글(주제)당 교정·재검수 상한(`CONTENT_AUTO_CORRECTION_MAX_PASSES`·`_MAX_REREVIEWS`, 기본 2·2)을 넘기면 주제 교체(`CONTENT_AUTO_TOPIC_SWAP_MAX`, 기본 1), 그것도 다 쓰면 OPERATOR_REQUIRED다. 23:00 창이 내일 글을 담으므로 전날 밤에 교정된다. 주제 선정은 키워드에만 있고 진료 항목에 없는 검사·시술 질문을 고르지 않는다. 인용 없는 HARD, 승인 기준 없음, 저장 본문의 금지 표현은 `INPUT_CHANGE_REQUIRED`로 남긴다. `scheduled_date`는 시도 지문에 넣지 않는다.
- 독립 AI 검수의 확신도 부족만으로 붙은 합성 UNCERTAIN은 같은 호출 안에서 상위 모델로 1회 재검수하고, 저장된 UNCERTAIN 차단은 스윕이 예산 안에서 재검수한다. HARD 사실·안전 지적은 삭제형 재작성 1회 뒤 재검수를 거친다. 미해결 HARD·UNCERTAIN이 발행을 막는 계약과 UNAVAILABLE이 PASS를 주지 않는 계약은 그대로다.
- 이미지는 업로드 전 정책 검토를 거치며 새 발행은 이미지 내용 hash·주제 hash·정책 버전에 연결된 인증을 요구한다. 생성 인물을 실제 원장 신원으로 사용하지 않는다. 2026-09-07~08 전환에서 당시 운영 공개 글 115건의 기존 이미지를 실제 바이트 재검수·content-addressed 불변 사본·CAS 저장으로 이 계약에 편입했다. 공개 GCS 이미지 프록시 URL은 인증된 내용 hash를 버전 질의값으로 포함해야 하며, 이미지 교체 때 그 값도 바뀌어 Site 이미지 캐시가 이전 바이트를 계속 제공하지 않게 한다. 비공개 기존 행도 일반 생성·발행의 엄격한 인증 gate를 통과해야 한다. 영구 레거시 우회나 합성 인증값을 만들지 않는다. 이미지 생성이 실패해 당일 예산이 소진되면 글을 빈 이미지로 내보내지 않고 같은 병원의 가장 오래된 인증 이미지를 빌려 발행한다. 빌린 행은 원본의 내용 hash·주제 hash·정책 버전을 그대로 옮기고 `image_reused_from_content_id`로 결합 대상을 명시한다. 새 제목의 주제 hash를 만들어 넣지 않는다. 재사용 인증도 내용 hash와 정책 버전은 요구하며, 교체 스윕(01:20·04:20·07:20)이 그 글의 주제 이미지를 만들어 마커를 지운다. 재사용할 인증 이미지가 없는 병원은 종전처럼 `CONTENT_IMAGE_NOT_READY`로 막힌다.
- 생성 claim 이후 외부 호출 결과를 저장할 때 현재 상태·claim token·content revision을 재확인한다. 취소·발행·최근 편집을 늦은 응답으로 덮어쓰지 않는다.
- 23:00과 01·04·07 배치는 디스패처다. 슬롯을 claim한 뒤 글 단위 태스크 `generate_claimed_content_item`(content 큐, claim token 동반, `GENERATE_CONTENT_ITEM` OperationRun)으로 넘기고 즉시 끝난다. 글 단위 태스크는 lease 토큰이 바뀌었거나 만료됐으면 공급자 호출 없이 물러난다. 재시도는 태스크가 아니라 스윕과 재시도 정책이 소유한다. 23:00 창은 내일과 그 다음 날(`NIGHTLY_GENERATION_LOOKAHEAD_DAYS=2`)이고 병원 간 라운드로빈으로 상한 50을 나눈다. 01·04·07 복구 스윕의 창은 발행 catch-up과 같은 7일이며, 로더가 워커와 같은 규칙으로 재시도 불가 행을 claim 전에 거른다. 30분 유예 안의 claim은 진행 중인 일감이라 stuck으로 보지 않는다. 처리량은 `CELERY_CONCURRENCY × (8h ÷ 6분)`으로 추정하며 부족하면 디스패치 시 경고를 남긴다.
- 참고자료의 주제 불일치는 차단이 아니라 제거다. 사람이 실제 GET으로 근거가 될 수 없다고 확인한 주소는 `REFERENCE_URL_EXCLUSIONS`(항목마다 사유)에 두고 수기 목록 후보·모델 참고자료·저장된 통과 어디서도 인정하지 않는다(수기 목록과 같은 문서 판정으로 비교해 별칭도 막고, GET의 최종 주소나 저장된 통과 기록의 최종 주소가 제외 문서여도 같다). 기본 선택지는 검증된 수기 목록(`CURATED_MEDICAL_SOURCE_PAGES`)이고, 목록 밖 URL은 실제 GET(200·화이트리스트 유지·soft-404/빈 템플릿/통계·메뉴 아님·실제 제목·본문이 글 주제와 일치)을 통과해야만 남는다. 판단 불가(403/429/시간 초과/영문·기관명뿐인 제목/운영 외 환경의 오프라인 기본값)는 목록 밖이면 제거, 목록이면 유지다. 모델 라벨은 주제 판정에 쓰지 않는다. 검증 결과는 `content_items.reference_checks`(0082)에 남고, 07:45·08:00·수동 발행은 같은 URL·같은 글 주제 지문(제목·첫 H2·FAQ 질문·brief)의 24시간 내 통과 기록을 요구해 없으면 다시 검증한다(미래 시각 기록은 5분 시계 차이까지만, 형식이 깨진 항목은 실패). 기관 장애는 같은 URL·같은 주제의 7일 내 직전 통과를 재사용하고, 그것도 없는 목록 밖 URL의 일시 장애(연결·시간 초과·5xx·408/429)는 빼지 않고 다음 시간대로 미루며 예정일 23시 발행기부터 08:00 요약에 '기관 사이트 접속 불가'로 알린다. 실행당 GET 상한 초과도 미룸이다. 비어지면 글 주제로 채점한 수기 목록 치유, 그래도 없으면 `MISSING_REFERENCES` 보류다. 진료비·병원 선택 글(제목 기준, `reference_requirement.references_left_to_operator`)은 공신력 있는 문서가 본질적으로 없어 생성·발행 모두 수기 목록으로 채우지 않고, 비면 그 보류는 자동 본문 수리·주제 교체 없이 곧바로 `OPERATOR_REQUIRED`(사람이 정함)다. 병원 선택은 제목에 수기 목록의 질환·시술 키워드(의료 주제)가 없는 고르기 글만이다(`title_names_medical_subject`). 아직 쓰이지 않은 슬롯은 작가가 만든 제목으로 판정해, 작가 회차 뒤에도 참고자료가 0개면 표본 사다리 없이 `MISSING_REFERENCES`+`operator_decides` 기록과 기한 없는 OPEN이 된다. 통과한 참고자료가 있으면 그대로 발행한다 — 단, 발행 전(DRAFT·READY와 생성 중인 슬롯) 진료비·병원 선택 글에서는 작가가 인용해 통과한 수기 목록 문서(`is_curated_source_url`, 별칭 포함)도 빼고 목록 밖 문서의 실제 GET 통과만 남긴다(그 통과는 치유와 같은 카탈로그 키워드 대조다). 공개된 글의 참고자료는 그대로다. 생성 프롬프트의 검증된 문서 힌트도 브리프의 측정 질문(질의·핵심 키워드·질문 이름)이 진료비·병원 선택이면 주지 않는다(생성 뒤 치유 판정은 작가 제목 그대로). 수기 목록 대조·치유 선택·의료 주제 판정은 진료과 이름과 병원 고르기 경로 키워드(정형외과·심장내과·병원선택·통증종류 …, `authority_sources.keyword_names_provider`)를 주제로 세지 않는다. 관리자 PATCH가 발행 전 진료비·병원 선택 글(저장될 제목 기준)에 수기 목록 문서를 담으면 GET 전에 422 `CURATED_REFERENCE_NOT_ALLOWED`로 거절한다. 수기 목록 문서인지는 목록 항목별 문서 판정(`authority_sources._matching_documents`: scheme·www·기본 포트·경로 표기·항목이 쓰지 않는 질의 무시, 항목 URL 자신의 id 이름마다 정수 비교 — KDCA `cntnts_sn`은 값의 숫자만 모아, 다른 이름은 앞 정수로 — 반복 값은 빼는 판정(`is_curated_source_url`·제외 목록)은 상한 없이 하나라도 맞으면, 인정해 주는 판정(`curated_source_entries`·검증기의 `curated`)은 서버가 쓰는 첫 값만)으로 판정하고, GET의 최종 주소가 목록 문서면(리다이렉트) 그 주소도 목록 문서다 — 생성·발행 재검증은 빼고 PATCH는 GET 뒤 같은 422다. GET은 행·병원 잠금 밖에서 하고 잠근 뒤 상태·판·참고자료·주제가 그대로이고 발행 전(DRAFT·READY)일 때만 적용한다 — 공개된 글의 참고자료는 어떤 자동 경로도 바꾸지 않는다. 아직 생성되지 않은 슬롯(제목·본문 없음)은 재검증·치유하지 않는다(판이 올라 진행 중인 생성 저장이 버려진다). 관리자 PATCH도 같은 검증을 거쳐 실패 URL과 사유를 400으로 돌려준다. restore는 검증만 하며 공개됐던 글의 참고자료를 바꾸지 않고, 통과하지 못하면 409로 거절한다. 상세는 `services/reference_verification.py`. 검수자의 REFERENCE 지적은 SOFT 비차단이며 해당 출처만 제거한다. 중복 주제 유사도 검사는 어떤 경우에도 차단·인시던트·Slack을 만들지 않는다. 문체 보완 예산 1회로 다른 각도를 요구하고, 그래도 유사하면 발행하고 `duplicate_topic_findings`에 기록만 남긴다.
- 재사용 이미지는 같은 content_type을 먼저 고른다. 재사용할 글 이미지가 없으면 병원 대표 이미지(`hero_image_url`)를 병원 단위로 한 번 인증(로고·병원 정체성 허용, 문자·인물 금지, 주제 일치 요구 없음)해 `image_fallback_source='HOSPITAL_HERO'`로 붙인다. 이 모양도 내용 hash와 정책 버전을 요구하며 교체 스윕이 마커를 지운다. 그것도 없을 때만 `CONTENT_IMAGE_NOT_READY`다.
- Celery 밖의 외부 감시(`/admin/watchdog/pipeline`, Cloud Scheduler 5분·08:30)가 canary 신선도·Beat 생존·당일 발행 0건을 보고 Slack 웹훅에 직접 보낸다. 감시 토큰이 비어 있어도 부팅은 막지 않는다. 인프라 조건은 개발 채널, 발행 누락은 운영 채널이며 개발 웹훅이 없으면 운영 채널로 대체하는 예외는 이 부품에만 적용한다.
- 생성 당시 Essence와 최근 재검사 Essence를 분리한다. 전체 공개 후보 hash·검수 coverage와 unresolved HARD/UNCERTAIN finding을 발행 게이트에서 보존한다.
- 2026-09-07~08 기존 공개 글 전환은 고정된 운영 manifest와 CAS로 수행했다. FAQ 3건 구두점 수리, 본문 22건 독립 검수, 이미지 115건 인증 뒤 정확한 공개 ID 집합과 새 엄격 공개 gate를 read-only로 검증했다. AI 검수 메타데이터가 레거시인 글을 그 이유만으로 유료 재검수하지 않는 원칙을 유지한다. 상세 수치와 증거는 [운영 전환 기록](docs/releases/2026-09-07-eb55518-partial.md)을 본다.
- 지연 발행은 원 계약 월을 보존한다. `published_at`·`published_by`는 현재 공개 판을 나타내고, 처음 공개한 사실은 `first_published_at`·`first_published_by`에 한 번만 기록해 실제 발행·계약 이행·귀속의 닫힌 월 집계에 사용한다. 반려나 근거 철회로 현재 공개 판이 내려가도 최초 사실을 지우지 않으며 재발행은 현재 판 시각만 갱신한다. 마이그레이션 전에 반려가 이미 지운 발행일은 추정해 복원하지 않는다.
- 후행 검수는 조건부 표본 확인이다. 모든 글의 수동 승인이나 월간 보고 차단으로 확대하지 않고, 운영자 큐 행이나 목록 라벨로도 올리지 않으며 콘텐츠 탭과 보고서 증빙에만 보인다.

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
- 원장용 월간 PDF는 쉬운 합니다체로 원장님께 말한다. ‘소개’ 대신 ‘언급’, 첫 측정은 ‘기준점’이라 쓰고, 감소·미언급은 결손 나열 대신 우리가 바꿀 다음 수(더 넓은 키워드·새 질문 유형·안내 보강)로 말한다. 숫자는 칸에 그대로 보이고, 모르는 값과 0을 섞지 않으며, 환자 수·순위를 약속하지 않는다. 문구는 `report_narrative.py`·`report_engine._director_copy`에 두고 `tests/test_report_plain_language.py`가 금지어·의료광고 필터를 막는다.
- 템플릿만 바꾼 뒤 지난달 리포트를 다시 찍을 때는 일반 재생성이 아니라 `TEMPLATE_REFRESH`(`rebuild=true&template_only=true`)를 쓴다. 대체할 버전의 저장 요약·마감 시각을 그대로 옮기고, 숫자 판정(`build_monthly_template_refresh_plan`)이 PASS가 아니면 만들지 않는다. 노출 행동 연결 같은 부수효과가 없다. 절차는 [템플릿 갱신](docs/ops/monthly-template-refresh.md).
- 월간/V0 전달 API는 검증된 현재 원장용 artifact에 결합하여 AE가 전달한 사실을 기록한다. 무료 진단의 자동 Resend 이메일과 다르다.
- 도입문의 공개 접수는 진료과·지역·키워드가 함께 오면 INTERNAL 초도 진단을 자동 생성하고, 원장 휴대전화로 안내 문자(NHN Cloud SMS)를 보낸다. 리드 저장 뒤의 부수효과라 어느 쪽이 실패해도 접수를 되돌리지 않으며, 진단 규칙은 Admin 수동 생성과 같은 서비스 함수를 쓴다. 랜딩의 모든 CTA는 이 도입문의 폼(`#lead`)을 가리키고 그 폼은 세 입력을 필수로 받는다 — 셋 중 하나라도 빠지면 문의만 쌓이고 연락할 근거가 만들어지지 않는다. 셀프서브 무료 진단(`/ai-diagnosis`)은 유지하되 랜딩 CTA에서는 분리한다. 상세는 [도입문의 접수 자동 처리](docs/ops/inquiry-intake-automation.md).
- 콜용 노출 진단은 사람이 직접 만들 수 있다. 문의가 없는 병원은 Admin `노출 진단 생성` 탭에서 리드와 함께 새로 만들고(동의를 받은 적이 없으므로 `privacy`는 False), 도입문의의 값이 틀렸을 때는 상담 요청 화면의 `값 고쳐 다시 만들기`로 고친다. 리드당 진단은 '1건'이 아니라 '활성 1건'이며 옛 진단은 `superseded_at`·`superseded_by_id`로 갈음해 보존한다 — 실제로 지출한 공급자 호출이 기록으로 남아야 한다. 갈음된 진단은 폴러·운영자 큐·복구 버튼 어디에도 올라오지 않고 보고서만 계속 열린다. HTTP 경계와 워커 claim은 같은 집합을 본다. 고객에게 나간 진단은 갈음 대상이 아니다.
- 보고서 재생성은 실패 복구 전용이 아니다. 정상적으로 만들어진 보고서도 생성 기준이 바뀌면 사유를 남기고 새 버전으로 다시 만들 수 있다. 월간은 새 report 행과 버전을 만들고 이전 보고서·전달 기록을 보존하므로 전달 완료본도 대상이며, 화면은 "새 버전은 다시 전달해야 반영된다"를 경고로 알린다. 초기 진단(V0)은 월간 생성 경로가 만들지 않으므로 그 버튼을 달지 않는다. 리드 진단은 아티팩트를 버전으로 쌓되 `PENDING`·`INTERNAL`만 제자리 재생성 대상이다 — 고객에게 나간 리드는 공개 토큰 뷰가 최신 버전을 서빙해 이미 보낸 링크의 내용이 바뀌고, `ck_lead_diagnoses_delivery_requires_report`가 같은 선을 긋는다. HTTP 경계와 워커 claim은 항상 같은 집합을 봐야 한다.

### 자율 작업·알림·비용

- OperationRun, Incident, NotificationOutbox와 자료 처리 run·관측 슬롯의 멱등성·lease·에피소드, 서명된 worker dispatch를 유지한다.
- 새 태스크는 명시적 큐 라우팅·서명 목적·시간 제한·실패와 복구 연결·canary/readiness 영향을 확인한다. 현재 7개 큐는 한 Worker 서비스가 소비하며 `control`은 다음 빈 슬롯의 우선순위만 제공한다. 전용 용량이나 선점을 보장한다고 설명하지 않는다. RedBeat 변경은 영속 스케줄 재조정도 필요하다.
- 재시도는 일시 오류와 표본 실패에 한정하고 업무별 최대 횟수를 따른다. 공급자 재시도, 생성 개선 반복, 배치 재실행, 이메일 재발송을 일률적인 3회 규칙으로 바꾸지 않는다. 자동 복구가 예산 안에서 소유한 상태(RETRYING)는 운영자 큐·현황 카드·Slack에 사람의 일로 올리지 않는다.
- 채널 URL 자료의 fetch 실패는 인시던트가 아니라 자료 행의 상태(`ERROR`·`fetch_error`)이며, 필수 자료가 없는 병원은 콘텐츠 상태 카드가 사람을 부른다. 옛 `CHANNEL_SOURCE_FETCH_FAILED`는 스윕의 한도 있는 멱등 정리가 링크를 걷고 열린 것만 RECOVERED로 내린다.
- Essence 자동 검수가 보류하면 초안에 복구 사이클과 시각을 남기고 24h × 2^(cycle-1) 백오프로 최대 4회 자동 재검수한다. 사람이 손댄 초안은 자동 재시도하지 않는다. 72시간 넘게 ERROR인 필수 자료는 상태를 바꾸지 않고 이번 합성 입력에서만 제외하며 그 사실을 gap에 남긴다. Essence 합성·검수는 `essence` 비용 카테고리를 쓴다.
- 비용 예약 영수증과 공급자 HTTP attempt 원장을 분리한다. usage 누락은 0으로 바꾸지 않고 DB 실패 시 bounded Redis spool로 복구한다. Redis 비용 가드와 single-flight는 장애 시 fail-open이므로 절대 지출 상한이라고 설명하지 않는다.
- 무료 진단 single-flight는 동일 cache key의 중복 구매만 프로세스 간 합친다. provider semaphore는 프로세스 내부 한도이며 같은 API key의 전역 동시 호출 상한이 아니다.
- 공개 commit 뒤 IndexNow 제출은 durable intent와 제한된 재시도로 처리한다. 정상 제출·재시도 성공·usage spool 복구를 Slack 알림으로 만들지 않는다.
- Slack은 [알림 정책](docs/ops/slack-notification-policy.md)을 따른다. 정상 발행 알림을 다시 추가하지 않는다. outbox 실패가 도메인 트랜잭션을 되돌리지 않게 한다.
- `#mkt-reputation`의 모든 메시지는 fallback text와 Block Kit header의 첫 토큰으로 `[Lead : 도입 문의]`·`[Error : 오류 발생]`·`[Report : 운영 현황 보고]` 중 하나를 쓴다. 문구와 종류→라벨 대응은 `services/notification_labels.py` 한 곳에 두고 라벨 문자열을 다른 파일에 복사하지 않는다. 라벨은 기존 문장 앞에만 붙고 버튼·`OPS-` 참조·개발팀 안내를 지우지 않으며, 자동 복구 사실(`INCIDENT_RECOVERED`)은 Error가 아니라 Report다. 라벨을 중복 억제·채널 라우팅·발행 게이트 판정에 쓰지 않는다.

## 검증과 배포

변경 범위에 맞는 테스트·타입·lint·계약 검사를 수행한다. 기본 진입점은 `make test-backend-local`, `make test-frontend`, `make copy-guard`, `make db-budget-guard`다. 통합 테스트의 DB/Redis/PDF 의존성과 skip 여부를 함께 기록한다. 테스트 DB·Redis URL에는 기본값이 없고 테스트는 `backend/.env`를 읽지 않는다 — 전용 테스트 URL뿐 아니라 앱 자체의 `DATABASE_URL`·`SYNC_DATABASE_URL`·`REDIS_URL`도 같다. 변수가 비어 있으면 그 변수를 쓰는 테스트는 변수 이름을 밝힌 실패로 끝나며, 앱 코드가 연결 오류를 삼키는 경로도 연결 불가능한 표지 호스트 가드(`tests/conftest.py`)가 실패시킨다. 값이 있는데 DB에 접속하지 못해도 실패이고, DB URL은 `_test`로 끝나는 DB(또는 `db_env`의 전용 DB)만 허용한다. 그러니 README의 목록(CI backend 잡 env와 같다)을 모두 export하고 DB·Redis를 띄운다. 새 DB/Redis 테스트도 `tests/db_env.py`의 `require_db_url`·`require_redis_url`로 URL을 읽고, 접속 실패는 skip 대신 `fail_unreachable`로 끝낸다. DB/Redis 테스트 모듈의 skip·xfail과 로컬 포트 기본값은 `test_no_skip_in_db_tests.py`·`test_no_default_test_db_port.py`가 막는다. `make test`의 컨테이너 경로 제약, `make setup`의 기존 `.env` 덮어쓰기 동작에 유의한다.

배포는 [현재 배포 안내](docs/ops/deployment-runbook.md)를 따른다. 병원별 헬스는 HTTP 200만 보지 않고 hospital ID·canonical host·현재 리비전까지 확인한다. 배포 후 페이지·sitemap·llms·콘텐츠·이미지·큐 canary·스키마를 변경 범위에 맞게 확인한다. 문서만 변경했다면 런타임이 바뀐 것처럼 새 배포 성공을 주장하지 않는다.

## 문서 유지 규칙

현재 안내에는 문서 버전·갱신일·코드 기준을 남긴다. 수정 시 실제 서비스/Worker/API/Public 경로를 따라가고 코드 주석도 검증한다. 과거 PRD·계획·검수 기록은 그 시점의 기록으로 보존한다. 사용자 지시가 이 안내보다 우선한다.
