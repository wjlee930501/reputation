# 무결성 검증·HITL 최소화 검토 — 2026-09-08

검토 기준 커밋: `59acabe` (origin/main, PR #90 병합 직후)
검토 방식: 기존 감사 문서(`2026-09-07-*`, `system-map.md`, `marketer-operations-runbook.md`)를 **검증 대상 주장**으로만 취급하고, 코드에서 file:line 근거를 다시 냈다. 독립 검토자 8개 — Claude Fable 5.1 × 6(생애주기·콘텐츠·측정/리포트·자료/운영기준·admin 횡단·공개 사이트), Codex gpt-5.6-sol high × 2(HITL 인벤토리·백엔드 배선). 서로 다른 모델이 독립적으로 같은 결함에 도달한 항목은 `수렴` 열에 표시한다.

## 1. 테스트 실행 결과 (로컬, CI 동일 env)

| 대상 | 결과 |
|---|---|
| Backend pytest (실 PostgreSQL 5432+5434, Redis, `REQUIRE_PDF_RENDER=1`) | **3,110 passed / 0 failed** (PDF 9건은 Homebrew pango를 `DYLD_FALLBACK_LIBRARY_PATH`에 넣어야 통과 — 로컬 환경 문제, 코드 아님) |
| Backend ruff | 통과 |
| Admin test / lint / typecheck | **538 passed** / 0 / 0 |
| Site test / lint / typecheck | **310 passed** / 0 / 0 |
| Site production build | `NEXT_PUBLIC_SITE_URL` 없으면 fail-fast (의도된 가드, `.env.local`에 추가함) |

결론: **테스트가 잡는 범위에서는 결함 없음.** 아래 결함은 전부 테스트가 커버하지 않는 배선·상태 표시·수동 경로다.

## 2. 결함 등록부

**처리 현황 (2026-09-08):** PR-0A 완료 — H-05(`5606168`), H-06(`cf4ee67`, `5388ba9`), H-07(`78fe8b6`), M-10(`c9076f4`), M-11(`cf4ee67`), M-13(`85128d7`) 수정·검수 완료. 상세는 [PR-0A 실행 결과](../plans/2026-09-08-pr0a-lifecycle-activation-plan.md#실행-결과-2026-09-08). PR-0B 완료 — H-02(`0487525`), H-03(`ead9d6b`, `8c6e1b1`), H-04(`78b08a0`), M-01(`2482cf4`), M-03(`1a52744`) 수정·검수 완료. 상세는 [PR-0B 실행 결과](../plans/2026-09-08-pr0b-essence-evidence-plan.md#실행-결과-2026-09-08). PR-0C 완료(`1d1d881`); PR-1A 완료(3상태·사전·overview, `15b955a`까지, backend 3,286·admin 568·site 310) — Task 1(H-01 표시 부분): `41affe3`(복사 가드) `6e4d5b2` → `db71a26` `a16a764` `691f186` `fd00a49`(Fable·Codex 5라운드 교차 검수, 운영 큐·readiness·대시보드·온보딩까지 단일 판정으로 통일). 이 과정에서 H-16 신규 등록. Task 2(H-01 복구, `20bbe45`까지)·Task 3(H-08 `96bda45`)·Task 4(H-09 `907a33e` `1e95338`)·Task 5(H-14 `4cba988` `320ace8`)·Task 6(M-15·M-21·M-22 일부 `3553105` `320ace8`) 완료. 검토 라운드 상한(작업당 2회, 계약 위반만 수정)을 사용자 지시로 도입 — 그 이후 지적은 §2.3에 기록. 테스트 순서 의존 flake 2건은 `adff1e6`으로 근본 해결(전역 async 엔진 dispose·잔여 행 정리).

심각도: **HIGH** = 데이터/비용/안전 무결성 또는 사람이 화면을 믿을 수 없게 만드는 것. **MED** = 기능은 돌지만 사람을 헛돌게 하거나 특정 조건에서 깨짐. **LOW** = 정리 대상.

### 2.1 HIGH

| ID | 결함 | 근거 | 영향 | 수렴 |
|---|---|---|---|---|
| H-01 | **공개된 글이 조용히 숨겨지는데 admin은 초록.** 공개 글 제목 편집 → 이미지 인증 삭제(`api/admin/content.py:578-593`) → 사이트가 fail-closed로 숨김(`api/public/site.py:615-649`) → 아무것도 재인증 안 함 → admin `compliance.publishable`은 이미지·FAQ·AI검수를 빼고 계산(`content.py:1339-1372`) → "공개 완료 + 공개 사이트에서 보기" 표시, IndexNow는 404 URL 재제출(`content.py:604-612`) | 운영자가 알 방법 없음. 화면을 믿을 수 없음 | Claude F-1, C-02 / Codex GATE-03 |
| H-02 | **근거 노트 "노이즈 제외"가 승인 snapshot에 안 들어감.** snapshot hash = source id/content/status/time만(`essence_engine.py:201`), 노트 제외는 JSON만 바꿈(`api/admin/essence.py:545,569`), readiness는 hash만 신뢰(`essence_readiness.py:59-63`), 자동 합성은 `is_noise`를 무시(`essence_auto_review.py:286-304`) | AE가 명시적으로 뺀 주장으로 계속 생성·발행 | Claude E-04 / Codex ESS-01, W-01 |
| H-03 | **수동 승인이 자동 검수 결과를 다시 확인하지 않음.** 금지 표현·근거 충돌·프롬프트 인젝션으로 ESCALATED된 초안을 체크박스로 승인 가능(`essence.py:1672-1676`) | 안전 게이트 우회 | Claude E-03 |
| H-04 | **"수동 초안 만들기"가 해당 snapshot의 자동 승인을 영구 해제**(`essence_auto_review.py:1240-1256, 368-384`), 경고 없음. 자료 일부만 고른 초안은 승인도 폐기도 불가(`essence.py:1697-1706`, 삭제 엔드포인트 없음) | 선의의 클릭 한 번으로 자동화가 죽고 영구 대기 행이 남음 | Claude E-01, E-02 |
| H-05 | **ACTIVE 전환 코드가 세 곳.** `domain_verification.py:123-127`은 PAUSED 검사 없이 ACTIVE 기록 → "DNS 확인하고 운영 시작"/대시보드 "공개 주소 확인"(`operations.py:801-808`)이 일시정지 병원을 우회 재개, 사이트 revalidate 없음. 테스트 없음 | `HospitalNotActivatable` 우회 | Claude L-01, L-09 / Codex(대시보드 verify=C) |
| H-06 | **admin 6곳이 `site_live`만 보고 "공개 중"이라 표시**하지만 공개 게이트는 `status==ACTIVE`; pause는 `site_live=True` 유지 → PAUSED 병원에 "운영 일시 정지" 배지 옆 "이 주소로 현재 공개 중"(404 링크). pause/resume이 사이트 캐시를 갱신하지 않아 ≤30분 공개 유지(`hospitals.py:1091-1118`) | 08-22 A-1과 같은 종류의 모순 잔존 | Claude L-03, F-3 |
| H-07 | **`schedule_set=false`면 헤더가 "재개"를 숨김**(`layout.tsx:114-117`) — 백엔드는 허용(`test_resume_allows_missing_schedule`). 다른 재개 UI 없음, 운영센터는 PAUSED 제외 | 일정 없는 일시정지 병원은 UI에서 복구 불가 | Claude L-02 / Codex W-05 |
| H-08 | **생성 재시도 예산이 매일 리셋 → 비용 누수.** 22:30 복구가 `scheduled_date`를 다시 쓰고 그 값이 시도 fingerprint에 포함(`tasks.py:388-401`, `generation_retry_policy.py:11`) | 결정적 실패 글이 하루 4회 유료 호출을 영원히 반복 | Claude C-01 |
| H-09 | **수동 발행 경로 결함 3종.** (a) 병원 ACTIVE/site_live/일정 게이트 미검사, 병원 row lock 없음(`content.py:782-797`, `post_publish_review_policy.py:38`) (b) 공백만 있는 제목/본문이 PUBLISHED 가능, 사이트는 거부(`content_publication.py:183` vs `site.py:625`) (c) `published_by`를 요청 본문에서 받음(`content.py:173,847`) → 불변 `first_published_by` 위조 가능 | 보이지 않는 PUBLISHED 행, 귀속 오염 | Codex GATE-01, GATE-02, SEC-02 |
| H-10 | **백엔드 admin API가 인터넷에서 닿는데 공유 키만 검사.** admin 라우터 전부 `/api/v1`(`main.py:172-186`), LB가 `var.domain`의 `/api/v1/*`를 API로 전달(`loadbalancer.tf:184-185`). actor 헤더 선택·위조 가능(`security.py:47,142`, `audit_log.py:25`, `accounts.py:47`) | 키 유출 시 세션 없이 전체 조작; 정상 운영자도 actor 위조 가능 | Codex SEC-01 (terraform으로 본 검토에서 확인) |
| H-11 | **표본 부족(LIMITED) 월간 리포트가 milestone 투영기를 죽임.** 전달 게이트는 `COMPLETE|LIMITED` 허용(`monthly_report_delivery.py:142-157`), 투영기는 `COMPLETE`만 받고 raise(`monthly_events.py:181-196`), 리포트별 격리 없음(`milestone_event_tasks.py:67-92`) → **모든 병원**의 온보딩·월간 Slack milestone 정지. "최종"의 정의가 3곳에서 다름(09:00 요약이 전달 가능 리포트를 매일 "측정 미완료"로 재알림) | 잠복 전역 장애 | Claude M-01, M-02 |
| H-12 | **V0 무음 정지·재구매·조기 실행.** 비용 보류 continuation에 `safe_error_message` 없음(`tasks.py:2971-2979`), 이어가기 예산 소진 무음(`tasks.py:3189-3212`); 유일한 사람 행동 "다시 실행"은 새 lineage로 150 슬롯 전량 재구매(`v0_checkpoint.py:126-207`); 대시보드 V0 실행이 `profile_complete` 전에 가능하고 API/worker도 게이트 없음(`dashboard/page.tsx:378`, `operations.py:460`, `tasks.py:2806`) | 무효 기준선 과금, 무음 정지 | Claude M-V0 / Codex W-02 |
| H-13 | **사이트 준비 복구가 무한 재큐잉.** 매분 `profile_complete && !site_built` 가장 오래된 100곳을 claim/attempt 기록 없이 dispatch(`autonomous_recovery.py:128,204`), 자식 task 3회 재시도 후에도 반복 | 중복 큐 메시지, 뒤 병원 기아, 인시던트 없음 | Codex REC-01 |
| H-14 | **일정 화면이 계약 요금제를 바꿈.** `schedule/page.tsx:290` 선택 → `content.py:300`이 `hospital.plan` 기록. 감사되는 계약 정정 엔드포인트(`handoffs.py:280`)는 admin 호출자 없음 | 인수 기록과 병원 가격/편수 불일치, 정정 경로 우회 | Codex W-03 |
| H-15 | **인시던트 배정 UI 없음.** 복구/확인/Slack 재전송은 배정자 또는 OWNER만 허용(`operations_center_actions.py:90-96`), 배정 엔드포인트는 존재(`operations_center_incident_routes.py:162`)하나 클라이언트 미호출, 버튼은 역할 무관 표시 | OPERATOR가 403을 받고 갈 곳 없음 | Claude admin W-1 / Codex W-04 |
| H-16 | **월간 원장 리포트가 공개 보류 글을 '발행 글'로 나열.** `tasks.py:7801` `visible_publications`가 status==PUBLISHED만 보고 공유 판정(`content_visibility.assess_public_visibility`)을 거치지 않음 → `report_engine.py:1164`·`doctor_report.html:103`에 보류 글 제목 노출 | Codex PR-0C Task 1 5차 검토 | 원장에게 공개되지 않은 글을 공개된 것으로 보고. 단, 재인증(PR-0C Task 2) 전에는 일시 보류가 대부분이라 리포트 시점 판정을 그대로 넣으면 계약 이행을 과소 보고할 수 있음 — 계약 월·`first_published_at` 규칙과 함께 PR-0C-2/PR-0D에서 결정 | 리포트 시점에 보류 글은 별도 항목("공개 보류 N편")으로 표기하거나 제외하되 계약 편수 집계는 `first_published_at` 기준 유지 |

### 2.2 MED

| ID | 결함 | 근거 |
|---|---|---|
| M-01 | URL만 있고 추출 원문 없는 자료가 PENDING으로 남아 사람이 "제외"할 때까지 승인 무한 차단 (system-map §5 주장과 반대) | Claude E-05 |
| M-02 | 24k 청크당 `max_tokens=3000`에 노트 수 상한 없음 → 결정적 ERROR, "다시 처리"로 복구 불가 | Claude E-08 |
| M-03 | 이미 처리된 자료의 "재처리" 버튼이 no-op이면서 "완료" 알림 표시 | Claude E-10, `essence/page.tsx` |
| M-04 | 비ACTIVE 병원의 ESCALATED가 인시던트를 열지 않고 옛 것을 거짓 사유로 복구 처리 | Claude E-09, `tasks.py:2434-2449` |
| M-05 | 새 운영 기준 승인 시 `_rescreen_content`가 저장된 본문 전부를 새 철학으로 relabel → 주석의 "1회 재생성" 경로 사실상 dead | Claude C-03, `essence_auto_review.py:1136-1174` |
| M-06 | 운영자 "재생성"이 생성 lease를 안 잡음 → 야간 생성과 이중 지출 | Claude C-04, `tasks.py:4091` 대비 |
| M-07 | 발행 일정 교체 시 옛 일정의 과거 미발행 슬롯이 복구 대상에서 빠짐 | Claude C-06, `content_backlog_recovery.py:57` |
| M-08 | 전달 기록의 hash 결합이 GET 값 echo — 다운로드 바이트를 해시하지 않음; 대체된 버전도 전달 기록 가능 | Claude M-03/04, `reports.py:461-469` |
| M-09 | 같은 병원 언급률이 5개 병렬 집계(주간 대시보드·manifest PDF·target cohort·질문 카드·V0)로 각각 다른 숫자 | Claude 측정 5번 |
| M-10 | 프로필 체크리스트가 배지와 다른 규칙으로 도메인 상태 표시("HTTPS 인증서 DONE" vs "발급 중") | Claude L-05, `domain_setup.py:779-781` |
| M-11 | `resume`가 DNS/live 증거 없이 활성화 → A-1 재발 경로 | Claude L-06 |
| M-12 | `/hospitals/new?leadId=`가 중복 병원 409를 삼키고 "다시 시도"라고 안내; 리드 모달 버튼이 선택한 요금제를 버림 | Claude L-07 |
| M-13 | 운영 중 병원의 `profile_complete` 체크 해제가 무경고 수락 → 공개 사이트 404, 화면은 계속 운영 중 | Claude L-08 |
| M-14 | 커스텀 호스트 revalidate가 `/`만 purge, `/contents` `/doctor` `/visit`은 TTL 의존 | Claude F-2, `site_revalidate.py:68-92` |
| M-15 | 공개 글에 brief 편집 UI가 보이지만 서버가 항상 409 | Codex W-06, `content/page.tsx:1275,923` vs `content.py:1153` |
| M-16 | ACTIVE 목록/헤더 점/온보딩 체크리스트가 "완료"를 각각 다르게 정의 → 같은 병원이 동시에 완료·미완료 | Codex W-07, Claude admin |
| M-17 | 월간 측정 대상 여부(`in_tracking_set`)가 백엔드에만 있고 admin 타입에서 누락 | Codex W-08 |
| M-18 | 환자 질문 비어 있을 때 수동 생성만 안내; 멱등 `/seed-from-matrix`는 미호출 | Codex W-09 |
| M-19 | 노출 보완 "갱신" POST가 GET 자체 복구와 중복; "완료" 처리는 다음 재조정에 되살아남; 질문 우선순위 편집은 주간 작업이 덮어씀 | Codex W-11, Claude 측정 8번 |
| M-20 | DB `plan` enum에 `PLAN_8` 잔존(0039 미정리); `content_schedules.plan` 무제약 VARCHAR | Codex DB-01/02 |
| M-21 | 콘텐츠 화면이 공개 글 전부(월 12~20편)에 "문제 없음" 버튼을 띄우지만 백엔드 표본은 `sequence_no==1`/편집본만; 목록은 미검수를 "확인 완료"로 표시 | Claude admin 7번 |
| M-22 | TS/Pydantic 불일치: `ContentItem.compliance` 필수 vs Optional, `first_publish_date` null 가능, `OperationsOverview.queues[].overdue` 미전송, readiness 타입 3벌, `SourceAsset.photo_provenance` 누락 | Claude admin 4 / Codex W-10 |

### 2.3 LOW / 정리

PR-0A Task 3 후속(`85128d7`)으로 프로필 PATCH·활성화·일시정지·재개가 병원 advisory lock 아래서 직렬화된다. 그 결과 재개의 외부 DNS 조회가 잠금을 잡은 채 실행된다 — DNS 타임아웃 동안 같은 병원의 프로필 편집이 대기하는 것을 감수한 결정(잠금을 DNS 뒤로 미루면 막으려던 경합이 다시 열린다). `lock_hospital_for_domain_certificate`(`domain_certificate_jobs.py`)는 PR-0A Task 2부터 재개 경로도 쓰는 "행 잠금 + 도메인·전략 비교" helper인데 이름이 인증서 전용처럼 읽힌다 — `lock_hospital_domain`으로 개명(호출자 3곳). `tests/integration/test_attention_queue.py::test_incident_queue_groups_same_cause_in_a_constant_number_of_queries`는 5432 테스트 DB에 이전 실행이 남긴 인시던트 행이 있으면 `total 2 == 1`로 실패한다(깨끗한 DB에서 3/3 통과 확인). 테스트 격리 약점 — 병원 단위로 카운트하거나 setup에서 정리해야 한다; 부분 실행을 반복하는 로컬 개발에서 거짓 실패를 만든다. `tests/integration/test_provider_usage_postgres.py`도 단독 실행은 통과하고 다른 파일과 함께 돌리면 실패하는 순서 의존 flake가 있다(PR-0B Task 6 검증 중 관측) — 같은 부류. READY 상태 미사용; 23:00 task 안의 dead 07:45 호출; 07:45 "recovery" 이름의 페이징 전용 task; beat 안의 Nowon 일회성 잔존; `_report_delivery_blockers` 미사용(`reports.py:120-135`); `delivery_tracked` 하드코딩 True로 admin V0 분기 도달 불가; JSON-LD `alumniOf` 미렌더 필드; `/api/leads` 호출자 없음; slug/host 정규식 4곳 중복(패리티 테스트 없음); 주석 "25일 00:00" 드리프트; `connect_domain` check-then-write 500; pause/resume/set_schedule `FOR UPDATE` 없음.

(H-10) actor 단언의 `nonce`는 저장하지 않는다 — 재생(replay) 창은 120초 TTL로만 제한되며, 재생 저장소는 유예한다(공격자가 이미 X-Admin-Key와 유효 단언을 모두 가로챈 경우에만 120초짜리 재사용이 가능하고, 그 창에서 얻는 것은 이미 승인된 같은 요청의 반복뿐이다).

PR-0C 검토에서 기록만 한 항목(계약 위반 아님): (재인증) 자유 마감 run이 `attempts_spent`를 계속 올려 운영 표시가 부풀 수 있음; `_recertify_runs`가 병원의 재인증 run 전체를 읽음(무한 성장 — 인덱스 또는 기간 제한 검토); ACKNOWLEDGED 인시던트는 성공 후에도 자동 종료 안 됨(기존 의미); 예산 불변식은 `APP_ENV=production`의 dispatch 인증 게이트에 의존; 재배달 테스트가 `attempt_count`를 직접 시드; `ImagePolicyUnavailableError`(비용 가드 차단 포함)가 GENERATION_FAILED로 분류돼 쿨다운 3회 뒤 UNRECOVERED 문구가 원인을 오도할 수 있음; 일시정지 병원의 PATCH는 디스패치하지 않음(스윕이 재개 후 처리). (H-09) 게이트 테스트가 `site_live=False` 단독 케이스·0 커밋 단언 없음, 제목 공백만 검사; 프런트 회귀 테스트가 소스 문자열 검사. (H-14) 계약 정정 다운그레이드 시 현재 월 reconciler가 `total_count`만 낮추고 초과 slot을 남김(기존 동작). (M-21) 프런트 테스트 2개가 소스 텍스트 단언. 헤더 로딩 실패가 "불러오는 중"으로 표시. (H-01 표시) 대시보드 `visibility_load_only` 컬럼 목록 완전성은 통합 테스트만 보장; `Load` 타입 주석.

PR-0D Task 3(H-13)·PR-1E Task 3~4 검토에서 기록만 한 항목(계약 위반 아님): (H-13) `_redispatch_operation_run`이 `send_task` 뒤에 무조건 `QUEUED`를 쓰므로, 발행 직후 워커가 먼저 종결한 run을 이론상 되살릴 수 있음 — 2026-08-15부터 있던 경로이고 창이 ms 단위라 유예; `RETRY→QUEUED`가 원래 `queued_at`을 보존해 재시도 대기 중 1시간 유예를 넘기면 스윕이 같은 작업을 다시 보낼 수 있음(`_claim_safely`·병원 `FOR UPDATE`로 중복 실행은 무해); 성공했는데도 계속 대상인 병원은 멱등 키(`rebuild-site:{hid}:{utc-date}:{n_failed}`) 때문에 UTC 하루 1회만 복구 시도하며 인시던트 없이 조용함(무한 루프였던 이전보다 낫고, 원인은 `evaluate_auto_activation`의 다른 차단 조건이므로 그쪽 인시던트가 담당). (계약 등록) OWNER 대리 수락 사유 필드 없음(감사 `owner_override: true`로만 남음); 계약 번호 유일성은 라우트 검사만(DB 유니크 없음 — 동시 중복 가능, 운영 볼륨상 유예); `/hospitals/new` 성공 후 자동 입력을 배경에서 시작하지 않음(`/profile/autofill`은 저장하지 않는 동기 초안 API라 서버 디스패치는 예산만 쓰고 버림 — 병원 정보 탭의 자동 입력 모달이 담당); `영업 담당`은 리드에 필드가 없어 로그인 사용자 기본값. (옛 경로) 백엔드가 만드는 admin 딥링크 약 11곳(`domain_certificate_incidents.py`, `domain_health_control.py`, `naver_handoff_incidents.py`, `onboarding_notifications.py`, `onboarding_events.py`, `hospitals.py` `onboarding_url`, `operations_center_onboarding_queries.py`)이 아직 옛 세그먼트(`/profile#domain-setup`, `/onboarding`, `/dashboard`)를 가리킴 — redirect 덕에 동작하며, **redirect 제거 예정일 2026-10-09 전에 새 탭 경로로 옮겨야 한다**. `readiness_operator_copy.py`는 전역 용어 가드(admin 한정) 범위 밖이라 백엔드 운영자 문구는 검토 때 수동 확인.

### 2.4 확인된 정상 (감사 문서 주장 중 코드로 재확인된 것)

- 공개 활성화 게이트 `profile_complete && site_built`는 단일 함수(`hospital_lifecycle.py:131-138`)를 backend/worker/admin lib이 공유. 자동 활성화는 lock·멱등·강등 없음.
- beat 29개 항목 전부 등록된 task와 정확히 매칭, KST 일관, 서명 dispatch 예외 없음. 콘텐츠 6개 항목(21:30/22:30/23:00/01·04·07/07:45/08:00) 실재.
- 요금제 편수 배분 정확히 일치(`models/content.py:53-81`). 금지 표현은 삭제가 아니라 **전체 재생성**(`content_engine.py:845-866`, tenacity 3회) — 08-25 W-2 해결.
- 자동 발행과 수동 발행은 같은 `assess_content_publication` 사용; 자동 경로에 의료 안전 우회 없음. 공개 사이트 필터는 둘보다 엄격.
- 자료 저장 → durable run → claim/CAS 추출 → 자동 검수 → `SYSTEM_ESSENCE_AI_REVIEW` APPROVED까지 사람 불필요. 동일값/공백 PATCH no-op. 08-22 C-1(재시도+승인 잠금), 08-25 C-1(자동 승인 정상화) 해결.
- SoV 분모는 단일 primitive(`record_is_confirmed`); 08-22 A-2 해결(`exposure_action_engine.py:243-262`). V0 성공 Slack은 발송됨(`tasks.py:3139-3148`).
- 08-22 A-1: 목록/헤더/패널 배지는 공유 `liveCheckProvesServing`으로 수정됨(체크리스트만 잔존 → M-10). F-1: 리드 모달·백엔드 수정됨(`/hospitals/new?leadId=` 경로만 잔존 → M-12).
- 공개 사이트: 초안/반려/미승인 필드 유출 없음, canonical/sitemap/robots/llms/IndexNow origin 일치, 리다이렉트 루프 없음, revalidate 비밀키 상수시간 비교, 진단 토큰 256-bit HMAC. P-A-1, W-3, UI-2, UI-3 해결.
- admin→API 108쌍 전부 라우트 존재, BFF 허용목록 문제 없음. 마이그레이션 0069까지 선형.

## 3. 사람 개입(HITL) 현재 비용

두 검토자가 독립 산정 (Claude: 상호작용 전체, Codex: 주요 버튼만).

| 구간 | 페이지 | 상호작용 | 사람 결정 | 그중 진짜 필요한 결정 |
|---|---|---|---|---|
| 상담 → ACTIVE → 일정 (정상 경로) | 5~6 | ≈30 (주요 클릭 6) | ≈19 | **≈8**: 계약 번호·효력일·담당 AE·요금제 / 진료 철학·경쟁 병원·키워드 / 사진 권리 / 발행 요일 |
| 정상 운영 1개월 | 2 | ≈8~9 (주요 클릭 3) | 3 | **2**: 표본 1편 공개 후 확인, 원장 전달 기록 |

나머지 결정은 전부 **시스템이 이미 계산·제안한 값을 사람이 다시 눌러주는 것**: 새 병원 vs 기존 연결(후보가 없어도 물음), 요금제 2회 입력, `profile_complete` 체크박스(서버가 `missing_profile_requirement_keys`를 계산함), 색상/모드/문구 제안 승인, 공식 채널 "자료로 추가" ×N(프로필에 이미 있음), 인수 수락(등록과 같은 제출), 발행량(요금제에서 결정됨). 정상 경로에도 보이는 예비 버튼("기본 주소로 운영 시작", "초기 진단 다시 만들기", "처리 시작", "사이트 정보 다시 반영", "지금 측정", "갱신")은 클릭을 유도하고 일부는 자동화를 우회한다(H-05, H-12, M-06, M-19).

### 사람 행동 분류 요약 (두 검토자 합산, 중복 제거)

- **A. 진짜 사람 결정 (유지)**: 병원 생성/계약 기록, 공식 사실 편집·저장, 자기 도메인 입력·제거, 사진 권리 정보, 발행 요일, 일시정지/재개, 예외 승인(ESCALATED 사유 확인 후), 원장 전달 기록·정정·철회, 인시던트 담당 지정, 운영자 계정 관리.
- **B. 시스템이 해야 할 것 (제거/자동화)**: 인수 수락 클릭, `profile_complete` 체크박스, 자동 입력 미리보기 실행, 채널 "자료로 추가", "처리 시작", 색상/문구 수동 확정, "기본 주소로 운영 시작", 도메인 "확인"(자동 폴링), V0/SoV/사이트/리포트 재실행, 노출 보완 "갱신", 환자 질문 수동 생성(seed 호출), 온보딩 "새로고침", 공개 글 전부의 "문제 없음".
- **C. 중복 (한 곳으로)**: 공개 화면 디자인(온보딩=프로필), 공식 채널(온보딩=프로필), 자료 표(온보딩=운영기준=자료모음), 도메인 확인(프로필=대시보드), 노출 우선순위(대시보드=노출보완), 질문별 언급률(대시보드=환자질문), readiness(대시보드=온보딩=일정).
- **D. 죽었거나 오도**: 공개 글 brief 편집(항상 409), "재처리"(no-op+거짓 완료), 프로필 완료 전 V0 실행, PAUSED 병원의 "공개 중" 링크, `site` 단계 앵커, "사이트 재빌드" 안내 문구(버튼 없음), 일정 화면 요금제 변경.

## 4. 용어 불일치 (핵심)

| 개념 | 현재 변형 | 통일안 |
|---|---|---|
| 공개 표면 | 병원 정보 허브 / 콘텐츠 허브 / 정보 허브 / 공개 화면 / 공개 사이트 / 공개 표면 / 공개 주소 / 기본 플랫폼 주소 (8종) | **병원 공개 페이지** + **공개 주소**(URL) |
| 운영 기준 | 콘텐츠 운영 기준 / 운영 기준 / 운영 기준 정보 | **콘텐츠 운영 기준** |
| 초기 진단 | 초기 진단 리포트 / 초기 진단 보고서 / AI 진단 분석 중 | **초기 진단 보고서** |
| 월간 | 보고서 / 리포트 (서버 라벨 포함) | **보고서** |
| 근거 | 근거 노트 / 근거 자료 / 자료 / 자료 모음 / 검증된 사실 / wiki | **근거 자료**(원문) / **근거 노트**(발췌) |
| 요금제 | 요금제 / 월간 운영량 / 월간 발행량 | **요금제** + **월 발행 편수** |
| 사람 | 관리자 / 운영자 / AE / 담당자 / 담당 AE | **운영자**(계정) / **담당 AE**(배정) |
| 공개 후 확인 | 공개 후 확인 필요 / 공개 내용 확인 / 확인 완료 / 발행 후 확인 대기 / 후행 확인 / 사후검수 | **공개 후 확인** / **확인 완료** |

`ADMIN_COPY` 공통 사전은 25개 페이지 중 2개만 사용. `admin-korean-language-guide.md`는 13개 용어만 정의하고 위 8개 중 4개를 다루지 않는다.

## 5. 원본 보고서

전체 근거(배선 맵 108쌍, 타입 불일치, 걸음표, 검증 OK 목록, 미해결 질문)는 세션 스크래치패드의 `review-lifecycle.md`, `review-content.md`, `review-measure.md`, `review-essence.md`, `review-admin.md`, `review-site.md`, `codex-review-hitl.md`, `codex-review-backend.md`에 있다. 이 문서는 그 통합·중복 제거본이다.
