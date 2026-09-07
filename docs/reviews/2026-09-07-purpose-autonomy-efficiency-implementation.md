# 목적·자율 운영·비용 효율 감사 후속 구현 기록

문서 버전: **1.6** · 갱신일: **2026-09-07 (Asia/Seoul)**
소스 기준선: **`39dc1f8a98abe9193a8e2202395d2272c370fe8e`**
구현 릴리스: **`eb555180752a66753917f837419498bdcdce5606`**
구현 상태: **merge·빌드와 운영 migration·Worker·FAQ 수리까지 완료. 외부 본문 검수·이미지 인증 및 나머지 배포 단계 대기**

이 문서는 [원본 감사](2026-09-07-purpose-autonomy-efficiency-audit.md)의 A01~A17을 없었던 문제처럼 덮어쓰지 않고, 후속 구현과 검증 증거를 연결한다. `0069` 최초 공개 이력과 이미지 URL cache key를 포함한 PR #82는 merge commit `eb555180752a66753917f837419498bdcdce5606`으로 합쳐졌고 로컬 전체 검증과 독립 코드·구조 리뷰를 통과했다. 같은 소스로 Backend/Site/Admin 컨테이너 이미지 3종을 만들고 운영 migration·Worker·FAQ 수리까지 진행했다. 이후 상태와 digest는 [부분 전환 기록](../releases/2026-09-07-eb55518-partial.md)에 고정한다. 이번 릴리스용 본문·이미지 공급자 검수 Job과 나머지 서비스 배포·공개 표면 검증은 아직 완료되지 않았다.

## 구현 종결표

| 항목 | 작업 트리에서 확인한 구현 | 현재 판정 | 최종 종결에 필요한 증거 |
|---|---|---|---|
| A01 콘텐츠 독립 검수 | HARD/SOFT/UNCERTAIN finding, 전체 공개 후보 hash·필드별 coverage, 수정 후보 재검수, blocking finding 발행 차단. 운영 preflight는 미해결 22건(REVISE 19, UNAVAILABLE 3)과 AI 검수 레거시 기준 허용 93건(ABSENT 92, PASS 1)을 분리했고 exact allowlist 재검수 도구를 추가했다 | **FAQ 수리 완료·이번 일회성 운영 backfill 외부 공급자 호출의 사용자 승인 대기** | 승인 뒤 exact 22건 독립 재검수를 종결한다. 본문·이미지 보완 후 공개 API 전환 전에 exact 115 ID를 새 엄격 공개 gate로 검증 |
| A02 Essence·brief·치료 서사 | 생성 당시/최근 재검사 Essence ID 분리, brief의 philosophy·source snapshot, 치료별 환자 설명·주의·근거를 writer/reviewer에 공통 전달 | 로컬 검증 통과·운영 배포 대기 | 운영 배포 뒤 현재 Essence·source snapshot 기반 생성과 재검사 관측 |
| A03 늦은 생성 결과·발행 경합 | `content_revision`·`generation_claim_token` 조건부 저장, 이미지 내용/주제 hash·정책 버전 인증, 발행 잠금 안 현재 예정일 재확인. `first_published_at`·`first_published_by`를 최초 공개 때 한 번만 기록해 반려·근거 철회·재발행과 현재 판 `published_at`·`published_by`를 분리. 레거시 FAQ 3건은 exact old question·revision·후보 hash plan을 한 트랜잭션 CAS로 물음표만 수정하고 검수 대기로 남김 | **로컬 검증·운영 FAQ CAS 완료** | 운영 exact 3건 apply 후 즉시 replay 변경 0건, 세 글 `DEFERRED` 확인 완료. 이 3건은 ABSENT 레거시이며 22건 유료 재검수 allowlist에 추가하지 않음 |
| A04 공통 비용·킬스위치 | 자료 배치 경계 예약·정산, 무료 진단 cached/live 공통 비용 문맥, 0건 예약도 kill switch 선검사, 비용 차단 due time 영속화 | 로컬 검증 통과·운영 배포 대기 | 운영 비용 원장·차단·KST reset 후 재개 관측 |
| A05 환경 회복 재개 | 입력 변경/환경 회복/운영자 판단 오류 분류, 01·04·07·23시 sweep이 읽는 due time·시도 예산, 동일 사건 알림 억제 | 로컬 검증 통과·운영 배포 대기 | 운영 sweep·일시 오류 복구·최종 오류 중복 억제 관측 |
| A06 고정 반복 표본 | 월간/V0 `MeasurementObservationSlot`, 답변·판정 상태와 lease, COMPLETE/LIMITED/UNAVAILABLE 적정성, legacy lineage 구분 | 로컬 검증 통과·운영 배포 대기 | 운영 고정 슬롯 생성·부분 실패 복구와 API·PDF 상태 관측 |
| A07 V0 전달 증빙 | V0 원장용 PDF artifact의 실제 hash·크기·검증 메타데이터와 전달 게이트·이력 연결 | 로컬 검증 통과·운영 배포 대기 | 운영 V0 artifact 생성·검증·전달 이력 관측 |
| A08 전체 자료 자동 drain | 전체 대상 snapshot·cursor를 가진 `SOURCE_EVIDENCE_PROCESSING` run, broker/worker 유실 회수, 일반 등록·수정 자동 시작, 같은 값 PATCH no-op | 로컬 검증 통과·운영 배포 대기 | 운영 orphan 회수는 15분마다 병원 200곳씩 순환하므로 전체 순회 시간 기록 |
| A09 실제 사람 업무 판정 | 실행 상태·deadline을 반영한 `requires_operator_action`, 조회 실패와 POST 성공 분리, 자동 갱신·병원 필터 보존, 한국어 운영 문구 정리 | 로컬 검증 통과·운영 배포 대기 | 배포 후 운영센터 actionability·deep link·오류 경계 확인 |
| A10 검색 신호 진단 | 검색 미사용/미확정/검색 후 무출처/타 출처/병원 소유 출처를 분리하고 target별 최신 동일 정책 표본을 사용, 최소 비교 표본·더 늦은 관측 없이 생성·자동 완료 금지 | 로컬 검증 통과·운영 배포 대기 | 배포 뒤 실제 관측 cohort와 개선 후 자동 완료 확인 |
| A11 답변·판정 체크포인트 | 유료 월간/V0 고정 슬롯과 무료 진단 결과를 답변 직후 저장, 판정 입력 fingerprint, 답변 재구매 없이 판정 재개 | 로컬 검증 통과·운영 배포 대기 | 운영 호출의 답변·판정 checkpoint와 재개 비용 관측 |
| A12 이미지 단계 분리 | 신규 생성의 생성 바이트·정책 검수·업로드 재시도 분리와 내용/주제/정책 인증, 운영 115건의 실제 바이트 재검수·불변 사본·CAS writeback·안전하지 않은 이미지 교체 Job/CLI. 공개 GCS 이미지 프록시는 인증 내용 hash를 URL 버전으로 사용해 교체된 바이트의 Site 이미지 캐시 key도 바꿈 | **read-only dry-run 완료·이번 일회성 운영 backfill 외부 공급자 호출의 사용자 승인 대기·API 배포 차단** | exact 115건 모두 remaining임을 부작용 없이 확인. 승인 뒤 실제 바이트 인증·불변 URL 또는 안전한 교체를 종결하고 교체 전후 프록시 `?v=`와 인증 hash 일치 확인 |
| A13 실제 공급자 사용량 원장 | provider/model/workflow/소유자/run/item/logical call/HTTP attempt, cache·token·search·image 단위, unknown 구분, DB 실패 Redis spool·1분 복구, 멱등 예약 영수증 | 로컬 검증 통과·운영 배포 대기 | 운영 provider usage 원장·spool drain·월 경계 정산 관측 |
| A14 긴 자료·많은 근거 | 24,000자 단위 중첩 분할과 전체 range coverage, 중복 제거, 80개 단위 전체 근거 분할 독립 검수 | 로컬 검증 통과·운영 배포 대기 | 운영 장문·대량 근거 처리의 종결 run 관측 |
| A15 무료 진단 동시 호출 | 동일 cache identity는 Redis single-flight lease·인계로 프로세스 간 합치고 live/cached를 lead에 귀속. provider별 semaphore는 프로세스 내부에서만 동작 | **로컬 검증 통과·WATCH: 전역 API key 상한 없음** | 여러 인스턴스·서로 다른 cache key의 같은 API key 호출량을 운영 관측한 뒤 전역 제한 필요성 결정 |
| A16 검색 표면·IndexNow | llms.txt의 404/503·no-store 구분, sitemap 원자 조립과 transient 실패, host·URL·revision별 durable IndexNow intent·1분 bounded retry | 로컬 검증 통과·운영 배포 대기 | 공개 host별 sitemap·llms.txt와 IndexNow drain 관측 |
| A17 외부 호출·제어 용량 | 자료 claim/조건부 저장 사이 외부 호출을 DB lock 밖으로 이동, 공유 Worker의 `control` 우선 큐·priority, queue wait 구조화 관측, 7개 큐 canary | **로컬 검증 통과·WATCH: 공유 용량** | 측정 부하 중 control queue wait·deadline과 DB 연결을 운영 관측. 15분 회수는 200병원 순환 주기이며 전체 완료 SLA가 아님 |

## 구현 묶음과 정본

- `0065_provider_usage`: 공급자 HTTP 시도 원장과 best-effort 복구의 DB 계약.
- `0066_content_contracts`: 콘텐츠 revision·claim, 생성/재검사 provenance, 이미지 인증 계약.
- `0067_measurement_slots`: 월간/V0 반복 슬롯과 답변·판정 체크포인트 계약.
- `0068_lead_cost_deferral`: 무료 진단 비용 차단 재개 시각과 외부 호출 없는 claim 시도 횟수 복원 계약.
- `0069_content_first_publication`: 현재 공개 판의 `published_at`·`published_by`와 한 번만 기록하는 최초 공개 사실을 분리한다. 남아 있는 발행 값만 백필하며 과거 반려가 이미 지운 값은 추정하지 않는다.
- [FAQ 구두점 복구](../../backend/app/utils/repair_legacy_faq_questions.py): exact 3건의 질문 끝 물음표만 revision·후보 hash plan과 CAS로 수정하고 독립 검수를 대기로 남긴다.
- [기존 공개 본문 재검수](../../backend/app/services/content_public_review_backfill.py): exact allowlist만 제한된 batch로 재검수하고 후보·brief·현재 Essence·source snapshot·revision이 바뀌면 저장하지 않는다. 운영 exact 22건 allowlist까지 고정했고 실제 공급자 호출은 사용자 승인 대기다.
- [기존 이미지 인증](../../backend/app/services/content_image_certification.py): exact manifest의 실제 바이트 검수·불변 사본·CAS 저장과 안전하지 않은 이미지 교체를 수행한다. 운영 read-only dry-run에서 exact 115건이 모두 remaining임을 확인했고 실제 공급자 호출과 writeback은 사용자 승인 대기다.
- 세 배포 전 Job/CLI는 새 schema를 요구하지 않는다. 이번 변경의 expected head는 `0069_content_first_publication`이다.
- [현재 시스템 구조](../architecture/system-map.md): 위 계약이 생성·발행·측정·운영 흐름에 들어가는 위치.
- [배포 안내](../ops/deployment-runbook.md): 마이그레이션, 7개 큐, RedBeat `2026-09-07.2`, canary와 공개 표면 검증.
- [마케터 운영 안내](../ops/marketer-operations-runbook.md): 자동 복구와 사람이 실제로 판단할 일을 구분한 운영 절차.
- [알림 정책](../ops/slack-notification-policy.md): 정상 처리·자동 복구·IndexNow·usage spool은 조용히 기록하고 최종 행동 가능 예외만 전달하는 계약.

## 최종 검증 기록

로컬 검증 증거와 운영 실행 증거를 분리한다. 통과 표시는 동결된 작업 트리의 로컬 결과이며, 대기·배포 차단 표시는 실제 운영 실행이 남았다는 뜻이다.

| 증거 | 상태 | 결과 |
|---|---|---|
| Backend 전체 단위·통합 테스트 | **통과** | 3,084 passed, 0 failed, 0 skipped, 19 warnings, coverage 83.06%, 98.20초. `0069`·이미지 URL cache key, 실제 PostgreSQL·Redis·native PDF, fresh schema와 populated `0064` → `0069` upgrade 포함 |
| Linux CI | **통과** | 런타임 merge SHA는 `eb55518`, CI 설정 전용 SHA는 `7446762`. [PR #83 CI](https://github.com/wjlee930501/reputation/actions/runs/34117637771) 9/9 통과. Backend 3,084 passed, 0 skipped, 20 warnings, coverage 83.07%, 238.08초. PR #82에서 환경 별칭 때문에 skip된 TASK22 PostgreSQL 검사를 실행하도록 CI 설정만 교정 |
| Admin test·lint·typecheck·production build | **CI 통과** | `eb55518` 런타임 소스 tests·typecheck·lint·production build 통과. 로컬 전체 536/536 뒤 마지막 raw fallback 수정의 관련 19 tests·typecheck도 통과 |
| Site test·lint·typecheck·production build | **CI 통과** | `eb55518` 런타임 소스 tests·typecheck·lint·production build 통과. 로컬 310 tests·typecheck·lint 통과 |
| Ruff·diff·copy guard·DB connection budget guard | **통과** | Ruff, diff check, copy guard 통과. DB 연결 예산 75/80 |
| 독립 코드·구조 리뷰 | **통과** | code APPROVE, architecture CLEAR, P0/P1 0건. 마지막 최초 공개 이력·이미지 cache key 좁은 결합 리뷰도 APPROVE |
| Alembic upgrade 및 최종 current head | **운영 완료** | release image로 migration 실행 성공, 운영 DB current head `0069_content_first_publication` 확인 |
| RedBeat schedule 재조정 | 로컬 계약 통과·운영 대기 | schedule version `2026-09-07.2`. IndexNow retry와 provider usage spool drain을 포함한 영속 schedule 운영 재조정 대기 |
| 7개 queue canary·서명 dispatch·시간 제한 | **Worker만 배포·관측 대기** | `reputation-worker-00141-wgm` 100% 전환. RedBeat·Beat·readiness와 현재 release의 queue canary·queue wait 확인 대기 |
| 실제 배포 Git SHA·image digest·Cloud Run revision | **부분 완료** | `eb55518` Backend/Site/Admin 컨테이너 이미지 3종 Cloud Build 성공. Backend digest로 Worker만 전환했고 API·Beat·Site·Admin은 기존 `345a642` revision에서 각각 100% 트래픽 유지 |
| sitemap·llms.txt·공개 콘텐츠·이미지·IndexNow 복구 관측 | **전환 전 기준 확인** | 구 API 기준 7개 tenant·공개 글 115건 ID 보존과 tenant별 대표 이미지 7건 HTTP 성공 확인. 새 API/Site 결과는 아님 |
| provider usage 원장·spool·비용 영수증 운영 관측 | 대기 | — |
| Slack 정상 무알림·최종 차단 중복 억제 | 대기 | — |
| 운영 preflight baseline | **read-only 확인·FAQ mutation 완료** | 공개 115건. AI 검수 미해결 22건(REVISE 19, UNAVAILABLE 3), 레거시 기준 허용 93건(ABSENT 92, PASS 1), FAQ 구두점 대상 3건, 이미지 결합 인증 없음 109건·검수 시각만 있음 6건. FAQ만 CAS 적용했고 본문·이미지 공급자 호출과 writeback은 대기. ID는 문서에 나열하지 않음 |
| FAQ 구두점 exact repair | **완료** | exact 3건 CAS apply, 즉시 replay 변경 0건. 상태는 `DEFERRED`이나 ABSENT 레거시 3건을 본문 22건 유료 재검수 allowlist에 추가하지 않으며, 본문·이미지 보완 뒤 exact 115 새 엄격 공개 gate에서 확인 |
| 공개 본문 독립 재검수 | **외부 호출 미실행·배포 차단** | exact 22건 allowlist 고정, 레거시 ABSENT 92건·PASS 1건 제외. Anthropic에 병원·본문·공식 자료 맥락을 보내는 도구 호출이 자동 승인 검토에서 구체적 동의 부족으로 거부돼 사용자 승인을 기다림 |
| 운영 기존 이미지 인증 backfill | **read-only dry-run 완료·외부 호출 미실행·배포 차단** | exact 115건 candidate, current 0, remaining 115, side effect 0. 실제 이미지 바이트와 글 제목(주제)을 Google에 보내는 검수 Job은 명시적 실행 승인을 기다리며 인증·불변 사본·교체·CAS 저장은 미실행 |
| 새 엄격 공개 gate | **배포 차단** | 이미지 검수 시각 전용 우회 없이 exact baseline ID 집합의 추가·누락·상태 drift가 0건이고 read-only 결과가 수리 결과와 일치하는지 확인 대기 |

## 남은 판단

A10의 target별 최신 동일 정책 cohort, 검색 상태·인용 소유권 분류, 최소 비교 표본과 더 늦은 개선 관측 완료 조건은 로컬 검증을 통과했다. 운영 측정 자료에서도 같은 경계가 유지되는지는 배포 뒤 관측한다. A01~A17의 로컬 검증은 완료했지만 운영 전환 표의 차단 항목을 끝내기 전에는 전체 릴리스 종결을 선언하지 않는다.

**WATCH — 이후 프로필 변경과 기존 본문:** reviewer 증거는 검수 시점의 전체 후보와 입력에 묶이고 공급자 호출 중에는 profile·revision CAS가 늦은 저장을 막는다. 그러나 이후 [병원 프로필 PATCH](../../backend/app/api/admin/hospitals.py#L692-L889)로 주소·전화·운영시간·진료 항목 등이 바뀌면 현재 경로는 감사 기록과 사이트 캐시 무효화·재검증만 수행하며, 기존 본문 안의 병원 사실 claim을 찾아 claim-aware 재인증하지 않는다. [공개 후보 검수 판정](../../backend/app/services/content_publication.py#L128-L131)도 현재 후보에 저장된 검수 상태를 확인할 뿐 지속적인 병원 사실 인증서는 아니다. ABSENT 92건과 PASS 1건을 레거시라는 이유만으로 일괄 재검수하지 않는 결정과 별개로, 후속 개선은 프로필 claim 의존성을 기록하고 영향받은 글만 bounded 재검수해야 한다. 이번 릴리스에서는 프로필 필드 하나가 바뀔 때 공개 글 전체를 숨기거나 다시 쓰지 않는다. 이는 기존 한계에 대한 WATCH이며 A01의 새 배포 차단 조건이 아니다.

또한 공급자 사용량 원장은 업무 성공과 분리된 best-effort 관측이다. DB 저장 실패 시 Redis spool이 복구하지만 DB와 Redis가 동시에 실패하면 누락될 수 있다. single-flight와 비용 가드는 Redis 장애 때 fail-open이다. single-flight는 같은 cache key만 프로세스 간 합치며 provider semaphore는 프로세스 내부 한도라 같은 API key의 전역 동시 호출을 제한하지 않는다. `control`은 우선 큐이며 전용 Worker 용량이 아니므로 이미 실행 중인 긴 작업을 선점하지 않는다. 이 한계들은 운영 관측 없이 절대 비용 상한이나 deadline 보장으로 설명하지 않는다.

운영 데이터 점검에서 공개 글 115건 중 기존 이미지 109건에 새 결합 인증이 없고 6건은 기존 검수 시각만 있는 것을 확인했다. read-only dry-run은 exact 115건 candidate, current 0, remaining 115이고 부작용은 없었다. 영구적인 레거시 우회나 합성 인증값을 넣지 않는다. 승인 뒤 일회성 Job/CLI로 115건 모두의 실제 저장 바이트를 정책 재검수하고 content-addressed 불변 사본으로 옮기거나 안전한 이미지로 교체한 뒤 현재 revision·claim·URL을 비교하여 저장한다. 공개 GCS 이미지 프록시는 인증된 내용 hash를 `?v=`로 포함한다. Site의 이미지 최적화 최소 캐시 TTL이 86,400초이므로 이미지 교체 뒤 이 hash와 URL cache key가 함께 바뀌는지 검증한다. 기존 검수 시각 호환은 복구 중인 구 API 전환 구간에만 허용하고 새 API에는 남기지 않는다. 비공개 기존 행도 일반 생성·발행의 엄격한 gate를 따른다. 이 작업의 종결 증거가 남을 때까지 API 배포는 차단한다.

staged 실행 순서 중 migration `0069` → Worker → FAQ exact 3건 dry-run·CAS apply는 완료됐다. 다음 단계는 공개 본문 exact 22건 독립 재검수 → 이미지 exact 115건 실제 바이트 인증·불변 복사·필요 시 교체 → 레거시 이미지 시각 우회가 없는 새 엄격 공개 gate의 exact baseline ID read-only 검사 → RedBeat `2026-09-07.2` 재조정·Beat·readiness → API·Site·Admin이다. 외부 호출 승인은 실행 도구가 외부 공급자에 운영 맥락을 전송하는 데 필요한 세션 권한이며 서비스의 콘텐츠 승인 상태나 새 제품 HITL 단계가 아니다. 승인 전에는 이번 릴리스용 본문 22건·이미지 115건 검수 Job의 공급자 호출이 실행되지 않았다. 구 API의 시각 기반 허용은 이미지 복구 중 전환 구간에만 사용하고 새 API에는 남기지 않는다. 상세 ID와 단계별 JSON·로그는 추적하지 않는 `/private/tmp/reputation-autonomy-release` 아래에 두고 이 문서에는 집계와 최종 판정만 남긴다.
