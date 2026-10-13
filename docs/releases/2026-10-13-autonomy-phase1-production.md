# 2026-10-13 자율 운영 Phase 1 운영 배포 — 사고 소유 레지스트리·월간 오탐 제거·리드 부수효과 재시도

배포일: 2026-10-13 13:03~13:12 KST(backend 13:03:23~13:10, admin 13:11:14~13:12:07) · 배포 소스: main `08abf747`(PR #241 Phase 1: P1-A 사고 레지스트리·P1-B 월간·V0·리드, 병합 `da30ff5b` + PR #242 레거시 예산 교체 CLI)
배포 대상: API·Worker·Beat·Admin (Site 변경 없음) · DB head `0087_add_operation_run_redispatch_count` → `0088_add_outbox_open_pairing_index`(추가형 `notification_outbox` 부분 인덱스) · RedBeat `2026-10-09.1` → `2026-10-12.1`
리비전: api `00242-jsw`, worker `00232-vx2`, beat `00227-2mv`, admin `00106-psm` (site `reputation-site-cr057b9d1e` 그대로)
(이전: api `00241-lhd`, worker `00231-4rx`, beat `00226-gpw`, admin `00105-x4r`)
배포 PC: 작업 PC(wjlee@motionlabs.kr) · 깨끗한 worktree `reputation-deploy`(detached `08abf747`). **같은 날 두 번째 배포 — 대표 승인("배포 그냥 하면 되지", 2026-10-13 채팅). RB 하루 한 번 규칙의 예외.**
배치 시간대: 12:00 복구 스윕 ±45분을 피해 12:46 이후로 정했고, Worker 교체가 13:00 발행 틱과 겹치지 않게 13:03에 시작했다.

## 왜 했는가 — 2026-10-11 감사가 확인한 사람이 볼 필요 없는 알림·영구 OPEN 사고

| 결함 | 원인 |
|---|---|
| 스윕이 재시도를 소유하는 사고가 곧바로 '조치 필요'로 나감 | 사고 종류별 소유(대상·조용함·스윕 소유·기한)가 코드 곳곳에 흩어져 있었다 |
| 영구 OPEN 사고·주인 없는 RETRYING | 닫는 근거가 없는 종류, 기한 없는 RETRYING |
| 월초 오탐·매니페스트 없는 달 | 8일 최종화 이전 판정, 공백 판정이 전달 게이트와 달랐다 |
| 도입문의 접수 Slack/SMS 실패가 조용히 유실 | 재시도·사고화 경로 없음 |

계획: [사고 레지스트리](../plans/2026-10-11-p1-incident-registry.md) · [월간·리드](../plans/2026-10-11-p1-monthly-leads.md) · [MasterPlan](../plans/2026-10-11-autonomy-masterplan.md).

## 바뀐 계약 (정본: CLAUDE.md 2.23, 알림 정책)

- P1-A: `incident_types.INCIDENT_TYPE_POLICIES` 한 표(대상·조용함·스윕 소유·기한), 스윕 소유 사고는 RETRYING+기한으로 열고 기한이 지나야 사람의 일, 기한 없는 RETRYING 24시간 기본 기한, 종류별 resolver(`incident_backlog.RESOLVERS`), 나간 열림에 복구 알림 짝·발송 전 해결은 `INCIDENT_RESOLVED_BEFORE_SEND`, 복구 알림은 같은 에피소드 열림이 나간 뒤에만, RECOVERED 24시간 경과 사고 조용한 확인 처리, 사후검수 UNCERTAIN 7일 뒤 상위 재검수, `notification_outbox` 부분 인덱스(0088).
- P1-B: 월간 측정 사고는 8일 최종화 뒤 판정까지 기계 소유, 월간 공백 요약 1~8일, `MANIFEST_MISSING` 안내, 코호트 비용 경고선을 `COST_GUARD_MONTHLY_SOV_QUERIES`에서 계산(`SOV_MONTHLY_COHORT_LIMIT` 폐지 — 운영 env에서도 제거), V0 자동 재실행 6시간×3회, 리드 진단 측정 백오프 5회, 도입문의 Slack 1시간 재발송·문자 1회 재시도·최종 실패 사고화.
- Admin: `V0_REPORT_FAILED` 운영자 설명, 원장 안내 문자 '수신 불명(재발송 안 함)' 문구.
- PR #242: 10/9 이전 빈 슬롯의 레거시 생성 예산을 1회 교체하는 CLI(`python -m app.utils.legacy_budget_reset`, 기본 dry-run).
- **이후 Worker를 `00223` 이하로 롤백하지 않는다.**

## 배포 절차와 증거

1. 대표 승인(두 번째 배포): "배포 그냥 하면 되지"(2026-10-13 채팅).
2. Phase 0 +2시간 점검(이 배포 전, 11:38 KST): P0 리비전 `api-00241-lhd`·`worker-00231-4rx`·`beat-00226-gpw`·`admin-00105-x4r`의 ERROR **0건**(범위 2026-10-13T00:55Z~02:38Z). `expired authenticated dispatch envelope` 0건. WARNING은 API 4xx 스캐너 요청(261)·Admin 4xx(5)·Worker 기존 발행 보류(`auto publish blocked`, 33)뿐.
3. `.env.production` ↔ 운영 `reputation-api/worker/beat` env 대조(`deploy-envdiff-p1.py`, 값 미출력): 사전 차이는 `SOV_MONTHLY_COHORT_LIMIT`의 "운영에만 있음" **정확히 1건**(의도한 제거), 파일에만·값 다름 0. 운영 plaintext 49→48키, secret 참조 17키, 파일 45키. 사후 차이 0, 새 리비전 3개 env에 키 없음. Slack: `SLACK_WEBHOOK_URL_DEV` v1 disabled, `SLACK_WEBHOOK_URL` v5 enabled.
4. 사전 읽기 전용 조회(base64 `python -c`, P0 이미지 migrate Job `reputation-migrate-hq8c6`, 11:37 KST): `outbox_rows` 631(≤100만 → 표준 경로; 0088 적용 0.3초) · `open_notice_rows` 401 · 인덱스 선재 없음 · alembic `0087` · 기한 없는 RETRYING **0건** · 스윕 소유 종류의 대기 열림 알림 0 · 닫힌 사고의 대기 열림 알림 0 · RECOVERED 24시간 경과 190 · 리드 접수 보정 대상 0(Slack FAILED·SMS FAILED·장기 PENDING·SENDING 모두 0) · 코호트 ACTIVE 11/11 · 진행 중 실행 `ESSENCE_SNAPSHOT_REFRESH` 1건(배포 무관).
5. 가드: `make db-budget-guard`(75/80) · `make copy-guard` 통과. main CI(`08abf747`) success, PR #241·#242 검사 10/10 SUCCESS. legacy publish 사전 확인(VPC): `READY`, open_legacy_transport 0, unapplied_sent 0, convertible 0(total_historical 16, unknown 2). 증거 `.omo/evidence/deploy-preflight/legacy-publish-retirement-08abf74795ee585c341a17143a7b1cde3253a1cd-20261013T040348Z.json`(배포 worktree).
6. 트래픽: 배포 전 5개 서비스 모두 `latestRevision=True`·100%(고정 없음) / 배포 뒤 4개 서비스 `traffic[0]`=latestReady·100%·`latestRevision=True`, site 그대로.
7. `deploy.sh backend` 13:03:23~13:10(약 7분): migrate `reputation-migrate-wtqjc`(13:05:57, `Running upgrade 0087_add_operation_run_redispatch_count -> 0088_add_outbox_open_pairing_index`) → worker `00232-vx2`(13:06:07) → RedBeat reconcile `reputation-redbeat-reconcile-ltnd5`(13:06:32) → beat `00227-2mv`(13:06:54) → readiness `reputation-production-readiness-wnf2j`(~13:10:02) `ready=true`, **검사 17/17**, 7개 큐 canary 현재 릴리스 `08abf747`, `schema_revision=0088_add_outbox_open_pairing_index`, `slack_webhook_valid={operator: alive, developer: not_configured}`, `facts.auto_publish_hold={active: false, all_hospitals: false, hospital_count: 0}` → api `00242-jsw`(13:10:13).
8. `deploy.sh admin` 13:11:14~13:12:07, 별도 롤백 파일 `.omo/rollback-p1/admin`(backend `.omo/rollback-p1/backend`).
9. RedBeat: `reconcile_redbeat_schedule --check`(migrate `reputation-migrate-m79g6`) `clean=true`, `stored_version=expected_version=2026-10-12.1`, 이름 34개, orphan·stale 0.
10. 새 리비전(api·worker·beat·admin) ERROR **0건**(13:13 조회, 범위 04:03:23Z 이후; 14:00 틱·배포 +2시간(15:10) 조회 결과는 후속 커밋으로 덧붙인다). `expired authenticated dispatch envelope` 0건.
11. 병원 헬스 11곳: **11/11 200**, slug·canonical_host·hospital_id 일치, release=`reputation-site-cr057b9d1e`. 공개 루트·Admin 로그인·`/api/v1/health/live` 200.
12. `daily-fleet-heartbeat`(migrate `reputation-migrate-rlz69`) task `636fc679-…`가 새 워커 `00232-vx2`에서 13:22:12 `succeeded` `before_summary_window`. `dispatch_skipped`·`dispatch_expired_dropped` 0.
13. 배포 뒤 `ops_sql_report --only=q1,q2,q7`(migrate `reputation-migrate-fx962`): q7 `outbox_rows` 632 · `open_notice_rows` 401(사전 대비 +1 = 13:00 `GENERATION_BLOCKED_DIGEST`, 배포 전 옛 워커) · q1 `retrying_no_sla` **0**, 보조 목록(q1b) 비어 있음 · q2 HOLD/FAILED는 모두 10/8 이전 옛 `SLACK_DEV` 행(새로 쌓인 것 없음).
14. 첫 틱 효과(사후 SQL `reputation-migrate-4w656` 13:13, `reputation-migrate-dwm8b` 13:36): 기한 단 RETRYING 0(대상 없음) · 근거로 닫힌 사고 0 · 연결 성공으로 닫힌 RETRYING 0 · RECOVERED→ACK **190**(13:07~13:10, 틱당 50; CONTENT_GENERATION_FAILED 151·MONTHLY_SOV_MEASUREMENT_FAILED 12·ESSENCE_AUTO_REVIEW_ESCALATED 8·DOMAIN_UNHEALTHY 6·MONTHLY_DOCTOR_PDF_BLOCKED 5·MONTHLY_SLOT_GENERATION_FAILED 3·BACKGROUND_TASK_FAILED 2·ESSENCE_AUTO_REVIEW_FAILED 2·CHANNEL_SOURCE_FETCH_FAILED 1, Slack 없음) · 발송 전 종결 outbox 0 · 배포 뒤 생긴 outbox **0행**(복구 `[Report]` 0건, 새 `[Error]` 0건) · 새로 열린 사고 0 · 남은 OPEN/RETRYING은 사전과 같다(사후검수 74·콘텐츠 28·원장 PDF 4·Essence 4·월간 측정 2·백그라운드 2·리드 2).
15. 레거시 예산 1회 교체(대표 결정 D22): dry-run(`reputation-migrate-s2rr2`) 23건 — `CONTENT_NOT_GENERATED` 12 + `LEGACY_SPEND_UNKNOWN` 11, 10:13 운영 조회 증거(`q4-rows-before.jsonl`)의 id와 정확히 일치 → apply(`reputation-migrate-x9klc`, 13:19, 사유 "Phase 0 배포 뒤 레거시 예산 1회 교체 — 2026-10-13 대표 승인(D22)", actor `system:legacy-budget-reset-20261013`) **REPLACED 23/23**. 뒤이은 `ops_sql_report --only=q4`(`reputation-migrate-5lnqh`): `LEGACY_SPEND_UNKNOWN` 0 · `CONTENT_NOT_GENERATED` 0, 남은 `OPERATOR_REQUIRED` DRAFT는 `MISSING_REFERENCES` 3·`CONTENT_AI_HARD_FINDING` 1(P0 기록 때 2; 12:00 KST에 주제 교체 1건이 감사에 있다 — 같은 글인지는 확인하지 않았다). 두 번째 dry-run(`reputation-migrate-4nzwr`) 0건(멱등). 교체는 사고를 닫지 않는다 — 해당 `CONTENT_GENERATION_FAILED` 사고(OPEN 28 중 23, 열림 알림 없음)는 다음 복구 스윕(18:00)·야간 생성(23:00)이 슬롯을 다시 쓰고 발행 근거가 생기면 resolver가 닫는다.

## 체크리스트 예측과 달랐던 것

- P0의 `OPERATION_RECOVERY_EXHAUSTED`/`OPERATION_RUN_ABANDONED` 사고(`59c83853-…`)는 Phase 1 배포 **전**인 12:00:00 KST에 P0 워커의 resolver(`stopped_runs_recovered`, run `79174290-…`)가 닫았다(`slack_suppressed=true`, 곧바로 ACKNOWLEDGED). 그래서 체크리스트가 예상한 "오늘 23:00 이후 복구 `[Report]` 1건"은 나가지 않는다. 이 사고의 열림 알림(10:00 SENT)은 채널에서 짝 없는 열림으로 남는다(P0 코드의 침묵 종료, Phase 1 이전 동작).
- 새 resolver/넓어진 근거 대상(`LEAD_DIAGNOSIS_FAILED` 1·`RECOVER_LEAD_MEASUREMENT` 1·`MONTHLY_SOV_MEASUREMENT_FAILED` 2)은 첫 틱에서 닫히지 않았다 — 근거(진단 완료·갈음, 같은 기간 측정 성공)가 아직 없다. 복구 알림 0건.
- `ESSENCE_SNAPSHOT_REFRESH` run `f22a1e70-…`(10/13 03:31 KST 생성)이 사전 조회 때 REQUESTED, 11:45 KST부터 RUNNING이다. 배포 전부터의 상태로 배포와 무관.

## 롤백 좌표

api `reputation-api-00241-lhd` · worker `reputation-worker-00231-4rx` · beat `reputation-beat-00226-gpw` · admin `reputation-admin-00105-x4r`. DB `0088`은 되돌리지 않는다(추가형). RedBeat는 롤백 시 옛 이미지(`reputation@sha256:baf2f6b6…`)로 `--apply` 재조정이 필요하고 migrate·readiness Job 이미지도 옛 것으로 맞춘다. 롤백 파일 `.omo/rollback-p1/backend`(2026-10-13T04:04:12Z)·`.omo/rollback-p1/admin`(2026-10-13T04:11:20Z) — 배포 worktree `reputation-deploy`. P0 좌표는 `.omo/rollback-p0/`에 보존(쓰지 않는다). 롤백 주의: 발송 전 종결된 outbox·근거로 닫은/확인 처리한 사고(오늘 190건 ACK 포함)는 되살아나지 않고, 옛 발송기는 보류해 둔 열림 알림을 한꺼번에 내보낸다. 롤백 리비전 env에는 `SOV_MONTHLY_COHORT_LIMIT`이 남아 있다. 레거시 예산 교체는 데이터 변경이라 롤백으로 되돌아오지 않는다. Worker `00223` 이하 금지.

## 이미지

| 서비스 | 리비전 | 이미지 digest (Cloud Run 해석값) |
|---|---|---|
| reputation-api | `00242-jsw` | `reputation@sha256:57c2f02f2368851980031760bca0695ce9bcd8d72bf6da4f66593d22da989ab1` |
| reputation-worker / beat / migrate job | `00232-vx2` / `00227-2mv` | 위와 같음(태그 `reputation:20261013-130323`, push 출력 digest `sha256:8feb5259…dccf8`) |
| reputation-admin | `00106-psm` | `admin@sha256:72f12b57f8928bf472fb7d862efdda1f97baf04b7e4c40612bcefaa71001dfce`(태그 `admin:20261013-131114`, push 출력 `sha256:5b1cca11…a546`) |

## 다음 날(10/14) 지표

| 지표 | 기대 | 결과 |
|---|---|---|
| 기한 없는 RETRYING | 0 | 배포 직후 0 · 10/14 미측정 |
| 스윕 소유 사고가 기한 안에 사람의 일로 나간 수 | 0 | 미측정(10/14) |
| 나간 `INCIDENT_OPEN`에 짝 없는 복구/열림(채널 정합) | 0 (P0 `OPERATION_RUN_ABANDONED` 1건은 Phase 1 이전 침묵 종료) | 미측정(10/14) |
| `POST_PUBLISH_REVIEW_FLAGGED` 감소(03:10 재검수, 후보 62) | 감소, Slack 0 | 미측정(10/14) |
| 레거시 교체 23슬롯 재생성(18:00 스윕·23:00 야간) | 원고 생성·해당 사고 닫힘 | 미측정 |
| 일일 요약(18시) | "점검 이상 없음" 또는 사람의 일 건수 | 미측정 |
| 만료 봉투 ERROR | 0 | 미측정(10/14) |

## 일회성 효과·남은 것

- 예상된 Slack: 도입문의 접수 보정 사고 0건(사전 조회 0건과 일치), 새 resolver로 닫힌 사고의 복구 `[Report]` 0건. RECOVERED 24시간 경과 190건 ACK(Slack 없음).
- 다음 1~8일(11/1~11/8) 09:00 월간 공백 요약에서 `MANIFEST_MISSING`(매니페스트 없는 병원·월마다 1건, 전달 불가 안내) — 오늘은 해당 없음.
- 기존 HOLD 행(옛 `SLACK_DEV` `DELIVERY_OUTCOME_UNKNOWN`, 열림 43·복구 5)은 그대로다(이 배포가 만든 것 아님).
- Admin 새 문구(`/leads` 문자 '수신 불명', 운영센터 `V0_REPORT_FAILED` 설명)는 로그인이 필요해 사람이 1회 확인한다.
- 문서 후속: runbook의 서명 시제 정리, `deploy.sh` "6개 작업 큐" 문구(실제 7개) — P0 기록과 같다.
