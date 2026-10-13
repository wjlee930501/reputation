# 2026-10-13 자율 운영 Phase 0 운영 배포 — 수렴하지 않는 재시도·재배달 수렴

배포일: 2026-10-13 09:55~10:03 KST(backend 09:55:31~10:00:43, admin 10:01:37~10:02:36) · 배포 소스: main `31751a88`(PR #239, Phase 0: P0-A 생성 예산·P0-B 재시도 정책·P0-C 디스패치/복구/운영 조회 CLI)
배포 대상: API·Worker·Beat·Admin (Site 변경 없음) · DB head `0086_preserve_historical_publications` → `0087_add_operation_run_redispatch_count`(추가형 `operation_runs.redispatch_count NOT NULL DEFAULT 0`)
리비전: api `00241-lhd`, worker `00231-4rx`, beat `00226-gpw`, admin `00105-x4r` (site `reputation-site-cr057b9d1e` 그대로)
(이전: api `00240-c9n`, worker `00230-s6m`, beat `00225-npd`, admin `00104-gkq`)
배포 PC: 작업 PC(wjlee@motionlabs.kr) · 깨끗한 worktree `reputation-deploy`(detached `31751a88`, `.env.production` 복사)에서 실행 — 본 체크아웃의 untracked 3개(`.npm/`·`Library/`·`aside-test.js`)는 release revision 검사에 걸리므로 override 없이 worktree로 피했다.

## 왜 했는가 — 2026-10-11 감사가 확인한 수렴하지 않는 경로

| 결함 | 실제 원인 (운영 DB·로그로 확인) |
|---|---|
| 교체한 주제가 곧바로 `WRITER_RESULTS_EXHAUSTED` (F1·F2) | 주제 교체 뒤에도 작가 예산이 이어져 새 주제가 첫 시도부터 소진 상태였다. |
| 공급자 장애가 글 예산을 태움·INPUT_CHANGE 동결 (F9) | 환경 장애와 표본 실패가 같은 예산을 썼고, 원고 미생성이 `INPUT_CHANGE_REQUIRED`로 굳었다. |
| 재배달이 끝나지 않음·잔재 RUNNING (F8) | QUEUED 장기 체류(큐 우선순위 적체)를 고정 45분으로 재배달했고, lease 없는 35일 RUNNING 1건이 정리되지 않았다. |

## 바뀐 계약 (정본: CLAUDE.md 2.22·알림 정책)

- P0-A: 주제 교체 뒤 새 작가 예산, 소진 즉시 교체 후보(D1·D17), 본문 재생 키(`writer:{ctx}:{topic}:{claim_token}:{fp}`).
- P0-B: 공급자 장애 일일 예산(4회 초기화)·3일 창(`environment_since`, D14·D16), 원고 미생성 재분류, 교체 계단 확대, 인용 없는 모델 HARD → 주제 교체 계단. `CONTENT_TOPIC_SWAP_OPERATOR_DECIDES` 기본 꺼짐(D14, 운영 env에 키 없음).
- P0-C: 봉투 서명 6시간(`DISPATCH_TTL_SECONDS=DISPATCH_MAX_LIFETIME_SECONDS`), 재배달 예산 3회(`redispatch_count`, 45m→90m→3h→6h 백오프), lease 인수 확대, 10일 무변화 RUNNING 잔재 FAILED 종료(`OPERATION_RUN_ABANDONED`, `MONTHLY_SOV_PERIOD`만 20일; D15), `ops_sql_report` 읽기 전용 CLI(Q1~Q6), Admin 운영센터 문구 3종.
- **이후 Worker를 `00223` 이하로 롤백하지 않는다**(6시간 봉투 거절).

## 배포 절차와 증거

1. `.env.production` ↔ 운영 `reputation-api/worker/beat` env 키 대조(`deploy-envdiff-p0.py`, 값 미출력): 세 서비스 모두 파일에만·운영에만·값 다름 **0**. 운영 plaintext 49키·secret 참조 17키. 파일의 `INQUIRY_SMS_PROVIDER`·`INQUIRY_SMS_SENDER_NO`·`NHN_SMS_APP_KEY`·`SENTRY_DSN`은 빈 값(주입 안 됨, 운영과 일치). `SOV_MONTHLY_COHORT_LIMIT` 유지(Phase 1 미배포). Slack: `SLACK_WEBHOOK_URL_DEV` v1 disabled, `SLACK_WEBHOOK_URL` v5 enabled. 배포 뒤 같은 대조 재실행도 차이 0.
2. 사전 읽기 전용 조회(base64 `python -c`, migrate Job `reputation-migrate-66vwc`, 09:52 KST): 10일 잔재 RUNNING `NIGHTLY_CONTENT_GENERATION` 1건(35일 idle) · `exhausted_days` 비정수(runbook 원문 최상위 경로) **0** · 교체된 슬롯(`swapped`) 1 · `swap_candidates` 0 · 진행 중 실행은 위 1건과 `ESSENCE_SNAPSHOT_REFRESH` REQUESTED 1건(2시간 idle, 배포와 무관) · alembic `0086`.
3. 가드: `make db-budget-guard`(75/80) · `make copy-guard` 통과.
4. legacy publish 사전 확인(VPC, 10/10 이미지): `READY`, open_legacy_transport 0, unapplied_sent 0, convertible 0, total_historical 16, unknown 2. 증거 `.omo/evidence/deploy-preflight/legacy-publish-retirement-31751a88586ebf26aea57a1cc30e604efb7f6b0c-20261013T005556Z.json`(배포 worktree).
5. 트래픽: 배포 전 5개 서비스 모두 `latestRevision=True`·100%(고정 없음) / 배포 뒤 4개 서비스 `traffic[0]`=latestReady·100%·`latestRevision=True`.
6. `deploy.sh backend` 09:55:31~10:00:43(약 5분): migrate `reputation-migrate-x7mph`(09:57:45~09:57:57, `Running upgrade 0086_preserve_historical_publications -> 0087_add_operation_run_redispatch_count`) → worker `00231-4rx`(09:58:04) → RedBeat reconcile `reputation-redbeat-reconcile-tpbhs` → beat `00226-gpw`(09:58:55) → readiness Job `reputation-production-readiness-tckzw`(09:59:31~10:00:09) `ready=true`, **검사 17/17 통과**, 7개 큐 canary 현재 릴리스 `31751a88`, `schema_revision=0087`, `slack_webhook_valid={operator: alive, developer: not_configured}` → api `00241-lhd`(10:00:17).
7. `deploy.sh admin` 10:01:37~10:02:36, 별도 롤백 파일 `.deploy-rollback.admin-20261013`(backend 좌표 보존, 사본 `.deploy-rollback.backend-20261013`).
8. 새 리비전(api·worker·beat·admin) ERROR **0건**(조회 10:04 KST, 범위 00:55:32Z 이후). `expired authenticated dispatch envelope` 0건. WARNING은 10:00 catch-up 발행(새 워커)의 기존 보류 16건뿐(`CONTENT_NOT_GENERATED` 12·`MISSING_REFERENCES` 3·`CONTENT_AI_HARD_FINDING` 1).
9. 병원 헬스 11곳: **11/11 200**, slug·canonical_host·hospital_id 일치, release=`reputation-site-cr057b9d1e`. 공개 루트·Admin 로그인·`/api/v1/health/live` 200.
10. 6시간 봉투 수락: `run_beat_entry daily-fleet-heartbeat`(migrate `reputation-migrate-v2gp6`) task `a92432bd-43a3-468d-a27d-662551d510a0`가 새 워커 `00231-4rx`에서 10:03:06 `succeeded` `before_summary_window`(18시 전 정상). 만료 봉투 ERROR 0건.
11. 35일 RUNNING 잔재: run `79174290-3b11-44f6-a1c9-f71c0a50d6e3`(`NIGHTLY_CONTENT_GENERATION`, 2026-09-08 생성)이 새 워커의 첫 자율 복구에서 09:59:00 `FAILED`(`OPERATION_RUN_ABANDONED`). 사고 1건 `OPERATION_RECOVERY_EXHAUSTED`(`safe_error_code=OPERATION_RUN_ABANDONED`, id `59c83853-…`), outbox 1건 `SLACK_DEV`→운영 채널(`channel_used=SLACK`, 단일 채널 모드) 10:00:04 SENT(HTTP 200). 예상 1/1과 일치. 배포 뒤 RUNNING/QUEUED 잔재 없음(REQUESTED `ESSENCE_SNAPSHOT_REFRESH` 1건 그대로).
12. 배포 뒤 `ops_sql_report --only=q1,--only=q3,--only=q4`(migrate `reputation-migrate-t8brb`): Q4 `OPERATOR_REQUIRED` DRAFT — `CONTENT_NOT_GENERATED` 12(9개 병원), `LEGACY_SPEND_UNKNOWN` 11(9개 병원, 10/11 조회 12), `MISSING_REFERENCES` 3, `CONTENT_AI_HARD_FINDING` 2. Q4 동결 슬롯 처리("작업 다시 시도"·"레거시 예산 교체 후 다시 시도")는 이 기록 시점에 하지 않았다(사람 확인 대기).

## 롤백 좌표

api `reputation-api-00240-c9n` · worker `reputation-worker-00230-s6m` · beat `reputation-beat-00225-npd` · admin `reputation-admin-00104-gkq`. DB `0087`은 되돌리지 않는다(추가형). 롤백 파일 `.deploy-rollback`(backend, 2026-10-13T00:56:21Z)·`.deploy-rollback.admin-20261013`(admin, 2026-10-13T01:01:44Z) — 둘 다 배포 worktree `reputation-deploy`에 있다. Worker `00223` 이하 금지.

## 이미지

| 서비스 | 리비전 | 이미지 digest (Cloud Run 해석값) |
|---|---|---|
| reputation-api | `00241-lhd` | `reputation@sha256:baf2f6b65a9957e47562839226d1b65c2ac12d7912ddc017db9d042062e5d865` |
| reputation-worker / beat / migrate job | `00231-4rx` / `00226-gpw` | 위와 같음(태그 `reputation:20261013-095532`, push 출력 digest `sha256:e13c4802…db55e`) |
| reputation-admin | `00105-x4r` | `admin@sha256:9ace790d05e9f46bcd7451facb0894b34566ffcfa440129667414f3ece92bb37`(태그 `admin:20261013-100137`, push 출력 `sha256:dda8c7b6…f3b2`) |

## 다음 날(10/14) 지표 — MasterPlan §5 Phase 0 (f)

| 지표 | 기대 | 결과 |
|---|---|---|
| 교체 뒤 첫 시도 `WRITER_RESULTS_EXHAUSTED` 슬롯 | 0 | 미측정(10/14) |
| `OPERATION_REDISPATCH_EXHAUSTED` 사고 | ≤ 원인 1건 | 미측정(10/14) |
| 만료 봉투 ERROR | 0 | 미측정(10/14) |
| 일일 요약 | "점검 이상 없음" 또는 사람의 일 건수 | 미측정(10/14) |

## 일회성 효과·남은 것

- 예상된 Slack 1건: `OPERATION_RECOVERY_EXHAUSTED`/`OPERATION_RUN_ABANDONED`(개발 대상, 운영 채널로 전송) — 실제 1건.
- 10/12·10/13 예정 글 12편이 원고 없이 `CONTENT_NOT_GENERATED`/`OPERATOR_REQUIRED`로 동결돼 있다(Q4). 운영센터 "작업 다시 시도"로 한 번 풀어 줄 대상이다.
- `LEGACY_SPEND_UNKNOWN` 11건은 글별 사람 확인(일괄 금지).
- Phase 1(사고 레지스트리·월간·리드)은 이 배포에 없다: 하루 관측 뒤 별도 배포.
- 문서 후속: runbook의 서명 수명 시제 정리(같은 문단에 "1시간 그대로"와 "6시간으로 올렸다"가 함께 있음), `deploy.sh` "6개 작업 큐" 문구(실제 7개).
