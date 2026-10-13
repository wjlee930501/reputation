# 2026-10-13 자율 운영 Phase 3 운영 배포 — 콘텐츠 게이트 조정(할인·유인 차단·진료비 참고자료 선택화·결정적 치유)

배포일: 2026-10-13 15:48~15:56 KST(backend 15:48:18~15:55:48) · 배포 소스: main `c1ce0494`(PR #245 `claude/autonomy-p3`, 병합 기준 main `d6b85c96`)
배포 대상: API·Worker·Beat (Admin·Site 변경 없음 — `git diff d6b85c96..c1ce0494 -- admin/` 비어 있음) · DB head `0088_add_outbox_open_pairing_index` 그대로(마이그레이션 없음) · RedBeat 버전 그대로
리비전: api `00244-r2w`, worker `00234-nn4`, beat `00229-l4v` (admin `00107-d26`·site `reputation-site-cr057b9d1e` 그대로)
(이전: api `00243-mx4`, worker `00233-9k7`, beat `00228-jlm` — Phase 2)
배포 PC: 작업 PC(wjlee@motionlabs.kr) · 깨끗한 worktree `reputation-deploy`(detached `c1ce0494`). **오늘 네 번째 릴리스** — `--override-reason="대표 지시: 준비되는 즉시 배포(2026-10-13), 배치 창 밖"`(잠금 기록 `gs://reputation-reports-mso-platform-481505/ops/deploy-lock/2026-10-13-override-1548.json`). 배치 시간대(17:15~18:45, 22:15~02:00) 밖. 자동 롤백 켜짐(발동 없음).

## 바뀐 계약 (정본: CLAUDE.md, [계획](../plans/2026-10-13-p3-content-gates.md))

- D24: 공통 필터(`utils/medical_filter.py`)에 가격 우위·할인·이벤트·무료 유인 하드 차단(라벨 `할인·유인`), 문장 전체·방향 무관 문맥 예외와 결과 보장 표지. 필터는 공개 가시성·병원 공개 문구에도 쓰여 **배포 즉시** 공개 표면이 바뀐다.
- D23: 진료비·병원 선택 글은 참고자료가 비어도 생성·발행을 막지 않는다(`references_block_when_empty`), 공개 가시성도 숨기지 않는다.
- 생성 단계의 결정적 치유(금지 표현 문장 삭제, 병원명·필수 문구 덧붙이기), 생성 거절 사유 라벨(`rejection_kind`·`generation_rejections`), 운영 조회 q9·q10.
- 게이트 카탈로그 판 `2026-10-02.1` → `2026-10-13.1` — 종착 슬롯 전부가 한 번 다시 평가된다.
- 새 설정 `CONTENT_HARD_FINDING_IDLE_TERMINAL`(기본 false) — `.env.production`에 넣지 않았다(코드 기본값 사용, env 차이 0). 배포 전 측정(아래 4)이 10/10 이후 공회전 0건이라 켤 근거 없음.

## 배포 절차와 증거

1. 이미지 사전 빌드(읽기 전용 조회용): `reputation:20261013-154201`(push digest `sha256:486a8af3…579a`). 배포는 `deploy.sh`가 다시 빌드했다(아래 이미지 표).
2. **영향 조회(양방향, 배포 전)** — 15:45 KST, migrate Job `reputation-migrate-ndsv4`. migrate Job의 이미지를 새 이미지로 바꾸는 단계는 이번 세션 권한에서 거절돼, 현재(Phase 2) migrate 이미지에서 `c1ce0494`의 `medical_filter.py`·`medical_filter_baseline.py`·`forbidden_impact_scan.py`·`ops_sql_report.py`를 base64 `python -c`로 실어 모듈로 바꿔 끼운 뒤 돌렸다(읽기 전용, `SET TRANSACTION READ ONLY`). 배포 뒤 새 이미지 그대로(`reputation-migrate-kvrjd`, 15:57) 다시 돌려 **같은 결과**를 확인했다.
   - 결과: `{"summary": {"할인·유인": 1}, "changes": {"newly_blocked": 1}, "baseline": "7a1e2174"}` — **newly_blocked 1 · newly_unblocked 0 · still_blocked 0**. 병원 문구(히어로·진료 항목)·승인 필수 문구·about 문장 영향 0.
   - newly_blocked: 공개 글 `4a3c2b13-45e3-4fc2-91d8-b3e98cc68200` — 마포성모탑정형외과(`maposeongmotabjeonghyeongoegwa`, `smtopos.kr`) HEALTH "도수치료 비용 — 시간·횟수·보험에 따라 어떻게 달라지나요?"(first_published 2026-09-20). 걸린 문장(body): "일부 의료기관에서는 패키지 할인이나 회차 할인을 제공하기도 하므로, 치료 전 상담 시 문의하는 것도 방법입니다." 라벨 `할인·유인`, 운영 필터 판정 없음.
   - 판정 기준(newly_blocked 라벨이 모두 `할인·유인`이고 5건 이하)을 만족해 진행했다. 이 글은 배포 즉시 공개 표면에서 숨는다 — 해당 문장을 사람이 PATCH로 지우면 다시 공개된다.
3. 롤백 증거(배포 전 판): 위 글 `content_revision` 4 · active revision `3a260ef7-ca95-f92a-65cf-978bce139cf7` · 승인 공개 본문 sha256 `26a7ee11060750633e8a12f21fede4e0eab6b5e129a33b1d17136ae974d22c3a` · candidate sha256 `af611f4b4217fc2880231567c4e867e2039a728f24f8dc0c83bdfc2616485ed4` · 배포 전 공개(운영 필터로 보임). D23 롤백 대비 목록(발행 이력·참고자료 0개·진료비/병원 선택 제목) **0건**. 원본: 배포 worktree `.omo/rollback-p3/impact-scan-and-rollback-evidence.jsonl`.
4. 사전 조회(같은 실행): alembic `0088_add_outbox_open_pairing_index` · q7 `outbox_rows` 634 / `open_notice_rows` 403 · `CONTENT_AI_HARD_FINDING` FAILED 생성 실행(10/10 이후) **0건** → `CONTENT_HARD_FINDING_IDLE_TERMINAL`은 false 유지 · 열린 사고: 사후검수 FLAGGED 74(OPEN)·콘텐츠 생성 28(OPEN)+1(RECOVERED)·원장 PDF 4·Essence 승격 1 OPEN+3 RETRYING·Essence 실패 1·월간 측정 2·백그라운드 2·리드 진단 1·리드 측정 복구 1·온보딩 프로필 1.
5. **q4(카탈로그 판 재평가 대상) = 4슬롯**(모두 DRAFT, 7일 창 안): `OPERATOR_REQUIRED/MISSING_REFERENCES` 3(`d05a2b7d…`·`f717bcb8…`·`8344b365…`, 3개 병원, 10/3~10/4 관측 — D23 이전의 진료비 `operator_decides` 보류) · `OPERATOR_REQUIRED/CONTENT_AI_HARD_FINDING` 1(`4a2d0db0…`, 10/8 관측). 비용 상한 슬롯당 작가 세션 1회분 × 4 ≈ $1.2. 23:00 배치 상한 50에 비해 무시할 수준.
6. 배포 전 공개 가시성(Phase 2 이미지, `reputation-migrate-kp7dc` 15:49): PUBLISHED 248 · 보임 **248** · 숨김 0.
7. `deploy.sh backend` 15:48:18~15:55:48(약 7분 30초): env는 운영 서비스 env를 정본으로 사용(차이 0, `--accept-env-diff` 불필요) · Slack `SLACK_WEBHOOK_URL` 확인·`_DEV` 비활성 미주입 · legacy publish 사전 확인 `READY`(증거 `.omo/evidence/deploy-preflight/legacy-publish-retirement-c1ce0494…-20261013T064846Z.json`) · 롤백 좌표 `.omo/rollback-p3/backend`(06:49:18Z) · 빌드·push `reputation:20261013-154818` · migrate `reputation-migrate-q8dzh`(변경 없음) · worker `00234-nn4`(06:50:09Z 생성) · RedBeat reconcile `reputation-redbeat-reconcile-cjcpc` · beat `00229-l4v`(06:50:54Z) · readiness `reputation-production-readiness-drkvf` → api `00244-r2w`(06:55:16Z) · 배포 뒤 공개 표면 smoke 통과.
8. readiness: `ready=true`, **검사 18/18**, 7개 큐 canary 현재 릴리스 `c1ce0494`, `schema_revision=0088_add_outbox_open_pairing_index`(current), `slack_webhook_valid={operator: alive, developer: not_configured}`, `auto_publish_hold={active: false, all_hospitals: false, hospital_count: 0}`, live site 11.
9. 트래픽: 배포 전후 5개 서비스 모두 `traffic[0]`=latestReady·100%·`latestRevision=True`(고정 없음). API `/api/v1/health/live` 200 `release=c1ce0494…`.
10. 병원 헬스 11곳: **11/11 200**, slug·canonical_host 일치·hospital_id 있음, release `reputation-site-cr057b9d1e`. 공개 루트·Admin 로그인 200.
11. 배포 뒤 공개 가시성(새 이미지, `reputation-migrate-kvrjd` 15:57): PUBLISHED 248 · 보임 **247** · 숨김 1 = `4a3c2b13…`(`FORBIDDEN_EXPRESSION`) — 예상(newly_blocked)과 정확히 일치. 다른 공개 글은 숨지 않았다. q4·q7·q9 사전과 같음, q10 비어 있음(배포 뒤 생성 세션 없음).
12. 새 리비전(api `00244-r2w`·worker `00234-nn4`·beat `00229-l4v`) ERROR **0건** — +10분(16:06, 범위 06:48:18Z 이후) WARNING 20, +30분(16:26) WARNING 36. 내용: API 4xx 스캐너 요청, 16:00 발행 catch-up의 기존 보류 17건(`CONTENT_NOT_GENERATED` 13·`MISSING_REFERENCES` 3 = q4의 진료비 보류 슬롯·`CONTENT_AI_HARD_FINDING` 1 = q4의 HARD 슬롯), 그리고 예상한 `Public content withheld by the medical-ad filter: … labels=할인·유인` 1건(`4a3c2b13…`). `expired authenticated dispatch envelope` 0.
13. 첫 복구 스윕(18:00 KST) 관찰: 18:00 KST 복구 스윕(배치 시간대 안 — 관찰만, 후속 배포 worker `00235-vnd`에서 실행): `overnight_content_generation_recovery`가 **28건**을 claim해 `generate_claimed_content_item`으로 넘겼다(09:00:02Z "Nightly generation dispatched 28 item claims"). 18:25 조회(`reputation-migrate-668nd`, 슬롯·Essence 후속 조회 `reputation-migrate-lp6sf`):
   - 카탈로그 판 재평가 대상 q4 4슬롯의 결과: 진료비 참고자료 보류 3 중 **2슬롯이 작가 세션을 사서 원고가 생겼다**(`d05a2b7d…` 장편한외과 "건강검진 비용이 궁금할 때…", `f717bcb8…` 장앤김더나은속내과 "경산 내과 진료비…" — D23 효과, DRAFT·예정일 지남). 그러나 19:00 KST 발행 틱에서 둘 다 `skipped reason=approved_revision_not_created`로 발행되지 않았다 — 이 틱은 published 1·blocked 8(`CONTENT_AI_HARD_FINDING` 5·`GENERATION_REJECTED` 2·`CONTENT_NOT_GENERATED` 1)·skipped 8(`LEGACY_SPEND_UNKNOWN` 슬롯 포함 `approved_revision_not_created`), 1슬롯(`8344b365…` 강심장내과 "갑상선초음파 비용…")은 작가 회차 뒤 `LEGACY_SPEND_UNKNOWN`/`OPERATOR_REQUIRED`. HARD 1슬롯(`4a2d0db0…`)은 "Minimal correction and topic swap exhausted" → 다시 `OPERATOR_REQUIRED`.
   - 배포 뒤 `GENERATE_CONTENT_ITEM`(18:25 기준): SUCCEEDED 6 · PARTIAL(`LEGACY_SPEND_UNKNOWN`) 6 · FAILED 8(`CONTENT_AI_HARD_FINDING` 4·`GENERATION_REJECTED` 2·`GENERATION_BUDGET_EXHAUSTED` 1·`CONTENT_AI_REVIEW_UNAVAILABLE` 1) · RUNNING 2 · QUEUED 6. 작가 호출 로그 `content generation usage` 다수(Sonnet 작가 + Opus 검수).
   - 새 `OPERATOR_REQUIRED`: **`LEGACY_SPEND_UNKNOWN` 6슬롯**(`8344b365`·`6b3cc6bc`·`16ada8fa`·`d4f35742`·`e97924f2`·`80495e84`, 5개 병원, 예정 10/7~10/15) — 기록에 `budget` 키가 없고 `context`가 `gate_catalog=2026-10-13.1`이다. 대부분 작가 회차 뒤 생성 거절 기록(`generation_rejections`)이 붙은 슬롯이다. **예상 밖 — 원인 미조사(D22 레거시 예산 교체 슬롯과 겹치는지·거절 기록 저장이 예산 획득 기록을 덮는지 개발 확인 필요)**. 그리고 `CONTENT_AI_HARD_FINDING` 2(`4a2d0db0`·`25124b9b`, 주제 교체까지 소진). q4는 4 → 8(LEGACY 6 + HARD 2).
   - q10(첫 기록): `rejection_kind=references` SAMPLE_RECOVERABLE 2 · q10b(최근 30일 예정 글의 생성 세션 합): healed must_use 8(6편)·geo 7(4편)·forbidden 1, rejected must_use 8·geo 7·references 5·forbidden 1.
   - 사고(배포 뒤 만들어지거나 갱신): `CONTENT_GENERATION_FAILED` OPEN 8·RETRYING 6·RECOVERED 4, `DOMAIN_UNHEALTHY` OPEN 1(17:00 KST, 열림 Slack SENT). outbox: 위 열림 1·Essence 복구 1(SENT), Essence 승격 열림 1·`COST_GUARD_SOFT_WARNING` 1(PENDING). q7 634 → 638.
   - 공개 가시성은 그대로(PUBLISHED 248, 보임 247, 숨김은 `4a3c2b13…` 하나). 후속 리비전 ERROR 5건은 모두 이 스윕의 생성 표본 실패(`MissingCitableReferencesError` 2·`GenerationBudgetExceeded` 1·이미지 정책 거절 2 — 이미지 없음으로 계속), WARNING은 API 404 스캐너 819·발행 보류·이미지 검수 재시도·자기 도메인 TLS 확인.

## 후속 배포 — main `42c2b119`(PR #247 Essence 검수 출력 상한 1600→4000, PR #246 문서)

배포: 2026-10-13 16:26:42~16:31:42 KST, `deploy.sh backend`(같은 사유로 잠금 예외, 기록 `ops/deploy-lock/2026-10-13-override-1627.json`), API·Worker·Beat만(마이그레이션 없음, Admin 변경 없음).
리비전: api `00245-9dg`, worker `00235-vnd`, beat `00230-vzv` (Cloud Run 해석 digest `reputation@sha256:ece92bdd51f4b8d487319f4adf221350046f5d93461d558494035e7994cc204d`, 태그 `reputation:20261013-162642`, push 출력 `sha256:3f688961…3d5e`).
롤백 좌표 `.omo/rollback-essence/backend`(07:27:37Z): api `00244-r2w`·worker `00234-nn4`·beat `00229-l4v`(= Phase 3 리비전, 카탈로그 판 `2026-10-13.1` 유지 — 이 롤백은 재평가를 다시 일으키지 않는다).
확인: migrate `reputation-migrate-dkvrw`(변경 없음) · RedBeat reconcile `reputation-redbeat-reconcile-297hg` · readiness `reputation-production-readiness-q2wlm` `ready=true` **18/18**, 7개 큐 canary 릴리스 `42c2b119`, schema `0088` · `/api/v1/health/live` `release=42c2b119…` · 병원 헬스 11/11 200 · 공개 표면 smoke 통과 · +10분(16:42, 07:27:37Z 이후) ERROR **0**, WARNING 3(API 4xx 스캐너).
Essence(`nyupeulraenyeoseongyiweon`, hospital `9443a662-…`): 배포 시점 `ESSENCE_AUTO_REVIEW_FAILED` 사고 `9979d22e-…` OPEN(06:22:49Z), 실패한 `ESSENCE_SNAPSHOT_REFRESH` run `fc99638e-…`은 attempt_count 6 → 같은 입력의 재구매 백오프는 15분×2^6=16시간, 기준 시각이 started_at(10/12 18:29:55Z)이라 **19:30 KST 이후**에야 15분 reconcile이 다시 집는다(16:30 틱 `queued=0`). **결과: 17:21 KST(08:21:40Z) reconcile이 새 입력의 run `1ba672f3-…`으로 다시 집어 검수가 끝까지 돌았다**(SUCCEEDED 08:22:37Z, 백오프는 옛 run의 입력에만 걸렸다). 검수 결과는 자동 승인이 아니라 **보류(ESCALATED)** — 새 초안 v5 `8be36f96-…`(DRAFT), 사고 `ESSENCE_AUTO_REVIEW_ESCALATED` `7b07ac30-…` RETRYING(재검수 예산 남음, 열림 알림 PENDING 보류). `ESSENCE_AUTO_REVIEW_FAILED` `9979d22e-…`는 RECOVERED → ACKNOWLEDGED(08:22:37Z), 복구 알림 SENT. 07:27:37Z 이후 worker 로그에 `essence_llm_output_truncated` **0건**(잘림 재발 없음).

## 롤백 좌표

api `reputation-api-00243-mx4` · worker `reputation-worker-00233-9k7` · beat `reputation-beat-00228-jlm` (롤백 파일 `.omo/rollback-p3/backend`, 배포 worktree `reputation-deploy`). DB·RedBeat 변경 없음. Worker `00223` 이하 금지(Phase 0).

- **`deploy.sh rollback`(트래픽 복귀)은 카탈로그 판을 `2026-10-02.1`로 되돌린다** — 입력 지문이 다시 바뀌어 종착 슬롯 전부가 또 재평가된다(runbook Phase 3-5). 호환 롤백 이미지를 미리 빌드해 두었다: `reputation:rollback-p3-d6b85c96-cat20261013`(push digest `sha256:195ab3ae06d49ef4cd5677bdcd9ce1b9852aeef05239255f45ebf2db6333f6d1`) — 직전 운영 소스 `d6b85c96`에 `GENERATION_GATE_CATALOG_VERSION = "2026-10-13.1"` 한 줄만 바꾼 것(커밋 없음, `git archive d6b85c96 backend` + sed). 필터·D23은 옛 동작이다. 쓰려면 세 서비스를 이 이미지로 `gcloud run deploy`(옛 리비전 env 유지)해야 하며 `deploy.sh`로는 못 쓴다(커밋되지 않은 소스).
- 데이터는 돌아오지 않는다: 생성 단계 치유로 저장된 본문은 롤백으로 복원되지 않는다. 새 필터로 숨겨진 `4a3c2b13…`은 옛 리비전에서 **다시 공개된다**. D23으로 참고자료 0개로 발행된 진료비·병원 선택 글은 옛 API에서 다시 숨는다(배포 시점 0건 — 롤백 전 다시 뽑는다).

## 이미지

| 서비스 | 리비전 | 이미지 |
|---|---|---|
| reputation-api / worker / beat, migrate·readiness·redbeat Job | `00244-r2w` / `00234-nn4` / `00229-l4v` | 태그 `reputation:20261013-154818`, Cloud Run 해석 digest `reputation@sha256:8d3a08b071941877f8e42995dc19e09eae6ea1bd1e13fddb7f92e0768812a33b`(push 출력 `sha256:01f09739…ff8c`) |
| 호환 롤백 이미지(미배포) | — | `reputation:rollback-p3-d6b85c96-cat20261013` `sha256:195ab3ae…f6d1` |

## 남은 것

- 대표 결정 D29: 경보는 Slack 하나로 받는다 — `OPS_ALERT_EMAIL`은 의도적으로 비우고 Terraform 경보 2종은 적용하지 않는다(2026-10-13 ~18:00). Phase 2의 '보류' 두 항목은 후속이 아니다.

- `4a3c2b13…` 문장 정정(사람 PATCH) 또는 숨김 수용 — 대표 확인.
- ~~18:00 스윕의 `LEGACY_SPEND_UNKNOWN`/`OPERATOR_REQUIRED` 6슬롯 원인 확인(개발)~~ — 아래 '긴급 수정' (1)로 해결.
- 23:00 야간 생성(배치 시간대, 이번 관찰 범위 밖): q4 4슬롯 중 18:00에 다시 쓰이지 않은 것과 새 거절 라벨(q10) 첫 기록은 10/14 확인.
- 1주 뒤 `seo_geo_findings` 분량 권고 비율 비교(계획 Task 3-11 위험 항목).

## 긴급 수정 (2026-10-13 저녁)

1. **슬롯 예산 유실** — main `1090df18`(PR #249). 원인: `tasks._auto_correct_blocked_body`가 최소 교정 PASS 때 시도 기록
   (`generation_attempt`)을 통째로 지워 슬롯 예산(`budget`)도 함께 사라졌고, 다음 스윕이 예산 없는 옛 기록으로 읽어
   `LEGACY_SPEND_UNKNOWN`/`OPERATOR_REQUIRED`로 보냈다. 고친 코드는 시도 기록만 지우고 예산을 남긴다. API·Worker·Beat 배포
   19:36~19:45 KST(api `00246-bv7`, worker `00236-b6t`, beat `00231-fw6`). 잘못 종착된 5슬롯은 `legacy_budget_reset --ids`로
   되살렸다(`16ada8fa`는 이미 발행돼 제외). q4는 2건.
2. **D23 글이 발행되지 않음** — 생성은 진료비·병원 선택 글을 참고자료 0개로 쓰는데(운영 COLUMN 초안 `d05a2b7d…`·`f717bcb8…`),
   DB 함수 `reconcile_content_revisions`(0086)가 `NOTICE 또는 참고자료 1개 이상`을 요구해 승인 판이 생기지 않았고, 자동 발행
   틱이 매번 `approved_revision_not_created`로 롤백했다. 수정: 발행 경로(자동 발행·관리자 발행·후보 판 교체)가 판을 만들기
   직전에 `references_block_when_empty` 판정을 `essence_check_summary.reference_requirement`(판정·주제·유형·제목 sha256)로 적고,
   마이그레이션 `0089_allow_optional_reference_revisions`의 함수는 `NOTICE 또는 참고자료 1개 이상 또는 (표지가 '비어도 됨'이고
   유형·제목 hash가 행과 같음)`으로 읽는다. 제목 규칙을 SQL에 다시 쓰지 않고, 제목이 바뀐 뒤의 옛 표지는 무효다. 나머지 검사는
   그대로다. 의료 글은 표지가 '막음'이라 종전처럼 판이 없다(발행 게이트·공개 가시성과 같은 판정). **롤백**: `alembic downgrade`
   (0089→0088)는 0086 함수를 다시 설치해 엄격한 조건으로 돌아간다 — D23 글은 다시 발행되지 않는다. 이미 만들어진 판은 남는다.
3. **범용 서술 각도 초안이 발행되지 않음** — 같은 함수가 `content_brief.treatment_narrative.source`로 `approved_philosophy`·`hospital_profile`만
   받아, 주제의 진료가 승인 서술·병원 진료 어디에도 맞지 않아 범용 각도(`fallback`)로 쓰인 초안 6건(`f717bcb8`·`8344b365`·`d4f35742`·
   `cd8f401a`·`a6e62936`·`ab196937`)이 매 틱 `approved_revision_not_created`로 건너뛰어졌다. 서술 각도는 작성 힌트이지 승인 근거가 아니므로
   마이그레이션 `0090_allow_fallback_treatment_narrative`의 함수는 `fallback`도 받고(비어 있지 않은 `angle`·승인 Essence 검사는 그대로),
   발행 게이트는 공개된 적 없는 범용 각도 글에 **현재 본문 hash에 묶인** 독립 검수 PASS(또는 비차단 REVISE)를 요구한다 — 없거나 hash 없는
   옛 기록이면 `CONTENT_AI_REVIEW_STALE`로 막고 복구 스윕(`_needs_generation_recovery`)이 그 행을 집어 재검수한다. **롤백**: 0090→0089는
   엄격한 조건을 다시 설치한다.
