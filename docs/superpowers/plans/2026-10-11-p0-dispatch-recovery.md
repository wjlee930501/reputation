# Phase 0 — 배달·재배달·lease 정리 (감사 계획 §4 0-7·0-8·0-9)

작성 2026-10-11 · 기준 main `8c8f6e60` · 읽기 전용 조사 결과를 바탕으로 한 실행 계획.

## 요약 — 감사 가정과 다른 점

| 항목 | 감사 가정 | 조사 결과 |
|---|---|---|
| 0-7 | TTL 6h 상향, 위험 낮음 | 선행조건 충족(아래 증거). 단 **만료 Beat 버리기 규칙의 효과가 바뀐다** — 지금은 1시간 넘은 매분 작업 사본을 버리지만, 6시간이 되면 6시간 안의 사본은 **실행된다**(10/8 같은 4시간 정지 뒤 ~1,700건 재생). 정확성 문제는 아니고 회복 직후 수 분의 잡음이다. |
| 0-8 | 유형별 예산 3회 + V0·보고서 유예 45분, cold shutdown이 근거 | cold shutdown은 **배포 유실**만 줄인다. QUEUED가 오래 머무는 주원인은 **큐 적체**다 — 워커가 `-Q control,default,content,sov,reports,…`를 `queue_order_strategy: priority`(앞 큐 우선)로 읽어 `reports` 큐는 content·sov 적체(관측 최악 ~70분) 뒤에서 기다린다. 45분 고정 유예 + 예산 3회면 평범한 야간 적체만으로 V0가 FAILED가 된다. → **재배달마다 유예를 두 배로**(45m→90m→3h→6h) 늘리는 지수 백오프를 함께 넣는다. 또한 `attempt_count`는 claim 횟수(V0 정상 이어가기 포함)라 예산으로 쓸 수 없다 → 새 컬럼 `redispatch_count`(마이그레이션 0087)가 필요하다. |
| 0-9 | 정책 표 전 유형으로 lease 인수 확대 | **두 유형은 인수하면 안 된다.** `GENERATE_MONTHLY_REPORT`의 rebuild=True는 다시 돌면 새 버전을 하나 더 만든다(`plan_report_version`이 correlation key로 중복을 막지 않음) → 인수 대신 FAILED+사고. `RECERTIFY_PUBLISHED_IMAGE`는 자기 sweep이 30분(`IN_FLIGHT_TIMEOUT`) 뒤 좌초 실행을 새 실행으로 대체하므로 인수하면 주인이 둘 → 제외. "7일 넘은 RUNNING" 기준을 `started_at`으로 잡으면 24~31일 내내 사는 `MONTHLY_SOV_PERIOD`(8일)를 진행 중에 죽인다 → **`updated_at` 7일 무변화 + lease 없음/만료**로 판정. `SITE_REVALIDATION`은 자체 재시도 체인이 소유하므로 제외. |

과제 수: 7개(0-7, 0-8a, 0-8b, 0-8c, 0-9a, 0-9b, 문서). **마이그레이션 번호 0087을 쓴다 — 같은 Phase 0의 다른 초안이 0087을 쓰면 하나를 0088로 바꿔 체인을 잇는다.**

테스트 실행 전제: 순수 단위 테스트는 변수 없이 파일 단위로 돈다. `*_postgres.py`는 `INTEGRATION_DATABASE_URL` 등 24개 변수와 `alembic upgrade head`(0087 포함)가 필요하다 — 절차는 `scratchpad/masterplan/testenv-and-queries.md` Part A.

---

### Task 0-7: 봉투 서명 수명을 6시간으로 올린다

**Files:**
- Modify: `backend/app/workers/dispatch_envelope.py:14-22`
- Modify: `backend/app/core/celery_app.py:188-194` (주석만)
- Test: `backend/tests/test_dispatch_deploy_safe.py:1-8, 281-293, 296-300, 329-354`

**조사 결과:**
- `dispatch_envelope.py:21-22` — `DISPATCH_MAX_LIFETIME_SECONDS = 6 * 60 * 60`, `DISPATCH_TTL_SECONDS = 60 * 60`. 서명은 `dispatch_envelope.py:149` `EXPIRES_HEADER: str(issued_at + DISPATCH_TTL_SECONDS)`에서 모듈 전역을 그때 읽는다(monkeypatch 가능).
- 검증은 `dispatch_auth.py:101-106` `0 < lifetime <= DISPATCH_MAX_LIFETIME_SECONDS` — 이미 6시간 봉투를 받는다.
- `celery_app.py:197` `visibility_timeout: 7200`. 지금은 서명 1h < visibility 2h라 SIGKILL로 2시간 뒤 되돌아온 사본은 만료로 거절된다(주석 `celery_app.py:188-194`가 이 사실을 인정).
- **선행조건 "#226 이전 워커 리비전이 모두 사라진 다음 릴리스"(`docs/releases/2026-10-08-root-cause-fixes-production.md:30, 69`) 충족 증거:** #226은 main `c5ada5a1`로 worker `00225-cmn`에 10/8 배포(CLAUDE.md "최신 운영 배포 — 2026-10-08"). 이후 worker `00229-rm6`(10/9), `00230-s6m`(10/10)이 배포됐고 10/10 기록(`docs/releases/2026-10-10-alert-noise-production.md:5-6, 32-36`)은 "Site·Admin·Worker·Beat는 최신을 따라가고 있었다"고 적는다. #226 이전 워커는 `≤00224`이며 트래픽이 없다. 롤백 지점(`scripts/deploy.sh:1501, 1521` `capture_rollback_point`)도 직전 리비전(00230 이후)이라 #226 이후다. 배포 직전에 아래 Step 0으로 실측 재확인한다.
- `dispatch_auth.py:318` 만료 Beat 버리기 규칙은 `0 < period <= DISPATCH_TTL_SECONDS`를 쓴다. `_schedule_period_seconds`(`dispatch_auth.py:253-279`)는 시·일·월이 모두 '매번'인 crontab만 주기를 주므로 최대 3600초다 → **버리는 대상 집합은 그대로**. 바뀌는 것은 "만료" 시점이다: 1시간 넘은 매분 작업 사본은 이제 6시간까지 **실행**된다(위 요약 표).
- **TTL=3600을 직접 단정하는 테스트:** `tests/test_dispatch_deploy_safe.py:329-337` `test_this_release_signs_the_lifetime_pre_226_workers_accept`(`== 3600`) 하나. 3600을 하드코딩한 보조: `test_dispatch_deploy_safe.py:292`(`_stamp_with_lifetime`가 3600으로 되돌림), `:296-300`(파라미터 `[3600, MAX]` — 계속 유효), `:7`(docstring). 상대값만 쓰는 테스트(변경 불필요): `test_dispatch_deploy_safe.py:122, 350`, `test_worker_dispatch_auth.py:177, 192, 365`, `test_worker_broker_resilience.py:260, 448`.

- [ ] **Step 0: 운영 선행조건 실측(읽기 전용)**
  Run:
  ```bash
  gcloud run services describe reputation-worker --project mso-platform-481505 --region asia-northeast3 \
    --format='yaml(spec.traffic,status.traffic,status.latestReadyRevisionName)'
  gcloud run revisions list --service reputation-worker --project mso-platform-481505 --region asia-northeast3 \
    --limit 6 --format='table(metadata.name,status.conditions[0].status,metadata.creationTimestamp)'
  ```
  Expected: `spec.traffic`가 `latestRevision: true, percent: 100`, `status.traffic`의 리비전이 `reputation-worker-00230-s6m` 이상(00225 이상이면 #226 포함). `00224` 이하에 트래픽 0. 다르면 이 과제를 멈추고 보고한다.

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/test_dispatch_deploy_safe.py`

  1-a. `:329-337`의 `test_this_release_signs_the_lifetime_pre_226_workers_accept`를 아래로 교체:
  ```python
  def test_the_publisher_signs_the_full_accepted_lifetime() -> None:
      """서명 수명은 수락 상한과 같다(2026-10-11). #226 이전 워커(≤ worker-00224)는 모두 사라졌다."""

      headers = dispatch_auth.stamp_dispatch_headers(
          task_name=IMAGE_TASK, task_id="t", args=["x"], kwargs={}, retries=0, headers={}, now=ISSUED
      )
      issued = int(headers[dispatch_envelope.ISSUED_HEADER])
      assert (
          int(headers[dispatch_envelope.EXPIRES_HEADER]) - issued
          == dispatch_envelope.DISPATCH_MAX_LIFETIME_SECONDS
      )


  def test_signed_lifetime_covers_broker_redelivery_within_the_accepted_bound() -> None:
      """SIGKILL로 visibility_timeout 뒤 되돌아온 사본도 아직 유효해야 한다."""

      visibility = celery_app.conf.broker_transport_options["visibility_timeout"]
      assert (
          visibility
          <= dispatch_envelope.DISPATCH_TTL_SECONDS
          <= dispatch_envelope.DISPATCH_MAX_LIFETIME_SECONDS
      )


  def test_a_copy_redelivered_after_the_visibility_timeout_still_validates(monkeypatch) -> None:
      visibility = celery_app.conf.broker_transport_options["visibility_timeout"]
      _production(monkeypatch, now=ISSUED + visibility + 60)
      target = str(uuid.uuid4())
      headers = dispatch_auth.stamp_dispatch_headers(
          task_name=IMAGE_TASK, task_id="t", args=[target], kwargs={}, retries=0, headers={},
          now=ISSUED,
      )

      dispatch_auth.validate_task_dispatch(
          task_name=IMAGE_TASK, task_id="t", args=[target], kwargs={}, retries=0, headers=headers
      )
  ```
  1-b. `:281-293` `_stamp_with_lifetime`의 되돌림을 원래 값으로:
  ```python
  def _stamp_with_lifetime(monkeypatch, lifetime: int, target: str) -> dict[str, str]:
      original = dispatch_envelope.DISPATCH_TTL_SECONDS
      monkeypatch.setattr(dispatch_envelope, "DISPATCH_TTL_SECONDS", lifetime)
      headers = dispatch_auth.stamp_dispatch_headers(
          task_name=IMAGE_TASK,
          task_id="t",
          args=[target],
          kwargs={},
          retries=0,
          headers={},
          now=ISSUED,
      )
      monkeypatch.setattr(dispatch_envelope, "DISPATCH_TTL_SECONDS", original)
      return headers
  ```
  1-c. `:298` 주석을 `# 3600초: 직전 릴리스(1시간 서명)가 보낸 봉투 — 롤아웃 겹침 중에도 유효해야 한다. 상한: 이번 릴리스가 서명하는 수명.`으로, `:7` docstring을 `- 봉투 수명은 '정확히 같음'이 아니라 상한이다 — 직전 릴리스(3600초) 봉투도 롤아웃 중 유효하고, 서명은 상한(6시간)과 같다.`로 바꾼다.

- [ ] **Step 2: 실패 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_dispatch_deploy_safe.py -q -k "full_accepted_lifetime or covers_broker_redelivery or redelivered_after_the_visibility"`
  Expected: 3 failed — `assert 3600 == 21600`, `assert 7200 <= 3600`, `DispatchAuthorizationError: expired authenticated dispatch envelope`.

- [ ] **Step 3: 최소 구현** — `backend/app/workers/dispatch_envelope.py:14-22`를 교체:
  ```python
  # 봉투 수명.
  # - DISPATCH_MAX_LIFETIME_SECONDS: 검증이 받아 주는 상한(`0 < 수명 <= 상한`).
  # - DISPATCH_TTL_SECONDS: 발행자가 서명하는 수명. 상한과 같다(2026-10-11). 브로커가 되돌린
  #   메시지(visibility_timeout 7200초)나 배포·적체로 늦게 소비되는 메시지가 유효하도록 6시간이다.
  #   #226 이전 워커(정확히 3600초만 받던 리비전, worker ≤ 00224)는 10/8 이후 모두 사라졌다.
  #   워커를 그 리비전으로 롤백하지 않는다.
  DISPATCH_MAX_LIFETIME_SECONDS = 6 * 60 * 60
  DISPATCH_TTL_SECONDS = DISPATCH_MAX_LIFETIME_SECONDS
  ```
  `backend/app/core/celery_app.py:188-194` 주석의 마지막 세 문장("이번 릴리스는 아직 3600초로 서명하므로 … 다음 릴리스에서 사라진다.")을 다음으로 바꾼다:
  ```python
      # lease(`operation_run_signals`)는 이 값 이하다. 서명 수명(`DISPATCH_TTL_SECONDS`, 6시간)은
      # 이 값 이상이라, 정상 종료 없이 끊겨 7200초 뒤 되돌아온 메시지도 유효하게 다시 claim된다.
  ```

- [ ] **Step 4: 통과 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_dispatch_deploy_safe.py tests/test_worker_dispatch_auth.py tests/test_worker_broker_resilience.py -q`
  Expected: 모두 passed(실패 0). `test_only_beat_entries_within_the_envelope_lifetime_are_droppable`도 통과(버리는 집합 불변).

- [ ] **Step 5: 커밋**
  `git add backend/app/workers/dispatch_envelope.py backend/app/core/celery_app.py backend/tests/test_dispatch_deploy_safe.py && git commit -m "fix(dispatch): 봉투 서명 수명을 수락 상한 6시간으로 올린다"`

**계약 영향:** CLAUDE.md "자율 작업·알림·비용" 243행 `서명 수명 DISPATCH_TTL_SECONDS(이번 릴리스는 1시간 그대로; …6시간으로 올린다)` → `서명 수명 DISPATCH_TTL_SECONDS(6시간, 수락 상한과 같고 broker visibility_timeout 2시간 이상)`. 242행 `주기 ≤ 봉투 수명(1시간)의 Beat 작업` → `(6시간)`. `docs/ops/deployment-runbook.md:203-205`에 "10/11 릴리스에서 서명을 6시간으로 올렸다. 워커를 00224 이하로 롤백하지 않는다." 한 문장 추가(문서 과제에서 일괄).
**위험·롤백:** (1) 워커 정지 뒤 1~6시간 묵은 매분 Beat 사본(drain·canary·reconcile 등)이 버려지지 않고 실행된다 — 모두 멱등·저비용이고 `control` 큐 우선이라 회복 직후 수 분 지연. 필요하면 후속으로 매분 Beat 항목에 `options.expires`를 둔다(이번 범위 밖). (2) 롤백: 상수를 `60 * 60`으로 되돌리는 코드 롤백만으로 충분(검증 상한이 그대로라 이미 서명된 6시간 봉투도 계속 유효). 워커만 #226 이전으로 내리는 롤백은 금지.

---

### Task 0-8a: `operation_runs.redispatch_count` 컬럼(0087)

**Files:**
- Create: `backend/alembic/versions/0087_add_operation_run_redispatch_count.py`
- Modify: `backend/app/models/operations.py:157-160` (컬럼 추가)
- Modify: `backend/tests/test_migration_chain_linearity.py:56-59, 113, 136, 172, 183` · `scripts/integrity_rehearsal/check.py:182`
- Test: `backend/tests/test_migration_chain_linearity.py`

**조사 결과:**
- `models/operations.py:65-166` `OperationRun`에는 재배달 횟수를 담을 곳이 없다. `attempt_count`(`:137`)는 `operation_run_signals._claim`(`:151` `attempt_count + 1`)이 **claim마다** 올린다 — QUEUED 유실 재배달은 claim이 일어나지 않아 0 그대로이고, 반대로 V0의 정상 이어가기(`tasks.py:4983-4990` `self.retry(countdown=120, max_retries=V0_CONTINUATION_MAX_RETRIES)`)는 claim마다 올라 "정상 이어가기와 실패 예산을 섞지 않는다"(CLAUDE.md)는 계약 때문에 예산으로 쓸 수 없다.
- JSON 대안 검토: `request_payload`는 요청 사실이고 여러 조회가 JSON 경로로 읽는다(`operations_center_report_queries.py:211-214`, `leads.py:587`, `content.py:2498`). `result_summary`는 워커가 덮어쓴다(`generation_batch_run.py` `_persist`, `_stored_period`가 읽음). 재배달 횟수를 거기 섞으면 의미가 흐려진다 → **새 정수 컬럼**이 맞다.
- 마이그레이션 형식은 0083(`alembic/versions/0083_add_operation_run_not_before.py`)과 같게 한다(추가형·재실행 안전). head 고정 위치는 `tests/test_migration_chain_linearity.py:56-59`(상수·HEAD), `:113-136`(복구 목록), `:141-172`(parents), `:179-183`(pending, head부터 역순)와 `scripts/integrity_rehearsal/check.py:182`.

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/test_migration_chain_linearity.py`
  - `:56` 아래에 `OPERATION_RUN_REDISPATCH_COUNT = "0087_add_operation_run_redispatch_count"` 추가, `:59` `HEAD = OPERATION_RUN_REDISPATCH_COUNT`.
  - `:136`(복구 목록 튜플 끝 `HISTORICAL_PUBLICATIONS,`) 뒤에 `OPERATION_RUN_REDISPATCH_COUNT,` 추가.
  - `:172` parents dict 끝에 `OPERATION_RUN_REDISPATCH_COUNT: HISTORICAL_PUBLICATIONS,` 추가.
  - `:182` pending 리스트 맨 앞(`HISTORICAL_PUBLICATIONS,` 앞)에 `OPERATION_RUN_REDISPATCH_COUNT,` 추가.
  - 같은 파일에서 `HISTORICAL_PUBLICATIONS`가 체인 끝으로 쓰인 다른 목록이 있으면 같은 자리에 넣는다: `grep -n "HISTORICAL_PUBLICATIONS" backend/tests/test_migration_chain_linearity.py`로 모두 확인.
  - `scripts/integrity_rehearsal/check.py:182` `assert head == "0086_preserve_historical_publications", head` → `"0087_add_operation_run_redispatch_count"`.

- [ ] **Step 2: 실패 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_migration_chain_linearity.py -q`
  Expected: FAIL — `test_history_has_exactly_one_head`에서 `['0086_preserve_historical_publications'] == ['0087_add_operation_run_redispatch_count']` 불일치.

- [ ] **Step 3: 최소 구현**
  `backend/alembic/versions/0087_add_operation_run_redispatch_count.py`:
  ```python
  """Add operation_runs.redispatch_count — 자율 복구가 이 실행을 다시 보낸 횟수.

  Revision ID: 0087_add_operation_run_redispatch_count
  Revises: 0086_preserve_historical_publications

  QUEUED 유실(브로커에 있던 사본이 소비되지 않음)과 lease 만료 RUNNING 인수로 자율 복구가 새 task id로
  다시 보낸 횟수다. `attempt_count`(claim 횟수, V0 정상 이어가기 포함)와 다르다. 유형별 예산(3회)을 다
  쓰면 실행을 FAILED로 끝내고 원인별 사고 하나에 묶는다 — poison 메시지가 3시간마다 영원히 재배달되던
  것을 막는다. publish 실패로 남은 REQUESTED 재시도와 미룬 실행(`not_before_at`)의 정시 재배달은 세지
  않는다.

  Additive only. 기존 행은 0이다(서버 기본값). 재실행해도 같은 결과가 되도록 컬럼 존재를 먼저 확인한다.

  배포 순서: **이 마이그레이션을 먼저 적용하고, 코드는 그 다음 배포한다.** 새 ORM은 OperationRun을
  읽을 때마다 이 컬럼을 SELECT한다. 0087 위의 옛 코드는 안전하다(NOT NULL이지만 서버 기본값이 있어 옛
  INSERT도 통과한다).

  Downgrade는 컬럼만 지운다. 옛 코드에는 예산이 없어 종전처럼 무한 재배달로 돌아간다.
  """

  from collections.abc import Sequence

  import sqlalchemy as sa

  from alembic import op

  revision: str = "0087_add_operation_run_redispatch_count"
  down_revision: str | None = "0086_preserve_historical_publications"
  branch_labels: str | Sequence[str] | None = None
  depends_on: str | Sequence[str] | None = None

  _TABLE = "operation_runs"
  _COLUMN = "redispatch_count"


  def _has_column(table: str, column: str) -> bool:
      return any(item["name"] == column for item in sa.inspect(op.get_bind()).get_columns(table))


  def upgrade() -> None:
      if _has_column(_TABLE, _COLUMN):
          return
      op.add_column(
          _TABLE,
          sa.Column(_COLUMN, sa.Integer(), nullable=False, server_default="0"),
      )


  def downgrade() -> None:
      if _has_column(_TABLE, _COLUMN):
          op.drop_column(_TABLE, _COLUMN)
  ```
  `backend/app/models/operations.py` — `:159` `not_before_at` 바로 뒤에 추가:
  ```python
      # 자율 복구가 QUEUED 유실·lease 만료로 이 실행을 새 task id로 다시 보낸 횟수(0087).
      # claim 횟수(`attempt_count`)와 다르다 — 유형별 재배달 예산은 이 값으로 센다.
      redispatch_count: Mapped[int] = mapped_column(
          Integer, default=0, server_default="0", nullable=False
      )
  ```

- [ ] **Step 4: 통과 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_migration_chain_linearity.py -q` → Expected: passed.
  DB가 있으면: `cd backend && DATABASE_URL=… SYNC_DATABASE_URL=… .venv/bin/alembic upgrade head` (testenv 문서의 3개 DB) → Expected: `Running upgrade 0086_preserve_historical_publications -> 0087_add_operation_run_redispatch_count`. 이어서 `.venv/bin/alembic downgrade -1 && .venv/bin/alembic upgrade head`가 오류 없이 끝난다.

- [ ] **Step 5: 커밋**
  `git add backend/alembic/versions/0087_add_operation_run_redispatch_count.py backend/app/models/operations.py backend/tests/test_migration_chain_linearity.py scripts/integrity_rehearsal/check.py && git commit -m "feat(operations): 자율 복구 재배달 횟수 컬럼(0087)"`

**계약 영향:** CLAUDE.md 상단 배포 절의 DB head 표기는 배포 기록 작성 때 갱신(코드 커밋에서 바꾸지 않는다).
**위험·롤백:** 추가형·상수 기본값이라 PG11+에서 메타데이터만 바뀐다(테이블 재작성 없음). 배포는 `deploy.sh` Backend 4단계가 migrate를 먼저 돌리므로 표준 경로면 순서가 지켜진다. 롤백은 코드 먼저, 그다음 `alembic downgrade 0086_preserve_historical_publications`.

---

### Task 0-8b: QUEUED 유실 유예 — V0·월간 보고서 45분 + 재배달마다 두 배

**Files:**
- Modify: `backend/app/workers/autonomous_recovery.py:51-56`(상수), `:137-166`(`_operation_redispatch_is_due`), `:219-246`(후보 SQL)
- Test: `backend/tests/test_autonomous_redispatch_budget.py` (신규, 순수 단위)

**조사 결과:**
- `autonomous_recovery.py:55` `_QUEUED_REDISPATCH_GRACE = timedelta(hours=3)` 하나를 전 유형이 쓴다(`:150`, SQL `:227-232`). 주석 근거는 "관측된 최악의 content 적체 약 70분".
- 큐 소비 순서: `docker-entrypoint.sh:33` `-Q control,default,content,sov,reports,leadgen,certificates`, `celery_app.py:196` `queue_order_strategy: priority` → 앞 큐가 비어야 `reports`를 집는다. V0(`TRIGGER_V0_REPORT`, queue `reports`)와 `GENERATE_MONTHLY_REPORT`(queue `reports`)는 content·sov 적체 뒤에서 기다린다. **감사의 근거(cold shutdown, `docker-entrypoint.sh:30` `REMAP_SIGTERM=SIGQUIT`)는 배포 중 유실을 몇 초로 줄이는 것까지만 맞다** — 배포 유실이 사라졌으니 3시간을 기다릴 이유는 없지만, 적체는 여전히 45분을 넘길 수 있다.
- 적체 중 재배달은 안전하다: 재배달은 새 task id를 쓰고(`:735-739`), 늦게 소비된 이전 사본은 `operation_run_signals._claim`의 `task_id == worker_id` 조건(`operation_run_signals.py:122-141`)에서 claim을 못 해 `dispatch_auth.before_start`가 STALE `Ignore`로 끝낸다(`dispatch_auth.py:243-256`). 공급자 비용 없음.
- 따라서 예산(0-8c)과 함께 쓰려면 고정 45분으로는 야간 적체 한 번에 예산을 다 쓴다 → **유예 = 기본값 × 2^재배달 횟수**. V0: 45m → 90m → 3h → 6h(그 뒤 소진). 다른 유형: 3h → 6h → 12h → 24h.
- 미룬 실행(`not_before_at`, `:144-147`)과 Celery retry 대기(`_requeue_from_signal`이 `queued_at=now`로 되돌림, `operation_run_signals.py:262-289`, countdown ≤ 15분 `MAX_DISPATCH_COUNTDOWN_SECONDS`)는 45분보다 짧거나 별도 규칙이라 영향 없다.

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/test_autonomous_redispatch_budget.py` 신규
  ```python
  """자율 복구의 재배달 유예·예산·lease 인수 판정(2026-10-11, 감사 0-8·0-9)."""

  from __future__ import annotations

  import uuid
  from datetime import UTC, datetime, timedelta
  from types import SimpleNamespace

  import pytest

  from app.models.operations import OperationRunState
  from app.workers import autonomous_recovery

  NOW = datetime(2026, 10, 11, 3, 0, tzinfo=UTC)


  def _queued(operation_type: str, minutes: int, *, redispatch_count: int = 0, not_before=None):
      return SimpleNamespace(
          state=OperationRunState.QUEUED,
          operation_type=operation_type,
          queued_at=NOW - timedelta(minutes=minutes),
          requested_at=NOW - timedelta(minutes=minutes),
          not_before_at=not_before,
          redispatch_count=redispatch_count,
      )


  @pytest.mark.parametrize("operation_type", ["TRIGGER_V0_REPORT", "GENERATE_MONTHLY_REPORT"])
  def test_customer_reports_are_called_lost_after_45_minutes(operation_type) -> None:
      assert not autonomous_recovery._operation_redispatch_is_due(_queued(operation_type, 44), NOW)
      assert autonomous_recovery._operation_redispatch_is_due(_queued(operation_type, 46), NOW)


  def test_other_work_keeps_the_three_hour_grace() -> None:
      assert not autonomous_recovery._operation_redispatch_is_due(_queued("RUN_SOV", 179), NOW)
      assert autonomous_recovery._operation_redispatch_is_due(_queued("RUN_SOV", 181), NOW)


  @pytest.mark.parametrize(
      ("count", "not_yet", "due"),
      [(1, 89, 91), (2, 179, 181), (3, 359, 361)],
  )
  def test_each_redispatch_doubles_the_wait(count, not_yet, due) -> None:
      """야간 content 적체(관측 최악 ~70분) 한 번에 예산을 다 쓰지 않는다."""

      v0 = "TRIGGER_V0_REPORT"
      assert not autonomous_recovery._operation_redispatch_is_due(
          _queued(v0, not_yet, redispatch_count=count), NOW
      )
      assert autonomous_recovery._operation_redispatch_is_due(
          _queued(v0, due, redispatch_count=count), NOW
      )


  def test_the_grace_stops_growing_at_the_budget() -> None:
      run = _queued("TRIGGER_V0_REPORT", 0, redispatch_count=9)
      assert autonomous_recovery._queued_redispatch_grace(run) == timedelta(minutes=45 * 8)


  def test_a_deferred_run_still_follows_its_own_time() -> None:
      run = _queued("TRIGGER_V0_REPORT", 600, not_before=NOW + timedelta(minutes=1))
      assert not autonomous_recovery._operation_redispatch_is_due(run, NOW)
  ```

- [ ] **Step 2: 실패 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_autonomous_redispatch_budget.py -q`
  Expected: FAIL — 45분 테스트 2건 `assert False`(3시간 유예), 백오프 테스트 `count=1, due=91` 등 일부 실패, `AttributeError: module 'app.workers.autonomous_recovery' has no attribute '_queued_redispatch_grace'`.

- [ ] **Step 3: 최소 구현** — `backend/app/workers/autonomous_recovery.py`
  3-a. `:51-56` 상수 블록을 교체:
  ```python
  # REQUESTED는 '아직 브로커에 넣지 못했다'만 뜻한다 — 모든 배포 지점이 publish 직후 QUEUED로
  # 바꾸므로 짧게 둔다. QUEUED는 '브로커에 있다'라서 적체를 유실로 오인하지 않도록 길게 둔다.
  # 관측된 최악의 content 적체는 약 70분이었고, 2분 만에 다시 보내던 때는 적체마다 사본이 생겼다.
  _REQUESTED_REDISPATCH_GRACE: Final = timedelta(minutes=2)
  _QUEUED_REDISPATCH_GRACE: Final = timedelta(hours=3)
  # 고객이 기다리는 결과(V0·월간 보고서)는 45분만 기다린다. 배포 중 유실은 cold shutdown
  # (REMAP_SIGTERM=SIGQUIT)이 몇 초 안에 메시지를 반납해 사라졌고, 남은 대기는 앞 큐(content·sov)의
  # 적체다 — reports 큐는 priority 순서상 뒤에 있다. 적체 중 다시 보내도 늦게 온 이전 사본은
  # task id가 달라 STALE로 물러나므로 공급자 비용이 없다.
  _FAST_QUEUED_REDISPATCH_GRACE: Final = timedelta(minutes=45)
  _FAST_QUEUED_REDISPATCH_TYPES: Final = frozenset(
      {"TRIGGER_V0_REPORT", "GENERATE_MONTHLY_REPORT"}
  )
  # 유형별 재배달 예산. 재배달마다 QUEUED 유예가 두 배가 되므로(45m→90m→3h→6h) 정상 적체로는
  # 소진되지 않는다. 다 쓰면 실행을 FAILED로 끝내고 원인별 사고 하나에 묶는다.
  _REDISPATCH_BUDGET: Final = 3
  ```
  3-b. `_now()` 정의(`:117`) 바로 위에 헬퍼 추가:
  ```python
  def _redispatch_count(run: OperationRun) -> int:
      return int(getattr(run, "redispatch_count", 0) or 0)


  def _queued_redispatch_grace(run: OperationRun) -> timedelta:
      base = (
          _FAST_QUEUED_REDISPATCH_GRACE
          if str(run.operation_type) in _FAST_QUEUED_REDISPATCH_TYPES
          else _QUEUED_REDISPATCH_GRACE
      )
      return base * (2 ** min(_redispatch_count(run), _REDISPATCH_BUDGET))
  ```
  3-c. `_operation_redispatch_is_due`(`:149-150`)의
  ```python
          last_transition = run.queued_at or run.requested_at
          grace = _QUEUED_REDISPATCH_GRACE
  ```
  를
  ```python
          last_transition = run.queued_at or run.requested_at
          grace = _queued_redispatch_grace(run)
  ```
  로 바꾼다.
  3-d. 후보 SQL `:231`의 `<= observed_at - _QUEUED_REDISPATCH_GRACE,`를 `<= observed_at - _FAST_QUEUED_REDISPATCH_GRACE,`로 바꾼다(SQL은 가장 짧은 유예로 넓게 고르고, 유형·횟수별 판정은 `_operation_redispatch_is_due`가 한다).

- [ ] **Step 4: 통과 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_autonomous_redispatch_budget.py tests/test_dispatch_deploy_safe.py tests/test_autonomous_loop_recovery.py -q`
  Expected: passed. 기존 `test_a_queued_run_waits_hours_before_being_called_lost`(GENERATE_CONTENT_ITEM 179/181분)도 그대로 통과.

- [ ] **Step 5: 커밋**
  `git add backend/app/workers/autonomous_recovery.py backend/tests/test_autonomous_redispatch_budget.py && git commit -m "fix(recovery): V0·월간 보고서 QUEUED 유실 판정 45분, 재배달마다 유예 두 배"`

**계약 영향:** CLAUDE.md 243행 `QUEUED 유실 판정은 3시간이다` → `QUEUED 유실 판정은 기본 3시간, V0·월간 보고서는 45분이며 재배달마다 두 배가 된다`.
**위험·롤백:** 적체 중 V0가 한 번 더 보내져도 STALE로 끝나 비용은 없지만 로그 `dispatch_skipped`가 늘 수 있다. 기존 재배달은 `trigger_v0_report`의 kwargs(`failure_retry_count`)와 Celery `retries`를 싣지 않아(`_rebuild_dispatch`·`_validated_dispatch`는 args만) 재배달마다 V0 이어가기·실패 재시도 계수가 0으로 돌아간다 — 기존 동작이며 이제 예산 3회로 상한이 생긴다. 롤백은 코드 되돌림.

---

### Task 0-8c: 재배달 예산 — 소진 시 FAILED + 원인별 사고 하나

**Files:**
- Modify: `backend/app/workers/autonomous_recovery.py` — 임포트(`:11-14`), 루프(`:270-281`), `_redispatch_operation_run`(`:716-800`), 새 헬퍼(파일 끝 `_fail_unsafe_operation_run` 위)
- Modify: `backend/app/services/incident_types.py:55-69`, `backend/app/services/notification_copy.py:125-131`
- Test: `backend/tests/test_autonomous_redispatch_budget.py`(추가), `backend/tests/integration/test_autonomous_redispatch_budget_postgres.py`(신규), `backend/tests/test_notification_outbox.py:141-153`

**조사 결과:**
- 예산은 지금 `REBUILD_SITE`의 RUNNING claim 횟수(`_REBUILD_SITE_ATTEMPT_BUDGET=3`, `:57`, `:547-552`)뿐이다. QUEUED 재배달(`:716-800`)은 횟수를 남기지 않는다 → 소비되지 않는 메시지(예: 봉투 검증에서 거절되는 사본 — 검증 실패는 claim 전이라 실행이 QUEUED에 남는다, `dispatch_auth.py:196-233`)는 3시간마다 영원히 다시 간다.
- 세어야 할 것과 아닌 것: QUEUED(미루지 않음)·RUNNING(lease 만료 인수) → 셈. REQUESTED(publish 실패 — 브로커 장애 동안 2분마다 재시도, `test_autonomous_redispatch_postgres.py:124-178`) → 세지 않음(브로커 47분 장애가 예산을 태우면 안 된다). `not_before_at`이 지난 미룬 실행 → 계획된 재배달이라 세지 않음.
- 기존 사고 열기 패턴: `_fail_unsafe_operation_run`(`:1003-1086`)은 실행마다 `db.add(Incident)`(키 = 실행 id). "원인별 하나"는 `task_incident_control._open_incident`(`task_incident_control.py:316-383`)의 `insert … on_conflict_do_update(dedupe_key)` 재열기 패턴을 따른다(닫힌 사고면 `episode_seq+1`, 열린 사고면 횟수만). Slack은 새로 열렸거나 다시 열렸을 때만(`task_incident_control.py:105-118`).
- `terminal_outcome_identity`(`services/operation_terminal_outcomes.py:74-118`)는 재사용할 수 없다 — 정책 유형 전부가 `_CLASSIFIED_DOMAIN_OWNERS`에 있어 `safe_error_code != "TASK_FAILED"`면 None을 돌려준다.
- REBUILD_SITE는 이미 병원 단위 사고(`_open_rebuild_site_incident`, `:627-697`)가 있으므로 소진 시 그쪽으로 보낸다.
- 사고 종류는 새 `OPERATION_RECOVERY_EXHAUSTED`. 같은 성격의 `UNSAFE_STORED_DISPATCH`가 개발 대상(`incident_types.py:61`)이고 문구는 `BACKGROUND_TASK_FAILED` 별칭(`notification_copy.py:125-131`)이다 → 같은 자리에 등록한다(대상 결정은 아래 열린 질문).

- [ ] **Step 1: 실패하는 테스트 작성**
  1-a. `backend/tests/test_autonomous_redispatch_budget.py`에 추가:
  ```python
  def _running(operation_type: str, *, redispatch_count: int = 0, lease_delta=timedelta(seconds=-1)):
      return SimpleNamespace(
          state=OperationRunState.RUNNING,
          operation_type=operation_type,
          queued_at=NOW - timedelta(hours=2),
          requested_at=NOW - timedelta(hours=2),
          lease_expires_at=NOW + lease_delta,
          not_before_at=None,
          redispatch_count=redispatch_count,
          request_payload={},
      )


  def test_the_budget_counts_only_presumed_lost_copies() -> None:
      exhausted = autonomous_recovery._redispatch_budget_exhausted
      assert exhausted(_queued("RUN_SOV", 600, redispatch_count=3))
      assert exhausted(_running("RUN_SOV", redispatch_count=3))
      assert not exhausted(_queued("RUN_SOV", 600, redispatch_count=2))
      # 미룬 실행의 정시 재배달과 publish 실패(REQUESTED)는 예산을 쓰지 않는다.
      assert not exhausted(
          _queued("TRIGGER_V0_REPORT", 600, redispatch_count=3, not_before=NOW)
      )
      requested = _queued("RUN_SOV", 600, redispatch_count=9)
      requested.state = OperationRunState.REQUESTED
      assert not exhausted(requested)


  def _fake_redispatch_db(run):
      commits: list[bool] = []
      return SimpleNamespace(
          execute=lambda _statement: SimpleNamespace(scalar_one_or_none=lambda: run.id),
          commit=lambda: commits.append(True),
      )


  def _v0_run(state, *, redispatch_count=0, not_before=None):
      hospital_id = uuid.uuid4()
      return SimpleNamespace(
          id=uuid.uuid4(),
          operation_type="TRIGGER_V0_REPORT",
          state=state,
          hospital_id=hospital_id,
          task_id="original-copy",
          request_payload={
              "_dispatch": {
                  "target_type": "hospital",
                  "target_id": str(hospital_id),
                  "queue": "reports",
                  "task_args": [str(hospital_id)],
              }
          },
          requested_at=NOW - timedelta(hours=4),
          queued_at=NOW - timedelta(hours=4),
          not_before_at=not_before,
          completed_at=None,
          heartbeat_at=None,
          lease_owner=None,
          lease_expires_at=None,
          safe_error_code=None,
          safe_error_message=None,
          version=4,
          redispatch_count=redispatch_count,
      )


  @pytest.mark.parametrize(
      ("state", "not_before", "expected"),
      [
          (OperationRunState.QUEUED, None, 2),
          (OperationRunState.QUEUED, NOW - timedelta(minutes=1), 1),
          (OperationRunState.REQUESTED, None, 1),
      ],
  )
  def test_a_redispatch_records_whether_it_spent_the_budget(
      monkeypatch, state, not_before, expected
  ) -> None:
      run = _v0_run(state, redispatch_count=1, not_before=not_before)
      monkeypatch.setattr(
          autonomous_recovery.celery_app, "send_task", lambda *_a, **_k: None
      )

      assert autonomous_recovery._redispatch_operation_run(_fake_redispatch_db(run), run, NOW)

      assert run.redispatch_count == expected
  ```
  1-b. `backend/tests/integration/test_autonomous_redispatch_budget_postgres.py` 신규:
  ```python
  """재배달 예산·lease 인수·버려진 RUNNING 정리를 실제 PostgreSQL에서 확인한다(2026-10-11)."""

  from __future__ import annotations

  import uuid
  from datetime import UTC, datetime, timedelta

  import pytest
  from sqlalchemy import select
  from sqlalchemy.orm import Session

  from app.models.hospital import Hospital, HospitalStatus
  from app.models.operations import (
      Incident,
      NotificationOutbox,
      OperationRun,
      OperationRunState,
  )
  from app.services.incident_safety import build_incident_key
  from app.services.incident_types import IncidentFingerprint
  from app.services.operation_run_payloads import DispatchPayload, build_request_payload
  from app.workers import autonomous_recovery

  NOW = datetime(2026, 10, 11, 5, 0, tzinfo=UTC)


  def _session(pg_conn) -> Session:
      return Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")


  @pytest.fixture
  def db(pg_conn):
      session = _session(pg_conn)
      try:
          yield session
      finally:
          session.close()


  def _hospital(db) -> Hospital:
      hospital = Hospital(
          name="재배달 예산 가상의원",
          slug=f"redispatch-budget-{uuid.uuid4().hex[:10]}",
          status=HospitalStatus.ACTIVE,
      )
      db.add(hospital)
      db.flush()
      return hospital


  def _run(
      db,
      *,
      operation_type: str,
      state: OperationRunState,
      queue: str,
      args: tuple[object, ...] | None = None,
      minutes_ago: int = 0,
      redispatch_count: int = 0,
      lease_expires_at: datetime | None = None,
  ) -> OperationRun:
      hospital = _hospital(db)
      target = str(hospital.id)
      running = state == OperationRunState.RUNNING
      run = OperationRun(
          hospital_id=hospital.id,
          operation_type=operation_type,
          state=state,
          task_id=f"copy-{uuid.uuid4().hex[:8]}",
          version=2,
          attempt_count=1 if running else 0,
          redispatch_count=redispatch_count,
          request_payload=build_request_payload(
              DispatchPayload("hospital", target, queue, (target, *(args or ())))
          ),
          requested_at=NOW - timedelta(minutes=minutes_ago),
          queued_at=NOW - timedelta(minutes=minutes_ago),
          started_at=NOW - timedelta(minutes=minutes_ago) if running else None,
          lease_owner="dead-worker" if lease_expires_at is not None else None,
          lease_expires_at=lease_expires_at,
      )
      db.add(run)
      db.commit()
      return run


  def _reconcile(monkeypatch, pg_conn) -> list[dict[str, object]]:
      sent: list[dict[str, object]] = []
      monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: _session(pg_conn))
      monkeypatch.setattr(autonomous_recovery, "_now", lambda: NOW)
      monkeypatch.setattr(
          autonomous_recovery.celery_app,
          "send_task",
          lambda name, args, **kwargs: sent.append({"name": name, "args": args, **kwargs}),
      )
      autonomous_recovery.reconcile.run()
      return sent


  def _sent_for(sent, run) -> list[dict[str, object]]:
      return [
          call
          for call in sent
          if isinstance(call.get("headers"), dict)
          and call["headers"].get("operation_run_id") == str(run.id)
      ]


  def _reload(db, run) -> OperationRun:
      db.expire_all()
      current = db.get(OperationRun, run.id)
      assert current is not None
      return current


  def _cause_incident(db, code: str) -> Incident | None:
      key = build_incident_key(
          "autonomous_recovery", "operation_recovery_stop", code, IncidentFingerprint.UNKNOWN
      )
      return db.scalar(select(Incident).where(Incident.dedupe_key == key))


  def test_a_lost_v0_copy_is_resent_after_45_minutes_and_counted(db, pg_conn, monkeypatch) -> None:
      run = _run(
          db, operation_type="TRIGGER_V0_REPORT", state=OperationRunState.QUEUED,
          queue="reports", minutes_ago=46,
      )
      original_task_id = run.task_id

      sent = _reconcile(monkeypatch, pg_conn)

      assert len(_sent_for(sent, run)) == 1
      current = _reload(db, run)
      assert current.state == OperationRunState.QUEUED
      assert current.task_id != original_task_id
      assert current.redispatch_count == 1


  def test_a_publish_retry_does_not_spend_the_budget(db, pg_conn, monkeypatch) -> None:
      run = _run(
          db, operation_type="TRIGGER_V0_REPORT", state=OperationRunState.REQUESTED,
          queue="reports", minutes_ago=3, redispatch_count=3,
      )

      sent = _reconcile(monkeypatch, pg_conn)

      assert len(_sent_for(sent, run)) == 1
      assert _reload(db, run).redispatch_count == 3


  def test_an_exhausted_budget_fails_runs_under_one_cause_incident(
      db, pg_conn, monkeypatch
  ) -> None:
      first = _run(
          db, operation_type="TRIGGER_V0_REPORT", state=OperationRunState.QUEUED,
          queue="reports", minutes_ago=6 * 60 + 1, redispatch_count=3,
      )
      second = _run(
          db, operation_type="RUN_SOV", state=OperationRunState.QUEUED,
          queue="sov", minutes_ago=24 * 60 + 1, redispatch_count=3,
      )

      sent = _reconcile(monkeypatch, pg_conn)

      assert _sent_for(sent, first) == [] and _sent_for(sent, second) == []
      for run in (first, second):
          current = _reload(db, run)
          assert current.state == OperationRunState.FAILED
          assert current.safe_error_code == "OPERATION_REDISPATCH_EXHAUSTED"
          assert current.completed_at == NOW
      incident = _cause_incident(db, "OPERATION_REDISPATCH_EXHAUSTED")
      assert incident is not None
      assert incident.incident_type == "OPERATION_RECOVERY_EXHAUSTED"
      assert incident.state == "OPEN"
      assert incident.occurrence_count == 2
      outbox = db.scalars(
          select(NotificationOutbox).where(NotificationOutbox.incident_id == incident.id)
      ).all()
      assert len(outbox) == 1  # 같은 원인의 두 번째 실행은 Slack을 다시 보내지 않는다
  ```

  1-c. `backend/tests/test_notification_outbox.py:141-153` 파라미터 목록 끝에 `"OPERATION_RECOVERY_EXHAUSTED",` 추가.

- [ ] **Step 2: 실패 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_autonomous_redispatch_budget.py tests/test_notification_outbox.py -q -k "budget or redispatch_records or pure_infrastructure"`
  Expected: FAIL — `AttributeError: … has no attribute '_redispatch_budget_exhausted'`, `assert 1 == 2`(횟수 안 늘어남), `OPERATION_RECOVERY_EXHAUSTED`가 OPERATOR로 분류.
  Run(DB): `cd backend && .venv/bin/python -m pytest tests/integration/test_autonomous_redispatch_budget_postgres.py -q`
  Expected: `test_a_lost_v0_copy…` FAIL(3시간 유예라 미발송 — 0-8b를 먼저 했으면 `redispatch_count == 1` 단정에서 FAIL), `test_an_exhausted…` FAIL(그대로 재배달됨).

- [ ] **Step 3: 최소 구현**
  3-a. `backend/app/services/incident_types.py:55-69` `_DEVELOPER_INCIDENT_TYPES`의 `"UNSAFE_STORED_DISPATCH",` 다음 줄에 추가:
  ```python
          # 자율 복구가 예산을 다 쓰거나 안전하게 다시 돌릴 수 없어 멈춘 실행(원인별 사고 하나).
          "OPERATION_RECOVERY_EXHAUSTED",
  ```
  `backend/app/services/notification_copy.py:125-131` 별칭 튜플에 `"OPERATION_RECOVERY_EXHAUSTED",` 추가.

  3-b. `backend/app/workers/autonomous_recovery.py` 임포트:
  ```python
  from sqlalchemy import and_, case, func, or_, select, update
  from sqlalchemy.dialects.postgresql import insert as pg_insert
  ```
  상수 블록(0-8b에서 만든 `_REDISPATCH_BUDGET` 아래)에 추가:
  ```python
  _RECOVERY_STOP_INCIDENT_TYPE: Final = "OPERATION_RECOVERY_EXHAUSTED"
  _REDISPATCH_EXHAUSTED: Final = "OPERATION_REDISPATCH_EXHAUSTED"
  _RECOVERY_STOP_MESSAGES: Final[dict[str, str]] = {
      _REDISPATCH_EXHAUSTED: "자동 재배달을 3번 했지만 작업이 시작되지 않아 자동 복구를 멈췄습니다.",
  }
  ```
  3-c. `_queued_redispatch_grace` 아래에 예산 판정 추가:
  ```python
  def _counts_against_redispatch_budget(run: OperationRun) -> bool:
      """이 재배달이 '브로커에 있던 사본을 잃었다'는 추정인가.

      publish 실패로 남은 REQUESTED는 브로커 장애 동안 2분마다 다시 시도하므로 세지 않는다.
      미룬 실행(`not_before_at`)이 정한 시각에 나가는 것도 계획된 배달이라 세지 않는다.
      """

      if run.state == OperationRunState.RUNNING:
          return True
      return (
          run.state == OperationRunState.QUEUED
          and getattr(run, "not_before_at", None) is None
      )


  def _redispatch_budget_exhausted(run: OperationRun) -> bool:
      return (
          _counts_against_redispatch_budget(run)
          and _redispatch_count(run) >= _REDISPATCH_BUDGET
      )
  ```
  3-d. `_redispatch_operation_run`(`:716-800`) — `previous_version = run.version` 다음 줄에 `spent = 1 if _counts_against_redispatch_budget(run) else 0`을 넣고, `prepared` UPDATE의 `.values(…)` 끝(`version=prepared_version,` 앞)에 `redispatch_count=OperationRun.redispatch_count + spent,`를, 미러 블록(`run.version = prepared_version` 앞)에 `run.redispatch_count = _redispatch_count(run) + spent`를 추가한다. (`spent`는 상태를 REQUESTED로 바꾸기 **전**에 계산해야 한다.)

  3-e. 새 헬퍼 — `_fail_unsafe_operation_run`(`:1003`) 바로 위에 추가:
  ```python
  def _stop_operation_run(db, run: OperationRun, observed_at: datetime, code: str) -> bool:
      """자율 복구가 더 다루지 않는 실행을 FAILED로 끝내고 원인별 사고 하나에 묶는다.

      CAS는 읽은 task id·상태·판이 그대로일 때만 쓴다 — 같은 틱의 다른 reconciler나 늦게
      claim한 워커가 이미 전진시킨 실행을 덮지 않는다. 끝난 실행의 늦은 사본은 claim을 못 해
      STALE로 물러난다.
      """

      message = _RECOVERY_STOP_MESSAGES[code]
      stopped = db.execute(
          update(OperationRun)
          .where(
              OperationRun.id == run.id,
              OperationRun.task_id.is_not_distinct_from(run.task_id),
              OperationRun.state == run.state,
              OperationRun.version == run.version,
          )
          .values(
              state=OperationRunState.FAILED,
              completed_at=observed_at,
              heartbeat_at=None,
              lease_owner=None,
              lease_expires_at=None,
              not_before_at=None,
              safe_error_code=code,
              safe_error_message=message,
              version=OperationRun.version + 1,
          )
          .returning(OperationRun.id)
          .execution_options(synchronize_session=False)
      ).scalar_one_or_none()
      if stopped is None:
          return False
      run.state = OperationRunState.FAILED
      run.completed_at = observed_at
      run.heartbeat_at = None
      run.lease_owner = None
      run.lease_expires_at = None
      run.not_before_at = None
      run.safe_error_code = code
      run.safe_error_message = message
      run.version += 1
      db.commit()
      hospital = (
          db.get(Hospital, run.hospital_id)
          if run.operation_type == "REBUILD_SITE" and run.hospital_id is not None
          else None
      )
      if hospital is not None:
          # 사이트 준비는 이미 병원 하나당 사고 한 건이 있다(H-13).
          _open_rebuild_site_incident(db, hospital, run, observed_at)
      else:
          _open_recovery_stop_incident(db, run, observed_at, code)
      return True


  def _open_recovery_stop_incident(
      db, run: OperationRun, observed_at: datetime, code: str
  ) -> None:
      """원인 코드 하나에 사고 하나. 열려 있으면 횟수만 늘리고, 닫혔으면 새 에피소드로 다시 연다."""

      dedupe_key = build_incident_key(
          "autonomous_recovery", "operation_recovery_stop", code, IncidentFingerprint.UNKNOWN
      )
      closed = (IncidentState.RECOVERED.value, IncidentState.ACKNOWLEDGED.value)
      previous_state = db.scalar(select(Incident.state).where(Incident.dedupe_key == dedupe_key))
      message = _RECOVERY_STOP_MESSAGES[code]
      impact = "자동 복구가 멈춘 작업이 있어 진단·보고서·콘텐츠·사이트 준비 결과가 늦어질 수 있습니다."
      next_action = (
          "운영 관제의 실패한 작업에서 이 원인으로 멈춘 작업을 열어 ‘작업 다시 시도’를 누르세요. "
          "다시 멈추면 ‘개발팀 문의용 정보 복사’를 개발팀에 전달하세요."
      )
      incident = db.execute(
          pg_insert(Incident)
          .values(
              id=uuid.uuid4(),
              hospital_id=None,
              operation_run_id=run.id,
              dedupe_key=dedupe_key,
              incident_type=_RECOVERY_STOP_INCIDENT_TYPE,
              state=IncidentState.OPEN.value,
              severity=IncidentSeverity.HIGH.value,
              customer_impact=impact,
              source_type="OPERATION_RUN",
              source_id=code,
              safe_error_code=code,
              safe_error_message=message,
              next_action=next_action,
              admin_path="/operations",
              first_seen_at=observed_at,
              last_seen_at=observed_at,
              created_at=observed_at,
              updated_at=observed_at,
          )
          .on_conflict_do_update(
              index_elements=[Incident.dedupe_key],
              set_={
                  "state": IncidentState.OPEN.value,
                  "operation_run_id": run.id,
                  "last_seen_at": observed_at,
                  "occurrence_count": Incident.occurrence_count + 1,
                  "episode_seq": case(
                      (Incident.state.in_(closed), Incident.episode_seq + 1),
                      else_=Incident.episode_seq,
                  ),
                  "first_seen_at": case(
                      (Incident.state.in_(closed), observed_at),
                      else_=Incident.first_seen_at,
                  ),
                  "recovered_at": None,
                  "acknowledged_at": None,
                  "acknowledged_by_id": None,
                  "version": Incident.version + 1,
                  "updated_at": observed_at,
              },
          )
          .returning(Incident)
          .execution_options(populate_existing=True)
      ).scalar_one()
      if previous_state is not None and previous_state not in closed:
          return
      auto_assign_owner_sync(db, incident, observed_at=observed_at)
      projection = IncidentSlackProjection(
          incident_id=incident.id,
          hospital_name="시스템 공통 작업",
          severity=incident.severity,
          customer_impact=incident.customer_impact,
          next_action=incident.next_action,
          admin_path=incident.admin_path,
          owner_label=owner_label_sync(db, incident.owner_id),
          sla_label="확인 필요",
          hospital_id=None,
          operation_run_id=incident.operation_run_id,
          version=incident.version,
          problem=message,
          episode_seq=incident.episode_seq,
          incident_type=incident_type_of(incident),
      )
      enqueue_notification_sync(
          db,
          build_open_incident_notification(projection, settings.ADMIN_BASE_URL),
          now=observed_at,
      )
  ```
  3-f. 루프(`:270-281`)를 교체:
  ```python
          for run in operation_runs:
              if _site_rebuild_attempts_exhausted(run):
                  if _fail_exhausted_site_rebuild(db, run, observed_at):
                      if run.hospital_id is not None:
                          handled_site_hospitals.add(run.hospital_id)
                  continue
              if _redispatch_budget_exhausted(run):
                  if (
                      _stop_operation_run(db, run, observed_at, _REDISPATCH_EXHAUSTED)
                      and run.operation_type == "REBUILD_SITE"
                      and run.hospital_id is not None
                  ):
                      handled_site_hospitals.add(run.hospital_id)
                  continue
              redispatched = _redispatch_operation_run(db, run, observed_at)
              if redispatched:
                  operation_redispatches += 1
                  if run.operation_type == "REBUILD_SITE" and run.hospital_id is not None:
                      handled_site_hospitals.add(run.hospital_id)
  ```

- [ ] **Step 4: 통과 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_autonomous_redispatch_budget.py tests/test_notification_outbox.py tests/test_autonomous_loop_recovery.py tests/test_dispatch_deploy_safe.py -q` → Expected: passed.
  Run(DB): `cd backend && .venv/bin/python -m pytest tests/integration/test_autonomous_redispatch_budget_postgres.py tests/integration/test_autonomous_redispatch_postgres.py tests/integration/test_operation_run_deferral_postgres.py tests/integration/test_site_recovery_postgres.py -q` → Expected: passed.
  Run: `cd backend && .venv/bin/ruff check app/workers/autonomous_recovery.py tests/test_autonomous_redispatch_budget.py tests/integration/test_autonomous_redispatch_budget_postgres.py` → Expected: `All checks passed!`

- [ ] **Step 5: 커밋**
  `git add backend/app/workers/autonomous_recovery.py backend/app/services/incident_types.py backend/app/services/notification_copy.py backend/tests/test_autonomous_redispatch_budget.py backend/tests/integration/test_autonomous_redispatch_budget_postgres.py backend/tests/test_notification_outbox.py && git commit -m "fix(recovery): 재배달 예산 3회, 소진 시 FAILED와 원인별 사고 하나"`

**계약 영향:** CLAUDE.md 243행에 추가: "재배달은 유형별 예산 3회(`operation_runs.redispatch_count`, 0087)를 쓰며 QUEUED 유실·lease 만료 인수만 센다(publish 실패 REQUESTED·미룬 실행 제외). 다 쓰면 실행은 FAILED(`OPERATION_REDISPATCH_EXHAUSTED`)이고 원인별 사고 하나(`OPERATION_RECOVERY_EXHAUSTED`)에 묶인다(사이트 준비는 병원 사고)." "자동 복구가 예산 안에서 소유한 상태(RETRYING)는 … 사람의 일로 올리지 않는다"와 충돌하지 않는다(예산 소진 뒤에만 연다).
**위험·롤백:** (1) 사고는 자동으로 닫히지 않는다 — 해결 규칙은 1-2(`incident_backlog.RESOLVERS`)에서 "같은 원인 코드로 FAILED인 실행이 모두 성공한 자식 실행을 가짐" 형태로 더한다(열린 질문). (2) V0가 예산 소진으로 FAILED가 되면 병원의 V0 상태(ANALYZING 등)는 그대로다 — 기존 워커 크래시와 같은 결과이며 사고 문구가 재시도를 안내한다. (3) 롤백은 코드 되돌림(컬럼은 남겨도 무해).

---

### Task 0-9a: lease 만료 RUNNING 인수를 멱등 유형 전부로 넓힌다

**Files:**
- Modify: `backend/app/workers/autonomous_recovery.py:156-163`(`_operation_redispatch_is_due` RUNNING 분기), `:233-240`(SQL), 루프, 상수
- Test: `backend/tests/test_autonomous_redispatch_budget.py`(추가), `backend/tests/integration/test_autonomous_redispatch_budget_postgres.py`(추가)

**조사 결과(유형별 재실행 안전성):**
- lease는 claim 때 1시간(`operation_run_signals.py:36, 150`), 연장하는 Celery 경로가 없다. 가장 긴 hard limit 2700초 < 3600초라 **lease가 만료된 RUNNING은 죽은 실행**이다(`test_dispatch_deploy_safe.py:340-354`가 순서를 고정). 브로커가 같은 task id로 되돌린 사본(`redelivered`)은 자기 lease를 다시 잡을 수 있지만(`operation_run_signals.py:131-137`), 인수 재배달이 task id를 바꾼 뒤에는 STALE로 물러난다.
- `TRIGGER_V0_REPORT` — 기존 인수 대상. 측정 체크포인트로 산 단계를 다시 사지 않는다(CLAUDE.md 측정 절). ✔
- `REBUILD_SITE` — 기존. 상태 준비는 멱등, 시도 예산 `_REBUILD_SITE_ATTEMPT_BUDGET`. ✔
- `RUN_SOV` — `tasks.py:8368-8370` `_pending_weekly_manifest_specs`가 고정 manifest의 남은 칸만 고르고, 슬롯은 첫 유료 호출 전에 저장(`tasks.py:8448-8460` "Persist the whole selected repeat plan before the first paid call"). 진행 중이던 한 칸만 다시 살 수 있다. ✔
- `GENERATE_MONTHLY_REPORT` — rebuild=False: `_build_monthly_report_for_hospital`(`tasks.py:11551-11563`)가 기존 리포트가 있으면 `skipped_existing`. ✔ **rebuild=True: ✘** — `plan_report_version`(`services/monthly_period.py:154-199`)은 correlation key로 중복을 막지 않아 MANUAL_REBUILD·AUTOMATIC_RECOVERY·TEMPLATE_REFRESH가 다시 돌면 새 버전을 하나 더 만든다(커밋 뒤 크래시였다면 중복 버전·중복 마일스톤 알림). → 인수하지 않고 FAILED(`OPERATION_LEASE_EXPIRED`)+원인 사고.
- `REGENERATE_CONTENT`(`tasks.py:6064-6120`)·`REGENERATE_CONTENT_IMAGE`(`tasks.py:6618-6670`) — 공급자 호출 전 글 단위 생성 lease(`claim_generation_lease`)를 커밋하고, 살아 있는 claim이 있으면 CANCELLED, 발행·취소·비공개면 CANCELLED, 공급자 호출은 일일 예산 안. ✔
- `GENERATE_CONTENT_ITEM`(`tasks.py:5427-5503`) — 저장된 claim token을 실어 다시 보내고(`_RedispatchPolicy` 주석 `:100-102`), lease가 바뀌었으면 공급자 호출 없이 CANCELLED. 사실상 죽은 실행을 닫는 효과. ✔
- `RECERTIFY_PUBLISHED_IMAGE` — **제외.** 재인증 sweep(`_dispatch_published_image_recertifications`, `:394-422`)이 `IN_FLIGHT_TIMEOUT` 30분(`services/published_image_recertification.py:73, 336-348`) 뒤 좌초 실행을 무시하고 새 실행을 만든다. 인수하면 같은 (글, subject)에 주인이 둘이 된다(행 잠금으로 이중 결제는 막히지만 예산 셈 `attempts_spent`가 늘어난다).

- [ ] **Step 1: 실패하는 테스트 작성**
  1-a. `backend/tests/test_autonomous_redispatch_budget.py`에 추가:
  ```python
  def test_every_idempotent_policy_type_is_taken_over_and_recertify_is_not() -> None:
      """새 재배달 정책을 더하면 인수 여부를 여기서 명시적으로 정해야 한다."""

      assert autonomous_recovery._LEASE_TAKEOVER_TYPES == (
          set(autonomous_recovery._OPERATION_REDISPATCH_POLICIES) - {"RECERTIFY_PUBLISHED_IMAGE"}
      )


  @pytest.mark.parametrize(
      "operation_type",
      [
          "RUN_SOV",
          "GENERATE_MONTHLY_REPORT",
          "REGENERATE_CONTENT",
          "REGENERATE_CONTENT_IMAGE",
          "GENERATE_CONTENT_ITEM",
          "TRIGGER_V0_REPORT",
          "REBUILD_SITE",
      ],
  )
  def test_an_expired_lease_is_due_for_takeover(operation_type) -> None:
      assert autonomous_recovery._operation_redispatch_is_due(_running(operation_type), NOW)
      assert not autonomous_recovery._operation_redispatch_is_due(
          _running(operation_type, lease_delta=timedelta(seconds=1)), NOW
      )


  def test_recertify_stays_with_its_own_sweep() -> None:
      assert not autonomous_recovery._operation_redispatch_is_due(
          _running("RECERTIFY_PUBLISHED_IMAGE"), NOW
      )


  def test_a_monthly_rebuild_is_not_rerun_after_its_worker_died() -> None:
      refused = autonomous_recovery._lease_takeover_refused
      rebuild = _running("GENERATE_MONTHLY_REPORT")
      rebuild.request_payload = {"_dispatch": {"task_args": ["h", 2026, 9, True]}}
      plain = _running("GENERATE_MONTHLY_REPORT")
      plain.request_payload = {"_dispatch": {"task_args": ["h", 2026, 9]}}
      assert refused(rebuild)
      assert not refused(plain)
      assert not refused(_running("RUN_SOV"))
  ```
  1-b. `backend/tests/integration/test_autonomous_redispatch_budget_postgres.py`에 추가:
  ```python
  def test_an_expired_sov_run_is_taken_over(db, pg_conn, monkeypatch) -> None:
      run = _run(
          db, operation_type="RUN_SOV", state=OperationRunState.RUNNING, queue="sov",
          minutes_ago=90, lease_expires_at=NOW - timedelta(seconds=1),
      )

      sent = _reconcile(monkeypatch, pg_conn)

      assert len(_sent_for(sent, run)) == 1
      current = _reload(db, run)
      assert current.state == OperationRunState.QUEUED
      assert current.lease_owner is None
      assert current.redispatch_count == 1


  def test_an_expired_monthly_rebuild_is_stopped_not_rerun(db, pg_conn, monkeypatch) -> None:
      run = _run(
          db, operation_type="GENERATE_MONTHLY_REPORT", state=OperationRunState.RUNNING,
          queue="reports", args=(2026, 9, True), minutes_ago=90,
          lease_expires_at=NOW - timedelta(seconds=1),
      )

      sent = _reconcile(monkeypatch, pg_conn)

      assert _sent_for(sent, run) == []
      current = _reload(db, run)
      assert current.state == OperationRunState.FAILED
      assert current.safe_error_code == "OPERATION_LEASE_EXPIRED"
      assert _cause_incident(db, "OPERATION_LEASE_EXPIRED") is not None


  def test_an_expired_recertify_run_is_left_to_its_sweep(db, pg_conn, monkeypatch) -> None:
      run = _run(
          db, operation_type="RECERTIFY_PUBLISHED_IMAGE", state=OperationRunState.RUNNING,
          queue="content", minutes_ago=90, lease_expires_at=NOW - timedelta(seconds=1),
      )

      sent = _reconcile(monkeypatch, pg_conn)

      assert _sent_for(sent, run) == []
      assert _reload(db, run).state == OperationRunState.RUNNING
  ```

- [ ] **Step 2: 실패 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_autonomous_redispatch_budget.py -q -k "takeover or recertify or monthly_rebuild"`
  Expected: FAIL — `AttributeError: … '_LEASE_TAKEOVER_TYPES'`, RUN_SOV 등 5개 유형 `assert False`.
  Run(DB): `… tests/integration/test_autonomous_redispatch_budget_postgres.py -q -k "expired"` → `test_an_expired_sov_run_is_taken_over`(미발송)·`test_an_expired_monthly_rebuild…`(RUNNING 그대로) FAIL.

- [ ] **Step 3: 최소 구현** — `backend/app/workers/autonomous_recovery.py`
  3-a. `_OPERATION_REDISPATCH_POLICIES` 정의(`:75-117`) 바로 아래에 추가:
  ```python
  # lease가 만료된 RUNNING을 새 task id로 다시 보내도 안전한 유형과 근거(2026-10-11).
  # lease(1시간)는 가장 긴 hard limit(2700초)보다 길어, 만료된 RUNNING은 죽은 실행이다.
  _LEASE_TAKEOVER_TYPES: Final = frozenset(
      {
          "TRIGGER_V0_REPORT",  # 측정 체크포인트 — 이미 산 공급자 단계를 다시 사지 않는다
          "REBUILD_SITE",  # 상태 준비는 멱등이고 하루 시도 예산이 있다
          "RUN_SOV",  # 고정 manifest의 남은 칸만 측정, 슬롯은 유료 호출 전에 저장된다
          "GENERATE_MONTHLY_REPORT",  # rebuild=False만 — 아래 `_lease_takeover_refused`
          "REGENERATE_CONTENT",  # 글 단위 생성 lease + 일일 예산
          "REGENERATE_CONTENT_IMAGE",  # 같은 생성 lease + 이미지 일일 예산
          # 저장된 claim token이 바뀌었으면 공급자 호출 없이 CANCELLED로 끝난다.
          generation_run_control.GENERATE_CONTENT_ITEM_OPERATION,
      }
  )
  # RECERTIFY_PUBLISHED_IMAGE는 넣지 않는다 — 재인증 sweep이 30분 뒤 좌초 실행을 새 실행으로
  # 대체하므로(`IN_FLIGHT_TIMEOUT`) 인수하면 같은 (글, subject)에 주인이 둘이 된다.
  _LEASE_EXPIRED: Final = "OPERATION_LEASE_EXPIRED"
  ```
  `_RECOVERY_STOP_MESSAGES`에 항목 추가:
  ```python
      _LEASE_EXPIRED: (
          "보고서 재생성 작업이 끝나지 않은 채 중단됐습니다. 다시 실행하면 새 버전이 중복으로 생길 수 "
          "있어 자동으로 다시 실행하지 않았습니다."
      ),
  ```
  3-b. `_operation_redispatch_is_due`의 RUNNING 분기(`:156-163`)를 교체:
  ```python
      elif (
          run.operation_type in _LEASE_TAKEOVER_TYPES
          and run.state == OperationRunState.RUNNING
          and run.lease_expires_at is not None
      ):
          # 이전 워커의 lease가 만료돼 더는 이 실행을 소유할 수 없다는 것이 증명된 뒤에만 인수한다.
          # 재실행 안전성은 유형별로 `_LEASE_TAKEOVER_TYPES` 주석에 있다.
          return run.lease_expires_at <= observed_at
  ```
  3-c. SQL(`:233-240`)의 `OperationRun.operation_type.in_(("TRIGGER_V0_REPORT", "REBUILD_SITE")),`를 `OperationRun.operation_type.in_(tuple(_LEASE_TAKEOVER_TYPES)),`로.
  3-d. `_redispatch_budget_exhausted` 아래에 추가:
  ```python
  def _lease_takeover_refused(run: OperationRun) -> bool:
      """월간 보고서 재생성(rebuild=True)은 다시 돌면 새 버전을 하나 더 만든다 — 인수하지 않는다."""

      return (
          str(run.operation_type) == "GENERATE_MONTHLY_REPORT"
          and run.state == OperationRunState.RUNNING
          and _stored_monthly_report_flags(run) != ()
      )
  ```
  3-e. 루프(0-8c에서 바꾼 것)에서 `_site_rebuild_attempts_exhausted` 블록 다음, `_redispatch_budget_exhausted` 앞에 추가:
  ```python
              if _lease_takeover_refused(run):
                  _stop_operation_run(db, run, observed_at, _LEASE_EXPIRED)
                  continue
  ```

- [ ] **Step 4: 통과 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_autonomous_redispatch_budget.py tests/test_autonomous_loop_recovery.py tests/test_monthly_template_refresh.py tests/test_site_build_auto_activation.py tests/test_task_incident_control_site_build.py -q` → Expected: passed(기존 V0 인수 테스트 `test_reconciler_requeues_only_expired_running_v0_with_same_lineage`의 `version == 9` 포함).
  Run(DB): `… tests/integration/test_autonomous_redispatch_budget_postgres.py tests/integration/test_site_recovery_postgres.py tests/integration/test_published_image_recertification_postgres.py -q` → Expected: passed.

- [ ] **Step 5: 커밋**
  `git add backend/app/workers/autonomous_recovery.py backend/tests/test_autonomous_redispatch_budget.py backend/tests/integration/test_autonomous_redispatch_budget_postgres.py && git commit -m "fix(recovery): lease 만료 RUNNING 인수를 멱등 유형 전부로, 월간 재생성은 멈추고 알린다"`

**계약 영향:** CLAUDE.md 243행에 추가: "lease가 만료된 RUNNING은 멱등 유형(V0·사이트 준비·RUN_SOV·월간 보고서(rebuild 아님)·REGENERATE_CONTENT(_IMAGE)·GENERATE_CONTENT_ITEM)만 새 task id로 인수하고 같은 재배달 예산을 쓴다. 월간 보고서 재생성은 FAILED(`OPERATION_LEASE_EXPIRED`)+원인 사고, 공개 글 이미지 재인증은 자기 sweep이 맡는다."
**위험·롤백:** 인수된 콘텐츠 작업은 일일 예산 안에서 공급자를 한 번 더 부를 수 있다(멱등 가드가 먼저 막지 못한 진행 중 한 단계). RUN_SOV는 진행 중이던 한 칸을 다시 살 수 있다. 롤백은 `_LEASE_TAKEOVER_TYPES`를 `{"TRIGGER_V0_REPORT", "REBUILD_SITE"}`로 줄이는 한 줄.

---

### Task 0-9b: 7일 넘게 움직임 없는 RUNNING을 FAILED로 정리(사고 하나)

**Files:**
- Modify: `backend/app/workers/autonomous_recovery.py` — 상수, 후보 SQL(`:219-246`), 후보 필터(`:247`), 루프
- Test: `backend/tests/test_autonomous_redispatch_budget.py`, `backend/tests/integration/test_autonomous_redispatch_budget_postgres.py`

**조사 결과:**
- 9/7 잔재 `NIGHTLY_CONTENT_GENERATION`은 정책 표 밖 유형이다. 배치 기록기(`workers/generation_batch_run.py:110-158`)가 RUNNING·lease 2시간으로 만들고, 프로세스가 죽으면 아무도 끝내지 않는다(다음 밤은 새 키 `nightly:{task_id}`). 비슷하게 끝내는 주체가 없는 RUNNING: 파견이 유실된 마지막 `SITE_REVALIDATION`, 창이 닫힌 뒤의 `MONTHLY_SOV_PERIOD`.
- **판정 기준은 `started_at`이 아니라 `updated_at`이어야 한다.** `MONTHLY_SOV_PERIOD`는 24일에 만들어져 31일까지 6시간마다 다시 claim된다(`tasks.py:10074` lease 20분, `:10160-10180` 재claim이 `heartbeat_at`·버전을 갱신) — `started_at` 7일 기준이면 31일 오후의 정상 진행을 죽인다. `updated_at`은 ORM `onupdate=func.now()`(`models/operations.py:163-165`)라 Core UPDATE에도 갱신된다. 기준: RUNNING ∧ (lease 없음 ∨ 만료) ∧ `updated_at` ≤ 지금−7일.
- `SITE_REVALIDATION`은 제외한다 — 같은 reconcile의 재검증 루프(`:206-218`, `:260-268`)가 자기 RUNNING을 잠그고 `heartbeat_at`을 갱신하며 소유한다. 두 경로가 같은 틱에 같은 행을 다루지 않게 한다.
- 정책 유형의 lease 만료 RUNNING은 1시간 안에 0-9a 인수로 처리되므로, 이 규칙에 걸리는 것은 사실상 정책 밖 유형이다. 사고는 원인 코드 하나에 하나(`_open_recovery_stop_incident`) — "잔재 정리 + 사고 1건".
- 후보를 별도 SELECT로 더하면 기존 단위 테스트의 가짜 세션(`tests/test_autonomous_loop_recovery.py:102-112`)이 OperationRun 조회 순서로 결과를 돌려주므로 순서가 밀린다 → **기존 재배달 후보 SELECT 하나에 OR로 합친다**(조회 횟수 불변).

- [ ] **Step 1: 실패하는 테스트 작성**
  1-a. `backend/tests/test_autonomous_redispatch_budget.py`에 추가:
  ```python
  def _stale(state=OperationRunState.RUNNING, *, days: float, lease_delta=None):
      return SimpleNamespace(
          state=state,
          operation_type="NIGHTLY_CONTENT_GENERATION",
          updated_at=NOW - timedelta(days=days),
          lease_expires_at=None if lease_delta is None else NOW + lease_delta,
      )


  def test_only_a_week_without_progress_marks_a_run_abandoned() -> None:
      abandoned = autonomous_recovery._is_abandoned_running
      assert abandoned(_stale(days=8), NOW)
      assert abandoned(_stale(days=8, lease_delta=timedelta(days=-7)), NOW)
      assert not abandoned(_stale(days=6), NOW)
      assert not abandoned(_stale(days=8, lease_delta=timedelta(minutes=5)), NOW)
      assert not abandoned(_stale(OperationRunState.QUEUED, days=30), NOW)
      # 갱신 시각이 없는 가벼운 객체(기존 테스트의 SimpleNamespace)는 버려진 것으로 보지 않는다.
      assert not abandoned(SimpleNamespace(state=OperationRunState.RUNNING), NOW)
  ```
  1-b. `backend/tests/integration/test_autonomous_redispatch_budget_postgres.py`에 추가:
  ```python
  def _nightly(db, *, updated_days_ago: int) -> OperationRun:
      run = OperationRun(
          operation_type="NIGHTLY_CONTENT_GENERATION",
          state=OperationRunState.RUNNING,
          idempotency_key=f"nightly:{uuid.uuid4()}",
          task_id=f"nightly-{uuid.uuid4().hex[:8]}",
          request_payload={"window_start": "2026-09-08", "window_end": "2026-09-09"},
          attempt_count=1,
          started_at=NOW - timedelta(days=updated_days_ago),
          heartbeat_at=NOW - timedelta(days=updated_days_ago),
          lease_owner="dead-batch",
          lease_expires_at=NOW - timedelta(days=updated_days_ago) + timedelta(hours=2),
          requested_at=NOW - timedelta(days=updated_days_ago),
          updated_at=NOW - timedelta(days=updated_days_ago),
          version=3,
      )
      db.add(run)
      db.commit()
      return run


  def test_a_week_old_running_remnant_is_closed_with_one_incident(
      db, pg_conn, monkeypatch
  ) -> None:
      remnant = _nightly(db, updated_days_ago=8)
      recent = _nightly(db, updated_days_ago=6)

      _reconcile(monkeypatch, pg_conn)

      closed = _reload(db, remnant)
      assert closed.state == OperationRunState.FAILED
      assert closed.safe_error_code == "OPERATION_RUN_ABANDONED"
      assert closed.lease_owner is None and closed.lease_expires_at is None
      assert _reload(db, recent).state == OperationRunState.RUNNING
      incident = _cause_incident(db, "OPERATION_RUN_ABANDONED")
      assert incident is not None and incident.state == "OPEN"
  ```

- [ ] **Step 2: 실패 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_autonomous_redispatch_budget.py -q -k abandoned` → Expected: FAIL `AttributeError: … '_is_abandoned_running'`.
  Run(DB): `… tests/integration/test_autonomous_redispatch_budget_postgres.py -q -k remnant` → Expected: FAIL(`RUNNING == FAILED`).

- [ ] **Step 3: 최소 구현** — `backend/app/workers/autonomous_recovery.py`
  3-a. 상수 추가:
  ```python
  # 이만큼 아무 갱신이 없고 lease도 없는 RUNNING은 끝낼 주체가 사라진 잔재다(9/7 야간 배치 잔재).
  # 시작 시각이 아니라 마지막 갱신으로 본다 — 월간 측정 구간 실행은 24~31일 내내 RUNNING이다.
  _ABANDONED_RUNNING_AFTER: Final = timedelta(days=7)
  _RUN_ABANDONED: Final = "OPERATION_RUN_ABANDONED"
  ```
  `_RECOVERY_STOP_MESSAGES`에 `_RUN_ABANDONED: "7일 넘게 진행 기록이 없는 작업을 종료 처리했습니다."` 추가.
  3-b. `_lease_takeover_refused` 아래에 추가:
  ```python
  def _is_abandoned_running(run: OperationRun, observed_at: datetime) -> bool:
      if run.state != OperationRunState.RUNNING:
          return False
      lease = getattr(run, "lease_expires_at", None)
      if lease is not None and lease > observed_at:
          return False
      touched = getattr(run, "updated_at", None)
      return touched is not None and touched <= observed_at - _ABANDONED_RUNNING_AFTER


  def _abandoned_running_clause(observed_at: datetime):
      return and_(
          OperationRun.state == OperationRunState.RUNNING,
          # 재검증 실행은 같은 reconcile의 재검증 루프가 소유한다.
          OperationRun.operation_type != "SITE_REVALIDATION",
          or_(
              OperationRun.lease_expires_at.is_(None),
              OperationRun.lease_expires_at <= observed_at,
          ),
          OperationRun.updated_at <= observed_at - _ABANDONED_RUNNING_AFTER,
      )
  ```
  3-c. 후보 SELECT(`:219-246`)의 `.where(` 본문을 다음으로 바꾼다(기존 네 조건은 0-8b·0-9a 수정 반영본 그대로 `and_` 안에 둔다):
  ```python
                  .where(
                      or_(
                          and_(
                              OperationRun.operation_type.in_(
                                  tuple(_OPERATION_REDISPATCH_POLICIES)
                              ),
                              or_(
                                  and_(
                                      OperationRun.state == OperationRunState.REQUESTED,
                                      OperationRun.requested_at
                                      <= observed_at - _REQUESTED_REDISPATCH_GRACE,
                                  ),
                                  and_(
                                      OperationRun.state == OperationRunState.QUEUED,
                                      OperationRun.not_before_at.is_(None),
                                      func.coalesce(
                                          OperationRun.queued_at, OperationRun.requested_at
                                      )
                                      <= observed_at - _FAST_QUEUED_REDISPATCH_GRACE,
                                  ),
                                  # 미룬 실행은 적체가 아니다 — 정한 시각이 지났을 때만 보낸다.
                                  and_(
                                      OperationRun.state == OperationRunState.QUEUED,
                                      OperationRun.not_before_at <= observed_at,
                                  ),
                                  and_(
                                      OperationRun.operation_type.in_(
                                          tuple(_LEASE_TAKEOVER_TYPES)
                                      ),
                                      OperationRun.state == OperationRunState.RUNNING,
                                      OperationRun.lease_expires_at.isnot(None),
                                      OperationRun.lease_expires_at <= observed_at,
                                  ),
                              ),
                          ),
                          _abandoned_running_clause(observed_at),
                      )
                  )
  ```
  후보 필터(`:247`) `if _operation_redispatch_is_due(run, observed_at)`를
  ```python
              if _operation_redispatch_is_due(run, observed_at)
              or _is_abandoned_running(run, observed_at)
  ```
  로.
  3-d. 루프 맨 앞(`for run in operation_runs:` 바로 아래)에 추가:
  ```python
              if _is_abandoned_running(run, observed_at):
                  _stop_operation_run(db, run, observed_at, _RUN_ABANDONED)
                  continue
  ```

- [ ] **Step 4: 통과 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_autonomous_redispatch_budget.py tests/test_autonomous_loop_recovery.py tests/test_dispatch_deploy_safe.py tests/test_operation_run_signals.py -q` → Expected: passed(가짜 세션 조회 순서 불변).
  Run(DB): `… tests/integration/test_autonomous_redispatch_budget_postgres.py tests/integration/test_autonomous_redispatch_postgres.py tests/integration/test_operation_run_deferral_postgres.py tests/integration/test_site_recovery_postgres.py tests/integration/test_published_image_recertification_postgres.py -q` → Expected: passed.

- [ ] **Step 5: 커밋**
  `git add backend/app/workers/autonomous_recovery.py backend/tests/test_autonomous_redispatch_budget.py backend/tests/integration/test_autonomous_redispatch_budget_postgres.py && git commit -m "fix(recovery): 7일 넘게 움직임 없는 RUNNING 잔재를 FAILED로 정리하고 사고 하나로 알린다"`

**계약 영향:** CLAUDE.md 243행에 추가: "7일 넘게 갱신이 없고 lease도 없는 RUNNING(재검증 제외)은 자율 복구가 FAILED(`OPERATION_RUN_ABANDONED`)로 정리하고 원인 사고 하나로 알린다."
**위험·롤백:** 배포 직후 첫 틱에 오래된 잔재가 한꺼번에 FAILED가 되고 사고 1건·Slack 1건이 나간다(예: 9/7 야간 배치 잔재). 배포 전 운영 DB 조회로 대상 수를 확인한다:
```sql
SELECT operation_type, count(*) FROM operation_runs
WHERE state='RUNNING' AND operation_type <> 'SITE_REVALIDATION'
  AND (lease_expires_at IS NULL OR lease_expires_at <= now())
  AND updated_at <= now() - interval '7 days'
GROUP BY 1;
```
(실행 방법: testenv 문서 Part B의 읽기 전용 migrate Job.) 정리되는 실행에 연결된 병원 화면·감시(`pipeline_watchdog.GENERATION_BATCH_OPERATION_TYPE`, `fleet_heartbeat._GENERATION_BATCH_TYPE`)가 RUNNING 잔재를 "진행 중"으로 읽던 곳이 있으면 FAILED로 바뀌어 보일 수 있다 — 두 파일의 최신 배치 조회가 RUNNING만 고르는지 리뷰 때 확인. 롤백은 코드 되돌림(이미 FAILED가 된 잔재는 그대로 두는 것이 맞다).

---

### Task 0-D: 계약 문서 갱신

**Files:**
- Modify: `CLAUDE.md:242-243`, `docs/ops/deployment-runbook.md:203-205`

**조사 결과:** 위 각 과제의 "계약 영향"에 적은 문장이 현재 문서의 정본 문구다. `docs/releases/*`는 당시 기록이라 고치지 않는다.

- [ ] **Step 1: CLAUDE.md 243행**의 `서명 수명 \`DISPATCH_TTL_SECONDS\`(이번 릴리스는 1시간 그대로; #226 이전 워커 리비전이 모두 사라진 다음 릴리스에서 6시간으로 올린다). 수락 상한을 먼저 올리고 서명을 나중에 올리므로 롤아웃 겹침 구간의 옛 워커가 새 봉투를 거절하지 않는다.`를 `서명 수명 \`DISPATCH_TTL_SECONDS\`(6시간, 수락 상한과 같고 broker visibility_timeout 2시간 이상 — 2026-10-11에 올렸다. 워커를 #226 이전 리비전으로 롤백하지 않는다).`로, `QUEUED 유실 판정은 3시간이다.`를 `QUEUED 유실 판정은 기본 3시간, V0·월간 보고서는 45분이고 재배달마다 두 배가 된다. 재배달은 유형별 예산 3회(\`operation_runs.redispatch_count\`, 0087)를 쓰며 QUEUED 유실·lease 만료 인수만 센다(publish 실패 REQUESTED·미룬 실행 제외). 다 쓰면 FAILED(\`OPERATION_REDISPATCH_EXHAUSTED\`)이고 원인별 사고 하나(\`OPERATION_RECOVERY_EXHAUSTED\`)로 묶인다(사이트 준비는 병원 사고). lease가 만료된 RUNNING은 멱등 유형(V0·사이트 준비·RUN_SOV·월간 보고서(rebuild 아님)·REGENERATE_CONTENT(_IMAGE)·GENERATE_CONTENT_ITEM)만 인수한다 — 월간 보고서 재생성은 FAILED(\`OPERATION_LEASE_EXPIRED\`), 공개 글 이미지 재인증은 자기 sweep이 맡는다. 7일 넘게 갱신이 없고 lease도 없는 RUNNING(재검증 제외)은 FAILED(\`OPERATION_RUN_ABANDONED\`)로 정리한다.`로 바꾼다.
- [ ] **Step 2: CLAUDE.md 242행** `주기 ≤ 봉투 수명(1시간)의 Beat 작업`을 `주기 ≤ 봉투 수명(6시간)의 Beat 작업`으로. 같은 문장 뒤에 `봉투가 6시간 유효하므로 워커 정지 뒤 6시간 안의 매분 사본은 버려지지 않고 실행된다(멱등).`를 덧붙인다. 문서 버전·갱신일(`문서 버전: 2.18`)을 다음 번호와 2026-10-11로.
- [ ] **Step 3: runbook 203-205행** 문단 끝에 `2026-10-11 릴리스에서 서명을 6시간으로 올렸다(#226 이전 워커 ≤00224는 10/8 이후 트래픽 없음). 이후 워커를 00224 이하로 롤백하지 않는다.`를 더한다.
- [ ] **Step 4: 확인** Run: `make copy-guard` → Expected: 통과(문서만 변경이라 Admin 용어 가드 영향 없음).
- [ ] **Step 5: 커밋** `git add CLAUDE.md docs/ops/deployment-runbook.md && git commit -m "docs: 봉투 6시간 서명·재배달 예산·lease 인수 범위·RUNNING 잔재 정리 계약"`

**계약 영향:** 위 문장 자체.
**위험·롤백:** 문서만.

---

## 열린 질문(대표 결정 필요)

1. **`OPERATION_RECOVERY_EXHAUSTED`의 대상** — 개발 대상(`[개발 확인]`, 사람의 일 건수에 안 셈)으로 넣었다. 원인이 대개 브로커·워커·poison 메시지라서다. 다만 조치 문구는 AE도 할 수 있는 "작업 다시 시도"다. 운영 대상으로 바꾸면 일일 요약의 사람의 일 건수에 들어간다.
2. **이 사고의 자동 닫힘** — 지금 계획은 열고 끝이다(사람이 확인). 1-2(해결 규칙)에서 "같은 원인 코드로 FAILED인 실행이 모두 성공한 자식 실행을 가지면 RECOVERED"를 더할지.
3. **월간 보고서 재생성이 워커 사망으로 끊긴 경우** — 자동 재실행 대신 FAILED+사고로 사람에게 넘겼다(중복 버전·중복 알림 방지). 리포트 행이 이미 커밋됐는지 확인해 SUCCEEDED로 닫는 자동화가 필요하면 별도 과제.
