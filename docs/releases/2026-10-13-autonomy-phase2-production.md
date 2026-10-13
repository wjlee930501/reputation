# 2026-10-13 자율 운영 Phase 2 운영 배포 — 온보딩 자동화·인프라 감시 보강

배포일: 2026-10-13 15:03~15:13 KST(backend 15:03:05~15:10:48, admin 15:11:41~15:12:44) · 배포 소스: main `d6b85c96`(PR #244 `claude/autonomy-p2`: P2-A 온보딩 + P2-B 인프라)
배포 대상: API·Worker·Beat·Admin (Site 변경 없음) · **마이그레이션 없음** — DB head `0088_add_outbox_open_pairing_index` 그대로 · RedBeat `2026-10-12.1` 그대로(재조정 clean)
리비전: api `00243-mx4`, worker `00233-9k7`, beat `00228-jlm`, admin `00107-d26` (site `reputation-site-cr057b9d1e` 그대로)
(이전: api `00242-jsw`, worker `00232-vx2`, beat `00227-2mv`, admin `00106-psm`)
배포 PC: 작업 PC(wjlee@motionlabs.kr) · 깨끗한 worktree `reputation-deploy`(detached `d6b85c96`). **같은 날 세 번째 배포 — 대표 지시("준비되는 즉시 배포", 2026-10-13, 배치 창 밖이면 하루 여러 번 — D28).**
배치 시간대: 12:46~17:14 배포 가능 구간 안. 하루 잠금(`gs://reputation-reports-mso-platform-481505/ops/deploy-lock/2026-10-13.json`)은 거절 없이 통과해 `--override-reason`을 쓰지 않았다.

## 왜 했는가

[MasterPlan](../plans/2026-10-11-autonomy-masterplan.md) Phase 2 — 계약 인수 뒤 사람이 챙겨야 했던 온보딩 단계(Essence 재검수·첫 일정·자기 도메인 DNS 확인)와, 감시 경로 자체가 조용히 멈추는 인프라 공백을 메운다.
계획: [온보딩 P2-A](../plans/2026-10-11-p2-onboarding.md) · [인프라 P2-B](../plans/2026-10-11-p2-infra-deploy.md).

## 바뀐 계약 (정본: CLAUDE.md 2.25, 배포 안내 Phase 2)

- P2-A: Essence 자동 재검수 1h/4h/12h·상충/주입 의심 보류는 곧바로 사람·처리 ERROR 필수 자료 제외(D26: 다른 필수 자료가 PROCESSED면 1시간, ERROR뿐이면 6시간 뒤), Essence 자동 승인 뒤 첫 발행 일정 자동 생성(`system:essence-auto-approval`)과 첫 달 안분, 온보딩 정체 사고 4종, 계약 등록 때 리드 시드·자동 입력 초안(Admin), 대기 자기 도메인 15분 DNS 확인(D27: 14일 창 뒤에도 15분마다, 창이 지난 뒤 실제 불일치를 관측한 회차에만 사람의 일).
- P2-B: readiness `pipeline_watchdog_token_configured`(18개 검사), 운영 경보 메일(`OPS_ALERT_EMAIL`), Beat liveness, 비용 가드 fail-open·사용량 spool 잘림 개발 사고, `deploy.sh` 규칙 코드화(운영 env 정본·배치 시간대·자동 롤백·`--unpin`·하루 잠금 권고 D28).
- 플래그 `ONBOARDING_SOURCE_FETCH_ALERTS_ENABLED`·`PROFILE_GATE_OPTIONAL_ENRICHMENT`는 기본 꺼짐(대표 결정 전) — 운영 env에 넣지 않았다.
- **이후 Worker를 `00223` 이하로 롤백하지 않는다**(Phase 0).

## 배포 일부러 하지 않은 것 (결정)

- **Terraform 경보 정책 2종(`watchdog_scheduler_failures`·`slack_path_failures`) — 적용하지 않는다.** 대표 결정 D29: 경보는 Slack 하나로 받는다 — `OPS_ALERT_EMAIL`은 의도적으로 비우고 Terraform 경보 2종은 적용하지 않는다(2026-10-13 ~18:00). 결정 전 기록: 작업 PC의 terraform 1.5.7로는 `plan`을 돌릴 수 없어(배포 안내 Phase 2의 2번은 `plan`이 추가 2건만 보이는지 확인한 뒤 적용) 적용하지 않았다. terraform을 실행하지 않았다. 감시 토큰 불일치·Slack 경로 실패를 알리는 GCP 이메일 경보는 아직 없다 — 후속으로 terraform 버전을 맞춘 뒤 `-target` 두 개만 적용한다.
- **`OPS_ALERT_EMAIL`은 비워 둔다** — 대표 결정 D29: 경보는 Slack 하나로 받는다 — `OPS_ALERT_EMAIL`은 의도적으로 비우고 Terraform 경보 2종은 적용하지 않는다. readiness는 비어 있어도 통과한다(`facts.ops_alert_email_configured=false`). 새 키는 코드 기본값을 쓰므로 운영 env와 `.env.production`의 차이는 0이었고(14:30 대조) `--accept-env-diff`가 필요 없었다.
- **`.gitleaksignore` 예외 1줄**(`5690cb70`): `scripts/test_deploy_runtime.py`의 `--unpin` 테스트가 단언하는 Cloud Run 리비전 이름(공개 메타데이터)이 `generic-api-key`로 잡힌 오탐을 정확한 commit/path/line(`6375b228…:scripts/test_deploy_runtime.py:generic-api-key:2251`)으로만 예외 처리했다. 오케스트레이터 검토·승인.

## 배포 절차와 증거

1. 사전 읽기 전용 조회(14:30 KST, P1 이미지 migrate Job `reputation-migrate-njbgx`·`reputation-migrate-llpvx`): alembic `0088` · 첫 일정 자동 생성 후보 **0**(ESSENCE_NOT_CURRENT 2곳 — Essence 승인 전) · DNS 활성화 후보 **0** · 예상 온보딩 정체 **1건**(세종다온신경과의원 `P` — 인수 3일 경과·프로필 미완료) · 온보딩/DNS 사고 0 · outbox 632행 · `set_schedule` by `system:essence-auto-approval` 0. 운영 env ↔ `.env.production` 차이 0(api·worker·beat).
2. 사전 상태: 5개 서비스 고정 트래픽 없음. `deploy.sh`가 "운영 서비스 env를 정본으로 씁니다(reputation-api 현재 env)", Slack 웹훅 생존 확인(`SLACK_WEBHOOK_URL_DEV`는 latest 비활성이라 주입 안 함), 하루 잠금 통과, legacy publish 사전 확인 0건(증거 `.omo/evidence/deploy-preflight/legacy-publish-retirement-d6b85c9634579b8f4d08cc63744e309d7f2c00b5-20261013T060332Z.json`, 배포 worktree).
3. `deploy.sh backend`(`DEPLOY_ROLLBACK_STATE_FILE=.omo/rollback-p2/backend`) 15:03:05~15:10:48, exit 0: migrate `reputation-migrate-vlljd`(15:05:51, 적용할 리비전 없음 — head `0088` 그대로) → worker `00233-9k7`(15:06:02) → RedBeat reconcile `reputation-redbeat-reconcile-ctfss`(15:06:38) `clean=true`, `stored_version=expected_version=2026-10-12.1`, 이름 34개, orphan·stale 0 → beat `00228-jlm`(15:06:49) → readiness `reputation-production-readiness-pbrx8`(15:10:04) `ready=true`, **검사 18/18**(`pipeline_watchdog_token_configured: true` 포함), 7개 큐 canary 현재 릴리스 `d6b85c96`, `schema_revision=0088_add_outbox_open_pairing_index`, `slack_webhook_valid={operator: alive, developer: not_configured}`, `facts.auto_publish_hold={active: false, all_hospitals: false, hospital_count: 0}`, `facts.ops_alert_email_configured=false`, `live_site_count=11` → api `00243-mx4`(15:10:16) → 배포 뒤 공개 표면 확인(smoke) 통과. 자동 롤백 없음.
4. `deploy.sh admin` — 첫 시도(15:11:03)는 셸에 `PUBLIC_DOMAIN`·`ADMIN_DOMAIN`이 없어 사전 검증에서 멈췄다(빌드·롤백 좌표 기록 전, 변경 없음). 두 변수를 export한 재시도 15:11:41~15:12:44 exit 0, `.omo/rollback-p2/admin`에 좌표 기록 → admin `00107-d26`.
5. 트래픽: 배포 뒤 api·worker·beat·admin 모두 `traffic[0]`=latestReady·100%·`latestRevision=True`, site 그대로. `/api/v1/health/live` `release=d6b85c9634579b8f4d08cc63744e309d7f2c00b5`. 공개 루트·Admin 로그인 200.
6. 병원 헬스 11곳(`/.well-known/reputation-health`): **11/11 200**, slug·canonical_host 일치, hospital_id 있음, release=`reputation-site-cr057b9d1e`. (smoke는 hospital ID를 비교하지 않는다 — 이 항목은 따로 확인한 것이다.)
7. 배포 뒤 `ops_sql_report --only=q1,--only=q2,--only=q7,--only=q8`(새 이미지 migrate `reputation-migrate-xqglf`, 15:13): q1 `retrying_no_sla` 전 종류 **0**, q1b 비어 있음, OPEN/RETRYING은 사전과 같다(사후검수 74·콘텐츠 28·원장 PDF 4·Essence 3 RETRYING+1 OPEN·월간 측정 2·백그라운드 2·리드 1+1) · q2 HOLD/FAILED는 10/8 이전 옛 `SLACK_DEV` 행뿐 · q7 `outbox_rows` 632·`open_notice_rows` 401(사전과 같음) · **q8·q8b 행 없음**(안분된 첫 달 병원 없음 → 롤백해도 21:30 당월 조정의 초과 배정 대상이 없다. 첫 일정이 자동 생성되면 이 값이 바뀌므로 롤백 전에 다시 본다).
8. 새 리비전 로그(api·worker·beat·admin, 범위 06:04Z 이후): +10분(15:17 조회) ERROR **0건**, WARNING 1건(API 404 `/api/v1/public/site/hospital` — 공개 조회 4xx). +30분(15:37 조회) ERROR **1건** — 15:22:50 worker `auto_review_essence_snapshot` `ValueError('essence LLM incomplete structured output: length')`(아래 10번, 지난 10일 동안 처음 나온 오류), WARNING은 공개 조회·Admin favicon 404(4xx)뿐. Beat `beat_liveness_failed` 0건. `expired authenticated dispatch envelope`는 따로 보지 않았다.
9. 첫 15분 틱(15:15, 새 워커): `project_milestone_events` `onboarding_stalls_opened=1`·`onboarding_stalls_recovered=0`, `monitor_live_custom_domains` `checked=7`·`new_failures=0`·`pending_domains_verified/not_ready/skipped/expired=0/0/0/0`. 두 번째 틱(15:30): `onboarding_stalls_opened=0`(같은 사고 유지), DNS `pending_domains_*` 0/0/0/0 — 대기 자기 도메인이 없다.
10. 배포 뒤 사후 SQL(T0=06:03:05Z, 새 이미지 migrate `reputation-migrate-fnpj8`, 15:31): alembic `0088` · `set_schedule` by `system:essence-auto-approval` **0**(누적 0) · 새 일정 0 · 새 서비스 구간(활성화) 0 · 병원 ACTIVE 11·ONBOARDING 1 · 기존 사고의 상태 변화 0 · 새 사고 **2건**: (a) `ONBOARDING_PROFILE_INCOMPLETE` OPEN MEDIUM 세종다온신경과의원(15:15:01, 사전 예측과 일치), (b) **`ESSENCE_AUTO_REVIEW_FAILED` OPEN HIGH `nyupeulraenyeoseongyiweon`(15:22:49) — 예측 밖.** 15:15 `reconcile_essence_snapshots`가 자동 재검수 1건을 큐에 넣었고(`queued=1`), Essence 검수 LLM 응답이 `stop_reason=length`(구조화 출력 잘림)로 세 번(15:16·15:19·15:22, Celery 재시도 60s·120s) 실패해 사고가 열렸다. 이 배포는 Essence 토큰 상한을 바꾸지 않았다(`git diff 08abf747 d6b85c96`에 max_tokens 변경 없음) — 새 재검수 주기(1h/4h/12h)가 이 병원의 재검수를 앞당긴 것으로 보이며 원인(입력 길이·출력 상한)은 확인하지 않았다. · 배포 뒤 outbox **2행**(`INCIDENT_OPEN` SENT 2 = 위 두 사고, 누적 634).

## 롤백 좌표

api `reputation-api-00242-jsw` · worker `reputation-worker-00232-vx2` · beat `reputation-beat-00227-2mv` · admin `reputation-admin-00106-psm`. 롤백 파일 `.omo/rollback-p2/backend`(2026-10-13T06:04:04Z)·`.omo/rollback-p2/admin`(2026-10-13T06:11:48Z) — 배포 worktree `reputation-deploy`. P1·P0 좌표는 `.omo/rollback-p1/`·`.omo/rollback-p0/`(쓰지 않는다). DB는 바뀌지 않았다. RedBeat 버전이 같아 재조정은 필요 없다.
**롤백 주의(첫 달 안분, 배포 안내 Phase 2 8번)**: 마이그레이션이 없어도 트래픽 롤백만으로 안전하지 않다. 옛 Worker는 첫 달 약속을 요금제 편수로 계산하므로, Phase 2가 첫 일정을 안분해 만든 병원이 있는 채로 되돌리면 21:30 KST 당월 조정이 초과 배정한다. 롤백 전·후에 `ops_sql_report --only=q8`을 돌리고, 21:30 조정이 돌았으면 그날 23:00 전에 `q8b`의 미생성 슬롯을 기존 취소 API로 CANCELLED 처리한다(수동). 배포 직후 q8은 행이 없다. Worker `00223` 이하 금지.

## 이미지

| 서비스 | 리비전 | 이미지 digest (Cloud Run 해석값) |
|---|---|---|
| reputation-api / worker / beat / migrate·readiness job | `00243-mx4` / `00233-9k7` / `00228-jlm` | `reputation@sha256:cff7eddff7663a67d840adad0e54b8810b4df502ff146c731693d46b2f76e1e7`(태그 `reputation:20261013-150305`, push 출력 `sha256:b8ca4e4c…e628`) |
| reputation-admin | `00107-d26` | `admin@sha256:4e83aa3262862e37516b050a425e1e336d0543109029f195ba568a872e85b9f6`(태그 `admin:20261013-151141`, push 출력 `sha256:1485fcd3…ad95`) |

## 다음 날(10/14) 지표

| 지표 | 기대 | 결과 |
|---|---|---|
| 첫 일정 자동 생성(`set_schedule` by `system:essence-auto-approval`) | Essence가 자동 승인된 병원만, 첫 달 안분 | 배포 직후 0(후보 0) · 10/14 미측정 |
| 온보딩 정체 사고 | 세종다온신경과의원 1건 유지, 새 건은 실제 정체만 | 배포 직후 1건(예측과 일치) · 10/14 미측정 |
| `nyupeulraenyeoseongyiweon` `ESSENCE_AUTO_REVIEW_FAILED`(length 잘림) | 원인 확인 — 재발하면 Essence 출력 상한·입력 크기 조사 | 미조사 |
| 대기 자기 도메인 DNS 확인 | 대기 도메인 없음 → 0/0/0/0 | 미측정 |
| Beat `/live`(`beat_liveness_failed`) | 0 | 미측정 |
| 새 리비전 ERROR | 0 | 미측정(10/14) |

## 남은 것

- ~~Terraform 경보 2종 적용, `OPS_ALERT_EMAIL` 값 결정~~ — 대표 결정 D29: 경보는 Slack 하나로 받는다 — `OPS_ALERT_EMAIL`은 의도적으로 비우고 Terraform 경보 2종은 적용하지 않는다(후속 없음).
- Admin 새 화면(병원 정보의 자동 입력 초안)은 로그인이 필요해 사람이 1회 확인한다.
- 세종다온신경과의원 프로필 완료(사람의 일 — 온보딩 정체 사고가 부른다).
- `nyupeulraenyeoseongyiweon` Essence 자동 재검수 실패(`stop_reason=length`) 원인 조사 — 배포 뒤 처음 나온 오류이고 운영 채널에 '조치 필요' 1건이 나갔다.
