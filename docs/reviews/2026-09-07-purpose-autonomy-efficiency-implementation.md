# 목적·자율 운영·비용 효율 감사 후속 구현 기록

문서 버전: **2.1** · 데이터 스냅샷: **2026-09-07 23:58 (Asia/Seoul)**
운영 전환 갱신: **2026-09-08 01:35 (Asia/Seoul)**
소스 기준선: **`39dc1f8a98abe9193a8e2202395d2272c370fe8e`**
초기 부분 배포 런타임: **`eb555180752a66753917f837419498bdcdce5606`**
검증된 부모 소스: **`15845f73d724e444f7112ea47d619408330453bb` — 전체 Backend 검증, batch 06 실행**
운영 데이터 검수 소스: **`02b5d6b003e9358267c97505aa81ae1576d7b2b2` — 장면 매핑 v3, 115건 검수·배포 전 gate 실행**
최종 runtime 소스: **`ede3d8f5a8adec849c987d21c1491afef03edbca`**
배포 코드 병합 소스(PR #86): **`a48ff5684947ede081f3874ba03424cc1278794e` — runtime과 전체 tree 동일**
구현 상태: **PR #86 병합·5개 서비스·3개 영속 Job·readiness·7개 queue·공개 115건 검증·임시 Job 정리 완료. V0 진단은 정상 비동기 실행 중**

이 문서는 [원본 감사](2026-09-07-purpose-autonomy-efficiency-audit.md)의 A01~A17을 없었던 문제처럼 덮어쓰지 않고, 후속 구현과 검증 증거를 연결한다. 운영 콘텐츠 검수는 `02b5d6b003e9358267c97505aa81ae1576d7b2b2`에서 끝났고, readiness 독립 실행 import를 보완한 `38d6e2c3e5341b51de931a4c0261aa00974d2b52`를 거쳐 V0 실제 실행 오류를 고친 `ede3d8f5a8adec849c987d21c1491afef03edbca`를 최종 배포했다. PR #86 merge `a48ff5684947ede081f3874ba03424cc1278794e`와 runtime tree는 동일하다. 상세 단계는 [운영 전환 기록](../releases/2026-09-07-eb55518-partial.md)에 고정한다.

## 구현 종결표

| 항목 | 작업 트리에서 확인한 구현 | 현재 판정 | 최종 종결에 필요한 증거 |
|---|---|---|---|
| A01 콘텐츠 독립 검수 | HARD/SOFT/UNCERTAIN finding, 전체 공개 후보 hash·필드별 coverage, 수정 후보 재검수, blocking finding 발행 차단. 운영 preflight의 미해결 22건(REVISE 19, UNAVAILABLE 3)을 exact allowlist로 재검수했다 | **운영 본문 22건·공개 gate 종결** | 20건은 수정 없이 검수 기준 충족, 2건은 최소 수정 후 실제 공개 검수 통과. 최종 공개 기준 7 tenant·115건 모두 public safe, missing·미인증·검수 미해결 0 |
| A02 Essence·brief·치료 서사 | 생성 당시/최근 재검사 Essence ID 분리, brief의 philosophy·source snapshot, 치료별 환자 설명·주의·근거를 writer/reviewer에 공통 전달 | 운영 배포 완료·장기 관측 대기 | 현재 Essence·source snapshot 기반의 이후 생성과 재검사 관측 |
| A03 늦은 생성 결과·발행 경합 | `content_revision`·`generation_claim_token` 조건부 저장, 이미지 내용/주제 hash·정책 버전 인증, 발행 잠금 안 현재 예정일 재확인. `first_published_at`·`first_published_by`를 최초 공개 때 한 번만 기록해 반려·근거 철회·재발행과 현재 판 `published_at`·`published_by`를 분리. 레거시 FAQ 3건은 exact old question·revision·후보 hash plan을 한 트랜잭션 CAS로 물음표만 수정하고 검수 대기로 남김 | **로컬 검증·운영 FAQ CAS 완료** | 운영 exact 3건 apply 후 즉시 replay 변경 0건, 세 글 `DEFERRED` 확인 완료. 이 3건은 ABSENT 레거시이며 22건 유료 재검수 allowlist에 추가하지 않음 |
| A04 공통 비용·킬스위치 | 자료 배치 경계 예약·정산, 무료 진단 cached/live 공통 비용 문맥, 0건 예약도 kill switch 선검사, 비용 차단 due time 영속화 | **로컬 검증 통과·일일 한도 자동 복귀 확인** | 2026-09-07 최종 AVAILABLE 일 289/350·월 362/3,000·kill false. 2026-09-08 00:01:36 KST 새 날짜에서 AVAILABLE 일 0/250·월 362/3,000·kill false로 기본값 자동 복귀 확인 |
| A05 환경 회복 재개 | 입력 변경/환경 회복/운영자 판단 오류 분류, 01·04·07·23시 sweep이 읽는 due time·시도 예산, 동일 사건 알림 억제 | 운영 배포 완료·장기 관측 대기 | 운영 sweep·일시 오류 복구·최종 오류 중복 억제 관측 |
| A06 고정 반복 표본 | 월간/V0 `MeasurementObservationSlot`, 답변·판정 상태와 lease, COMPLETE/LIMITED/UNAVAILABLE 적정성, legacy lineage 구분 | 운영 배포 완료·V0 복구 관측 중 | 고정 슬롯의 실제 V0 run 종결과 이후 부분 실패 복구 관측 |
| A07 V0 전달 증빙 | V0 원장용 PDF artifact의 실제 hash·크기·검증 메타데이터와 전달 게이트·이력 연결 | 운영 배포 완료·V0 복구 관측 중 | 현재 RUNNING인 V0의 artifact 생성·검증·전달 이력 종결 |
| A08 전체 자료 자동 drain | 전체 대상 snapshot·cursor를 가진 `SOURCE_EVIDENCE_PROCESSING` run, broker/worker 유실 회수, 일반 등록·수정 자동 시작, 같은 값 PATCH no-op | 운영 배포 완료·장기 관측 대기 | orphan 회수는 15분마다 병원 200곳씩 순환하므로 전체 순회 시간 기록 |
| A09 실제 사람 업무 판정 | 실행 상태·deadline을 반영한 `requires_operator_action`, 조회 실패와 POST 성공 분리, 자동 갱신·병원 필터 보존, 한국어 운영 문구 정리 | 운영 배포·기본 smoke 통과 | 운영센터 actionability·deep link·오류 경계의 장기 관측 |
| A10 검색 신호 진단 | 검색 미사용/미확정/검색 후 무출처/타 출처/병원 소유 출처를 분리하고 target별 최신 동일 정책 표본을 사용, 최소 비교 표본·더 늦은 관측 없이 생성·자동 완료 금지 | 운영 배포 완료·장기 관측 대기 | 실제 관측 cohort와 개선 후 자동 완료 확인 |
| A11 답변·판정 체크포인트 | 유료 월간/V0 고정 슬롯과 무료 진단 결과를 답변 직후 저장, 판정 입력 fingerprint, 답변 재구매 없이 판정 재개 | 운영 배포 완료·V0 복구 관측 중 | 현재 V0 run의 답변·판정 checkpoint와 종결 결과 확인 |
| A12 이미지 단계 분리 | 신규 생성의 생성 바이트·정책 검수·업로드 재시도 분리와 내용/주제/정책 인증, 운영 115건의 실제 바이트 재검수·불변 사본·CAS writeback·안전하지 않은 이미지 교체 Job/CLI. 공개 GCS 이미지 프록시는 인증 내용 hash를 URL 버전으로 사용해 교체된 바이트의 Site 이미지 캐시 key도 바꿈. 장면 매핑 v3에서도 image policy v2와 일반 attempt cap 2는 유지 | **운영 115/115 인증·배포 후 바이트 확인** | 기존 바이트 유지 79건, 안전한 이미지 교체 36건. 공개 이미지 115건 모두 HTTP 200이며 실제 바이트 SHA-256과 URL 인증 hash 일치 |
| A13 실제 공급자 사용량 원장 | provider/model/workflow/소유자/run/item/logical call/HTTP attempt, cache·token·search·image 단위, unknown 구분, DB 실패 Redis spool·1분 복구, 멱등 예약 영수증 | 운영 배포 완료·장기 관측 대기 | 운영 provider usage 원장·spool drain·월 경계 정산 관측 |
| A14 긴 자료·많은 근거 | 24,000자 단위 중첩 분할과 전체 range coverage, 중복 제거, 80개 단위 전체 근거 분할 독립 검수 | 운영 배포 완료·장기 관측 대기 | 운영 장문·대량 근거 처리의 종결 run 관측 |
| A15 무료 진단 동시 호출 | 동일 cache identity는 Redis single-flight lease·인계로 프로세스 간 합치고 live/cached를 lead에 귀속. provider별 semaphore는 프로세스 내부에서만 동작 | **로컬 검증 통과·WATCH: 전역 API key 상한 없음** | 여러 인스턴스·서로 다른 cache key의 같은 API key 호출량을 운영 관측한 뒤 전역 제한 필요성 결정 |
| A16 검색 표면·IndexNow | llms.txt의 404/503·no-store 구분, sitemap 원자 조립과 transient 실패, host·URL·revision별 durable IndexNow intent·1분 bounded retry | 공개 표면 smoke 통과·회복 관측 대기 | 7 tenant의 canonical·sitemap·llms.txt 확인. IndexNow drain 장기 관측 |
| A17 외부 호출·제어 용량 | 자료 claim/조건부 저장 사이 외부 호출을 DB lock 밖으로 이동, 공유 Worker의 `control` 우선 큐·priority, queue wait 구조화 관측, 7개 큐 canary | **운영 readiness·7 queue 통과·WATCH: 공유 용량** | 7개 서명 dispatch와 Worker 성공 확인. 14개 wait 표본 2.269~4.940초는 관측 범위이며 SLA가 아님. 측정 부하 중 queue wait·deadline은 계속 관측 |

## 구현 묶음과 정본

- `0065_provider_usage`: 공급자 HTTP 시도 원장과 best-effort 복구의 DB 계약.
- `0066_content_contracts`: 콘텐츠 revision·claim, 생성/재검사 provenance, 이미지 인증 계약.
- `0067_measurement_slots`: 월간/V0 반복 슬롯과 답변·판정 체크포인트 계약.
- `0068_lead_cost_deferral`: 무료 진단 비용 차단 재개 시각과 외부 호출 없는 claim 시도 횟수 복원 계약.
- `0069_content_first_publication`: 현재 공개 판의 `published_at`·`published_by`와 한 번만 기록하는 최초 공개 사실을 분리한다. 남아 있는 발행 값만 백필하며 과거 반려가 이미 지운 값은 추정하지 않는다.
- [FAQ 구두점 복구](../../backend/app/utils/repair_legacy_faq_questions.py): exact 3건의 질문 끝 물음표만 revision·후보 hash plan과 CAS로 수정하고 독립 검수를 대기로 남긴다.
- [기존 공개 본문 재검수](../../backend/app/services/content_public_review_backfill.py): exact allowlist만 제한된 batch로 재검수하고 후보·brief·현재 Essence·source snapshot·revision이 바뀌면 저장하지 않는다. 운영 22건 검수와 2건 최소 수정·재검수를 종결했고 replay는 2건 current, repaired 0, provider 호출 0이었다.
- [기존 이미지 인증](../../backend/app/services/content_image_certification.py): exact manifest의 실제 바이트 검수·불변 사본·CAS 저장과 안전하지 않은 이미지 교체를 수행한다. 23:58 현재 115건 모두 current이고 기존 바이트 79건을 유지했으며 36건을 교체했다.
- 세 배포 전 Job/CLI는 새 schema를 요구하지 않는다. 이번 변경의 expected head는 `0069_content_first_publication`이다.
- [현재 시스템 구조](../architecture/system-map.md): 위 계약이 생성·발행·측정·운영 흐름에 들어가는 위치.
- [배포 안내](../ops/deployment-runbook.md): 마이그레이션, 7개 큐, RedBeat `2026-09-07.2`, canary와 공개 표면 검증.
- [마케터 운영 안내](../ops/marketer-operations-runbook.md): 자동 복구와 사람이 실제로 판단할 일을 구분한 운영 절차.
- [알림 정책](../ops/slack-notification-policy.md): 정상 처리·자동 복구·IndexNow·usage spool은 조용히 기록하고 최종 행동 가능 예외만 전달하는 계약.

## 최종 검증 기록

각 행에서 로컬·CI·운영 증거를 구분한다. 운영 완료는 적힌 배포·검증 범위에 한하며, 장기 관측 대기는 이후 실제 부하와 복구 주기를 더 봐야 한다는 뜻이다.

| 증거 | 상태 | 결과 |
|---|---|---|
| Backend 전체 단위·통합 테스트 | **최종 CI 통과** | 최종 runtime `ede3d8f`, merge `a48ff56` 전체 tree 동일 |
| Linux CI | **통과** | [PR #86 CI](https://github.com/wjlee930501/reputation/actions/runs/34142628859) 9/9 통과. Backend 3,095 passed, 0 failed, 0 skipped, 17 warnings, coverage 83.03%, 237.23초 |
| Admin test·lint·typecheck·production build | **최종 CI·배포 통과** | PR #86 CI와 최종 Admin revision·login smoke 통과 |
| Site test·lint·typecheck·production build | **최종 CI·배포 통과** | PR #86 CI와 최종 Site revision·공개 SSR smoke 통과 |
| Ruff·diff·copy guard·DB connection budget guard | **통과** | Ruff, diff check, copy guard 통과. DB 연결 예산 75/80 |
| 독립 코드·구조 리뷰 | **통과** | 부모 `15845f7` code·architecture CLEAR. 장면 매핑 v3 `02b5d6b`는 집중 unit 35건·PostgreSQL 12건과 독립 APPROVE 통과. 일회성 helper `d6867fa7`도 isolated PostgreSQL 5 tests와 독립 APPROVE를 통과했고 최초 공개 이력·판 cache 계약을 보존 |
| 비용 화면 한국어 명칭 | **집중 검증·private build 통과** | `e32a476`은 `cost_guard.py`의 명칭·docstring만 수정. focused unit/API 49건, isolated Redis 1건, Ruff 통과. 로직·공급자 계약 변경 없음 |
| Alembic upgrade 및 최종 current head | **운영 완료** | 최종 `reputation-migrate-m9lmh` 성공, 운영 DB current head `0069_content_first_publication` 확인 |
| RedBeat schedule 재조정 | **운영 완료** | schedule version `2026-09-07.2` reconcile 성공. IndexNow retry와 provider usage spool drain 포함 |
| readiness 독립 실행 | **수정·운영 완료** | 첫 standalone 검사에서 drain task 두 개의 명시적 import 누락으로 `required_tasks_registered`만 false였다. Worker task는 정상 등록·실행 중이었다. `38d6e2c`에서 import와 fresh subprocess 회귀 검사를 추가했고 최종 16 checks가 모두 true |
| 7개 queue canary·서명 dispatch·시간 제한 | **운영 완료·WATCH 유지** | readiness 16 checks 모두 true. 최종 Beat가 7개 큐에 서명 dispatch하고 Worker가 7건 성공. 14개 wait 표본 2.269~4.940초는 SLA가 아님 |
| 실제 배포 Git SHA·image digest·Cloud Run revision | **운영 완료** | API `00154-9xj`, Worker `00144-dcb`, Beat `00140-pbh`, Site `00109-2xj`, Admin `00074-h7b` 모두 runtime `ede3d8f`, Ready·latest·100% traffic·최종 digest 일치. 3개 영속 Job도 같은 source |
| sitemap·llms.txt·공개 콘텐츠·이미지·IndexNow 복구 관측 | **공개 smoke 통과·장기 관측 대기** | 7/7 tenant HTTP와 canonical·sitemap·robots·llms.txt 통과. 공개 글 115건 누락 0, 이미지 115건 HTTP 200·실제 바이트 SHA-256과 URL 인증 hash 일치. IndexNow 장기 복구 관측은 계속 |
| provider usage 원장·spool·비용 영수증 운영 관측 | **부분 확인** | 본문 26 HTTP·입력 247,213·출력 16,241 토큰에 공개 단가를 적용한 계산값은 $0.328418. 이미지 후보 창은 생성 80 attempts·입력 16,102·출력 89,649, 검수 171 attempts·입력 236,917·출력 7,559이나 배경 호출과 HTTP outcome 미확정이 섞임. 같은 공개 단가 가정의 본문 포함 약 $5.921443은 참고값이며 정확한 릴리스 청구액이나 상한이 아님 |
| Slack 정상 무알림·최종 차단 중복 억제 | **canary 확인·장기 관측 대기** | 최종 7 queue canary는 render dry-run만 수행하고 실제 Slack·provider 호출 0 |
| 운영 preflight baseline | **본문·FAQ·이미지 완료** | 공개 115건 exact 집합 유지. FAQ 3건 CAS 완료, 본문 22건 current·unresolved 0, 이미지 115건 current·remaining 0. ID는 문서에 나열하지 않음 |
| FAQ 구두점 exact repair | **완료** | exact 3건 CAS apply, 즉시 replay 변경 0건. 상태는 `DEFERRED`이나 ABSENT 레거시 3건을 본문 22건 유료 재검수 allowlist에 추가하지 않으며, 본문·이미지 보완 뒤 exact 115 새 엄격 공개 gate에서 확인 |
| 공개 본문 독립 재검수 | **완료** | exact 22건 중 20건은 수정 없이 검수 기준 충족, 2건은 최소 수정 후 공개 검수 통과. 최종 read-only 확인은 current 22, unresolved 0. 총 26 provider HTTP(최초·재시도 24 + 수정 후 검수 2). replay는 current 2, repaired 0, provider 호출 0 |
| 운영 기존 이미지 인증 backfill | **115/115 완료** | 기존 바이트 유지 79건 + 교체 36건. scoped v3 exact plan SHA `e3fcac72041da396c42a0912da9ac983aa830029a11a86df63e1b2c544575eaa`의 5건은 모두 attempt 1에 성공했고 failed·stale·leased·unresolved 0. replay도 current 5, repaired 0, provider/cache 0. helper `b8238…`는 code·architecture APPROVE와 PostgreSQL 9 tests를 통과했고 기존 attempt 2 및 state hash 5/5를 byte-for-byte 보존 |
| 후속 코드 공개·배포 | **완료** | PR #86 merge `a48ff56`, runtime `ede3d8f` tree 동일. 최종 build·5개 서비스·3개 영속 Job 전환 완료 |
| 새 엄격 공개 gate | **완료** | 공개 직전 7 tenant, baseline/current published 115, public safe 115, missing 0, image uncertainty 0, review unresolved 0. 전환 뒤 HTTP·SSR·115개 이미지 바이트까지 별도 확인 |
| V0 실제 실행 복구 | **코드 복구 완료·진단 RUNNING** | source `38d6e2c` 배포 뒤 실제 Worker 실행에서 `ScalarResult` 조회 오류를 발견해 `ede3d8f`로 수정·배포. 2026-09-07T16:32:28Z child RUNNING, attempt 1, error null, 고정 150개 중 18 RECEIVED/CONFIRMED·132 PENDING. replay 추가 dispatch 0. PDF 완료로 해석하지 않음 |
| 임시 Job 정리 | **완료** | 실행 journal의 임시 Job 36개만 대상으로 9개 삭제·27개 already absent·0 skipped. 2026-09-07T16:35:03Z 전체 absent 재확인. 5개 서비스·3개 영속 Job·진행 중 V0 보존 |

## 남은 판단

A10의 target별 최신 동일 정책 cohort, 검색 상태·인용 소유권 분류, 최소 비교 표본과 더 늦은 개선 관측 완료 조건은 검증을 통과했다. 실제 운영 측정 자료에서도 같은 경계가 장기간 유지되는지는 계속 관측한다. 배포와 공개 표면 검증은 완료했지만 A05~A17의 장기 운영 관측까지 끝났다고 확대하지 않는다.

**WATCH — 이후 프로필 변경과 기존 본문:** reviewer 증거는 검수 시점의 전체 후보와 입력에 묶이고 공급자 호출 중에는 profile·revision CAS가 늦은 저장을 막는다. 그러나 이후 [병원 프로필 PATCH](../../backend/app/api/admin/hospitals.py#L692-L889)로 주소·전화·운영시간·진료 항목 등이 바뀌면 현재 경로는 감사 기록과 사이트 캐시 무효화·재검증만 수행하며, 기존 본문 안의 병원 사실 claim을 찾아 claim-aware 재인증하지 않는다. [공개 후보 검수 판정](../../backend/app/services/content_publication.py#L128-L131)도 현재 후보에 저장된 검수 상태를 확인할 뿐 지속적인 병원 사실 인증서는 아니다. ABSENT 92건과 PASS 1건을 레거시라는 이유만으로 일괄 재검수하지 않는 결정과 별개로, 후속 개선은 프로필 claim 의존성을 기록하고 영향받은 글만 bounded 재검수해야 한다. 이번 릴리스에서는 프로필 필드 하나가 바뀔 때 공개 글 전체를 숨기거나 다시 쓰지 않는다. 이는 기존 한계에 대한 WATCH이며 A01의 새 배포 차단 조건이 아니다.

또한 공급자 사용량 원장은 업무 성공과 분리된 best-effort 관측이다. DB 저장 실패 시 Redis spool이 복구하지만 DB와 Redis가 동시에 실패하면 누락될 수 있다. single-flight와 비용 가드는 Redis 장애 때 fail-open이다. single-flight는 같은 cache key만 프로세스 간 합치며 provider semaphore는 프로세스 내부 한도라 같은 API key의 전역 동시 호출을 제한하지 않는다. `control`은 우선 큐이며 전용 Worker 용량이 아니므로 이미 실행 중인 긴 작업을 선점하지 않는다. 이 한계들은 운영 관측 없이 절대 비용 상한이나 deadline 보장으로 설명하지 않는다.

운영 데이터 점검에서 공개 글 115건 중 기존 이미지 109건에 새 결합 인증이 없고 6건은 기존 검수 시각만 있는 것을 확인했다. 실제 저장 바이트 검수와 교체 결과 79건은 기존 바이트를 유지하고 36건은 안전한 이미지로 교체해 115건 모두 current가 됐다. 마지막 scoped v3 5건은 기존 attempt·상태 hash를 바꾸지 않고 모두 첫 시도에 성공했으며 replay에서 추가 호출이나 수정이 없었다. 영구적인 레거시 우회나 합성 인증값을 넣지 않았다. 공개 GCS 이미지 프록시는 인증된 내용 hash를 `?v=`로 포함하며, 이번 배포에서 115건 모두 실제 제공 바이트 SHA-256과 URL 인증 hash가 일치했다. 이후 이미지 교체 때도 Site의 86,400초 최소 캐시 TTL을 고려해 hash와 URL cache key, 실제 바이트를 함께 확인한다. 기존 검수 시각 호환은 새 API에 남기지 않았다. 비공개 기존 행도 일반 생성·발행의 엄격한 gate를 따른다.

staged 실행 순서와 최종 공개 전환은 완료됐다. 최종 5개 서비스 로그 확인 구간 2026-09-07T16:24:50Z~16:31:35Z에는 ERROR가 없었다. V0 코드 복구도 배포됐으며 새 진단 run은 정상 비동기 실행 중이다. 이는 조회 결함 해결과 재개 증거이며 측정·PDF 종결 증거는 아니다. 구 API의 시각 기반 허용은 새 API에 남기지 않았다. 상세 ID와 단계별 JSON·로그는 추적하지 않는 `/private/tmp/reputation-autonomy-release` 아래에 두고 이 문서에는 집계와 최종 판정만 남긴다.
