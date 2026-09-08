# PR-0D 측정·리포트·복구·보안 무결성 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 표본 부족(LIMITED) 리포트 하나가 전 병원의 Slack milestone을 멈추는 잠복 장애, V0의 프로필 전 실행과 무음 정지, 사이트 준비 복구의 무한 재큐잉, 인시던트 배정 UI 부재, 전달 기록의 명목상 hash 결합, 공유 키만으로 열려 있는 admin API, DB의 잔존 `PLAN_8`을 없앤다 (검토 등록부 H-10, H-11, H-12 일부, H-13, H-15, M-08, M-18, M-20).

**Architecture:** "측정이 최종인가"의 정의를 `monthly_report_delivery.coverage_is_final()` 하나로 통일하고 milestone 투영은 리포트별로 격리한다. 사이트 준비 복구는 raw `send_task` 대신 이미 있는 `REBUILD_SITE` OperationRun 정책을 타서 grace·재시도 예산·인시던트를 얻는다. BFF는 세션에서 파생한 **서명된 actor 단언**을 헤더로 보내고 백엔드는 사람 변경 라우트에서 그 단언을 필수·검증한다(키만으로는 변경 불가). 인시던트 배정은 이미 있는 백엔드 엔드포인트에 admin 행동을 연결한다. 전달 기록은 브라우저가 실제로 내려받은 바이트의 SHA-256을 보낸다.

**Tech Stack:** FastAPI, Celery, Alembic, pytest(SimpleNamespace + 실 PostgreSQL), Next.js 16 admin(`crypto.subtle`), `node:test`. 근거: [설계](2026-09-08-admin-hitl-simplification-design.md) §3 PR-0D, [검토](../reviews/2026-09-08-integrity-hitl-review.md) §2.

**상세도 안내:** 모든 Task를 코드 단계까지 확정했다(Task 3·5는 `autonomous_recovery.py`의 `_redispatch_operation_run`/`_rebuild_dispatch`, `operations_center_serializers.py`의 `serialize_incident_row`, `HospitalHandoff.ae_owner_id`를 읽고 확장함). Task 3의 `build_aeo_site`가 REBUILD_SITE run을 닫는지는 구현자가 첫 단계에서 확인한다.

**이 PR에서 다루지 않음(후속 PR-0D-2):** H-12의 "다시 실행이 150 슬롯 전량 재구매"(V0 lineage 재사용 설계 필요), M-09(언급률 집계 5개 통일), M-19(노출 보완 갱신/완료 제거 — Phase 1에서 화면 자체가 사라짐).

---

## 실행 결과 (2026-09-09)

| Task | 커밋 | 검수 |
|---|---|---|
| 1 측정 최종성 한 함수·milestone 격리 (H-11) | `8de0590` | `coverage_is_final`로 전달 게이트·투영기·09:00 요약이 같은 판정; 리포트별 격리로 한 병원의 LIMITED가 전체 milestone을 멈추지 않음. 남은 것: `_monthly_report_measurement_is_final`과의 이중 술어(등록부 §2.3) |
| 2 V0 게이트·보류 사유 (H-12 일부) | `55733be` | `profile_complete` 전 V0 시작 불가(API·worker·화면); 비용 보류 continuation에 `safe_error_message`; 이어가기 예산 소진이 인시던트로 표면화. lineage 재사용(재구매 방지)은 PR-0D-2 |
| 3 사이트 준비 복구 예산 (H-13) | `b6440f7` → `9df28d9` → `6bcc59c` → `e14bea9` | 병원별 `REBUILD_SITE` run 24h 창·FAILED 3회 → `SITE_BUILD_RETRIES_EXHAUSTED` 하나(원인별 dedupe, 자동 배정, outbox Slack); 스윕 소유 run은 시도마다 generic 인시던트를 열지 않음; 복구된 인시던트는 새 실패에만 touch/재오픈(episode+1); 운영자 재시도 성공 시 `record_task_success`가 회수; 두 replica 경합은 savepoint로 흡수; 2라운드: 운영자 재시도 자식 run의 실패는 스윕 대상 여부와 무관하게 예산 인시던트를 touch/재오픈하거나(없으면 generic 인시던트) 보이게 유지, 인시던트 읽기 `FOR UPDATE`+version 증가로 admin CAS와 충돌 감지, 최근 성공 이후의 실패만 예산에 산입(`services/site_build_incidents.py` 공유); 3라운드(2라운드가 만든 회귀 수정 — 상한 초과를 감수): 멱등 키 접미사를 같은 UTC 날짜에 시작한 run 수로(성공 뒤 키 재사용 방지), REBUILD_SITE는 per-run generic 인시던트를 열지 않고 운영자 실패도 병원 단위 `SITE_BUILD_FAILED` 하나(같은 dedupe 키, 성공 시 회수), touch 기준은 run id. 기록만: `/operations/rebuild-site`가 클라이언트 `Idempotency-Key`를 통과시켜 `rebuild-site:` 접두사를 흉내 내면 스윕 소유로 분류됨(D4); `_redispatch_operation_run` stale-QUEUED 창, RETRY→QUEUED `queued_at` 보존, 성공 반복 병원의 UTC 하루 1회 상한 |
| 4 서명된 actor 단언 (H-10, SEC-01) | `68cbe19` → `7f83455` | BFF `X-Admin-Actor-Assertion`(HMAC, 120초) 필수, 배치 `X-Admin-Actor-System`; Astra 블로커 후속: 쓰기 요청의 actor는 `resolve_request_actor` 한 곳에서 결정(단언 email 또는 `system:<job>`), 단언과 다른 `X-Admin-Actor`는 403 `ACTOR_ASSERTION_MISMATCH`, 시스템 actor는 `require_active_account` 라우트에서 403 `SYSTEM_ACTOR_NOT_ALLOWED`, 감사 actor도 검증된 값만. 운영 배포 전 `BFF_ACTOR_SECRET` 생성(런북). 재생 저장소 유예(§2.3); 시스템 actor가 `verified_request_actor()`만 쓰는 콘텐츠 발행·반려·Essence 승인 라우트를 통과하는 것은 배치 경로로 의도된 기존 예외 |
| 5 인시던트 배정 (H-15) | PR-1D `3ca8d5a`/`dc537f3` | PR-1D로 이동 |
| 6 전달 hash 결합 (M-08) | `d04f988` | 내려받은 바이트 hash로 결합, 대체된 판 전달 거부 |
| 7 환자 질문 시드 (M-18) + enum 정리 (M-20) | `46ca2c0`, `d525b25` → `26d7765` | 비면 시스템이 `/seed-from-matrix`와 같은 멱등 시드; 마이그레이션 `0071`이 `PLAN_8` 행을 `PLAN_12`로 옮긴 뒤 두 컬럼에 CHECK만 건다(enum 타입 유지 — 타입 교체는 롤링 배포 중 이전 리비전 연결 캐시를 깨뜨림, Astra 블로커). 운영 사전 데이터 점검 불필요 |
| 8 전체 검증 | 체크포인트 2 기록 참조 | 깨끗한 DB 전체 suite·admin·site·copy-guard·db-budget-guard |
| 검토 | — | Task 3: Fable·Codex 1라운드(계약 위반 4건 수정 `9df28d9`) → 2라운드(Fable APPROVE+LOW 1, Codex 2건) → `6bcc59c` → Fable 정적 확인. 라운드 상한 2회 도달. 나머지 Task는 각 커밋 시점 검수 |

## 환경 준비

PR-0A와 동일한 환경 준비 명령(`docs/plans/2026-09-08-pr0a-lifecycle-activation-plan.md` 참조). 마이그레이션이 있는 Task 7 뒤에는 세 DB 모두 `alembic upgrade head`.

## 파일 구조

| 파일 | 책임 | 변경 |
|---|---|---|
| `backend/app/services/monthly_report_delivery.py` | `coverage_is_final(report)` 단일 정의 | 추가 |
| `backend/app/services/monthly_events.py`, `backend/app/workers/milestone_monthly_projection.py`, `backend/app/workers/tasks.py`(09:00 요약) | 같은 정의 사용, 리포트별 예외 격리 | 수정 |
| `backend/app/api/admin/operations.py`, `backend/app/workers/tasks.py`(V0) | V0 프로필 게이트, 비용 보류 사유 문구 | 수정 |
| `backend/app/workers/autonomous_recovery.py` | 사이트 준비 복구를 OperationRun 정책으로 | 수정 |
| `backend/app/core/security.py`, `admin/lib/admin-api-proxy-route.ts`, `admin/lib/actor-assertion.ts`(신규), terraform env | 서명된 actor 단언 | 추가·수정 |
| `backend/app/api/admin/operations_center_serializers.py`(또는 행동을 만드는 실제 파일), `admin/lib/operations-center.ts`, `admin/app/operations/OperationDetail.tsx`, `useOperationsCenter.ts` | 인시던트 배정 행동 | 수정 |
| `admin/app/hospitals/[id]/reports/page.tsx`, `admin/lib/report-delivery.ts` | 내려받은 바이트 hash | 수정 |
| `admin/app/hospitals/[id]/query-targets/page.tsx` | 비어 있을 때 `seed-from-matrix` 호출 | 수정 |
| `backend/alembic/versions/0071_plan_enum_cleanup.py`(신규) | `PLAN_8` 제거, `content_schedules.plan` CHECK | 신규 |

---

### Task 1: "측정이 최종인가"는 한 함수가 정한다 + milestone 투영 격리 (H-11)

**Files:**
- Modify: `backend/app/services/monthly_report_delivery.py` (≈118-135 `monthly_report_delivery_gate` 앞)
- Modify: `backend/app/services/monthly_events.py:174-185` (`project_monthly_event`의 `coverage_complete`)
- Modify: `backend/app/workers/milestone_monthly_projection.py:66-88` (`observe_monthly_milestones`), `:158-172` (`_current_state`)
- Modify: `backend/app/workers/tasks.py:8375` (09:00 요약의 `latest.quality != "COMPLETE"`)
- Test: `backend/tests/test_monthly_events.py`(있으면; 없으면 신규), `backend/tests/test_milestone_projector_postgres.py`

- [ ] **Step 1: 테스트 작성**

`backend/tests/test_monthly_report_delivery.py`(있으면 그 파일, 없으면 신규)에:

```python
from types import SimpleNamespace

from app.services.monthly_report_delivery import coverage_is_final


def _report(quality, adequacy=None, planned=10, success=10, failed=0):
    return SimpleNamespace(
        quality=quality, planned_count=planned, success_count=success, failed_count=failed,
        sov_summary={"observation_adequacy": adequacy} if adequacy else {},
    )


def test_complete_with_full_counts_is_final():
    assert coverage_is_final(_report("COMPLETE")) is True


def test_limited_with_confirmed_slots_is_final_like_the_delivery_gate():
    assert coverage_is_final(_report("DEGRADED", {"status": "LIMITED", "confirmed_slots": 3})) is True


def test_degraded_without_limited_adequacy_is_not_final():
    assert coverage_is_final(_report("DEGRADED", {"status": "UNAVAILABLE", "confirmed_slots": 0})) is False
    assert coverage_is_final(_report("COMPLETE", planned=10, success=9)) is False
```

`backend/tests/test_milestone_projector_postgres.py`에 (파일의 기존 seeding helper를 재사용; 없으면 그 파일이 리포트를 만드는 방식을 복사):

```python
async def test_one_limited_report_does_not_stop_other_hospitals_milestones(pg_async_session):
    """H-11: LIMITED 리포트가 CUSTOMER_READY 게이트에서 예외를 던져도 다른 병원의 milestone은 투영된다."""
    limited = await _seed_ready_report(pg_async_session, quality="DEGRADED", adequacy_status="LIMITED", confirmed_slots=3)
    complete = await _seed_ready_report(pg_async_session, quality="COMPLETE")

    scan = await observe_monthly_milestones(pg_async_session, datetime.now(timezone.utc), {}, datetime.now(timezone.utc) - timedelta(days=1))

    keys = {p.hospital_id for p in scan.milestones}
    assert complete.hospital_id in keys
    assert limited.hospital_id in keys  # LIMITED는 이제 최종으로 취급돼 CUSTOMER_READY로 투영된다
```

`_seed_ready_report`는 그 파일의 실제 helper 이름·인자로 바꾼다(리포트·manifest(closed)·VALID DOCTOR artifact를 만드는 것).

- [ ] **Step 2: 실패 확인** — `coverage_is_final` ImportError; postgres 테스트는 `NotificationPayloadError("CUSTOMER_READY_GATE_BLOCKED")`로 FAIL.

- [ ] **Step 3: 구현**

`monthly_report_delivery.py`에 추가하고, `monthly_report_delivery_gate` 안의 `sample_complete or sample_limited` 계산을 이 함수 호출로 교체:

```python
def coverage_is_final(report: MonthlyReport) -> bool:
    """이번 달 측정을 '최종'으로 볼 수 있는가 — 전달 게이트·milestone·09:00 요약이 모두 이 정의를 쓴다.

    COMPLETE(전 슬롯 성공) 또는 LIMITED(표본은 부족하지만 확정 슬롯이 있고 적정성이 LIMITED로
    닫힘). 세 곳이 각자 정의하면 전달 가능한 리포트를 매일 '측정 미완료'로 재알림하거나(M-02),
    한 리포트의 게이트 예외가 전 병원의 알림을 멈춘다(H-11).
    """
    counts_complete = (
        report.planned_count > 0
        and report.success_count == report.planned_count
        and report.failed_count == 0
    )
    if report.quality == "COMPLETE":
        return counts_complete
    adequacy = (report.sov_summary or {}).get("observation_adequacy")
    return (
        report.quality == "DEGRADED"
        and isinstance(adequacy, dict)
        and adequacy.get("status") == "LIMITED"
        and int(adequacy.get("confirmed_slots") or 0) > 0
    )
```

`monthly_events.py` `project_monthly_event`: `coverage_complete = (event.quality == "COMPLETE" and ... )` → `MonthlyEvent`에 `coverage_final: bool` 필드를 추가하고 `coverage_complete = event.coverage_final and event.manifest_closed`. `_monthly_event()`(`milestone_monthly_projection.py:≈190`)가 `coverage_final=coverage_is_final(report)`를 채운다. `_current_state`의 `coverage_complete`도 `coverage_is_final(report) and facts.manifest is not None and facts.manifest.closed_at is not None`로.

`observe_monthly_milestones`: 리포트별 격리 —

```python
    current: list[tuple[ReportFacts, tuple[str, MilestoneProjection]]] = []
    for facts in facts_by_report.values():
        try:
            current.append((facts, _project_observed_current(facts, observed_at)))
        except NotificationPayloadError as exc:
            # 한 리포트의 게이트 불일치가 다른 병원의 알림까지 멈추면 안 된다. 이 리포트는
            # 이번 창에서 건너뛰고 로그로 남긴다 — 다음 창에서 다시 시도한다.
            logger.warning("monthly milestone skipped: report=%s reason=%s", facts.report.id, exc)
    current = tuple(current)
```
(`logger = logging.getLogger(__name__)` 추가.) `_project_delivery_events`의 루프도 같은 try/except로 감싼다.

`tasks.py:8375`: `if latest is not None and latest.quality != "COMPLETE":` → `if latest is not None and not coverage_is_final(latest):`.

- [ ] **Step 4: 통과 확인** — `tests/test_monthly_report_delivery.py tests/test_milestone_projector_postgres.py tests/test_monthly_*.py tests/test_admin_reports.py` → PASS.

- [ ] **Step 5: 커밋**

```bash
git add backend/app/services/monthly_report_delivery.py backend/app/services/monthly_events.py backend/app/workers/milestone_monthly_projection.py backend/app/workers/tasks.py backend/tests && git commit -m "fix: one definition of a final monthly measurement; isolate milestone projection per report

A LIMITED report was deliverable per the gate but raised in the milestone
projector, which had no per-report isolation, so every hospital's Slack
milestones stopped. (H-11, M-02)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: V0는 프로필 완료 전에 시작되지 않고, 보류 사유를 남긴다 (H-12 일부)

**Files:**
- Modify: `backend/app/api/admin/operations.py:459-495` (`trigger_v0_report_operation`)
- Modify: `backend/app/workers/tasks.py` `trigger_v0_report` 시작부(≈2790-2810, `hospital.status = ANALYZING` 전) 및 `:2971-2979` (cost-deferred `error_summary`)
- Modify: `admin/app/hospitals/[id]/dashboard/page.tsx:378` (V0 실행 버튼 disabled 조건)
- Test: `backend/tests/test_admin_operations.py`, `backend/tests/test_v0_*.py`(있으면)

- [ ] **Step 1: 테스트** (`test_admin_operations.py`의 기존 V0 트리거 테스트 픽스처 사용)

```python
async def test_trigger_v0_refuses_before_profile_complete():
    hospital = _hospital(profile_complete=False, v0_report_done=False)
    db = _OpsDB(hospital)
    with pytest.raises(HTTPException) as exc:
        await operations_api.trigger_v0_report_operation(hospital.id, db=db)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "PROFILE_INCOMPLETE"
```

worker: `tests/test_v0_*.py` 중 `trigger_v0_report`를 fake로 실행하는 파일이 있으면 `profile_complete=False` 병원으로 실행 시 run이 `CANCELLED`/`FAILED(PROFILE_INCOMPLETE)`로 끝나고 측정 슬롯이 만들어지지 않음을 assert. 없으면 API 테스트만.

- [ ] **Step 2: 구현**

`operations.py` `trigger_v0_report_operation`: `hospital = await _get_hospital_or_404(...)` 직후

```python
    if not hospital.profile_complete:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROFILE_INCOMPLETE",
                "message": "병원 기본 정보가 완료되면 초기 진단이 자동으로 시작됩니다. 먼저 필수 항목을 채워 주세요.",
                "missing": missing_profile_requirement_keys(hospital),
            },
        )
```
(import `from app.services.hospital_lifecycle import missing_profile_requirement_keys`.)

`tasks.py` `trigger_v0_report`: 병원을 로드한 직후, ANALYZING 클레임 **전**에 `if not hospital.profile_complete:` → `finish_explicit_run`/run FAILED with `safe_error_code="PROFILE_INCOMPLETE"`, `safe_error_message="병원 기본 정보가 완료되지 않아 초기 진단을 시작하지 않았습니다."` 후 return (이 task가 run을 끝내는 기존 방식 — `regenerate_content_item`의 `finish_explicit_run` 또는 이 task가 쓰는 run 종료 helper — 를 그대로 쓴다).

cost-deferred(`:2971-2979`): `run.error_summary`에 `"safe_error_message": "비용 한도로 초기 진단 측정을 잠시 멈췄습니다. 다음 비용 창에서 자동으로 이어갑니다."`를 추가.

admin dashboard: V0 실행 버튼 `disabled`에 `|| !hospital.profile_complete` 추가하고 title에 "병원 기본 정보 완료 후 자동 시작됩니다".

- [ ] **Step 3: 통과·커밋**

```bash
git add backend/app/api/admin/operations.py backend/app/workers/tasks.py 'admin/app/hospitals/[id]/dashboard/page.tsx' backend/tests && git commit -m "fix: V0 cannot start before the profile is complete; cost deferral says why (H-12)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: 사이트 준비 복구는 OperationRun 예산을 탄다 (H-13)

**Files:**
- Modify: `backend/app/workers/autonomous_recovery.py:128-150, 200-206` (병원 선택 → raw `send_task`)
- Test: `backend/tests/test_autonomous_loop_recovery.py`

- [ ] **Step 1: 테스트** (파일의 기존 fake/PG 방식 사용)

```python
def test_site_build_recovery_creates_one_rebuild_run_per_hospital_and_does_not_redispatch_while_queued(...):
    """H-13: 같은 병원을 매분 다시 큐잉하지 않는다 — QUEUED run이 grace 안에 있으면 건너뛴다."""
    # 1) profile_complete && !site_built 병원 1곳, REBUILD_SITE run 없음 → reconcile → REBUILD_SITE run 1개 생성 + dispatch 1회
    # 2) 같은 상태에서 즉시 reconcile 재실행 → dispatch 0회 (QUEUED, 1시간 grace 안)
    # 3) run.state=FAILED이고 attempt_count >= 3 → dispatch 0회, 인시던트 1건 (dedupe key = hospital)
```
(구체 코드는 그 테스트 파일이 `reconcile()`을 어떻게 호출·관측하는지에 맞춰 작성한다 — `celery_app.send_task`를 recorder로 monkeypatch하는 기존 방식이 있으면 그대로.)

- [ ] **Step 2: 구현**

`reconcile()`의 병원 루프에서 `celery_app.send_task("app.workers.tasks.build_aeo_site", ...)` 호출을 아래로 교체한다. 재발송 자체는 이미 있는 `_redispatch_operation_run(db, run, observed_at)`(같은 파일 ≈234행)이 담당한다 — `REBUILD_SITE` 정책은 `_OPERATION_REDISPATCH_POLICIES`에 이미 있고, `_rebuild_dispatch`는 `target_type == "hospital"`이면 저장 payload 없이도 `hospital_id`로 dispatch를 재구성한다.

```python
        for hospital in hospitals:
            run = _ensure_rebuild_site_run(db, hospital, observed_at)
            if run is not None:
                _redispatch_operation_run(db, run, observed_at)
        db.commit()
```

새 helper(같은 파일, `_redispatch_operation_run` 위):

```python
_REBUILD_SITE_ATTEMPT_BUDGET: Final = 3
_REBUILD_SITE_BUDGET_WINDOW: Final = timedelta(hours=24)


def _ensure_rebuild_site_run(db, hospital: Hospital, observed_at: datetime) -> OperationRun | None:
    """사이트 준비 재실행을 OperationRun 예산 아래 둔다 (H-13).

    QUEUED/RUNNING이 grace 안이면 건너뛰고, 24시간 안에 FAILED가 예산만큼 쌓였으면 인시던트
    하나(병원 단위 dedupe)로 사람에게 넘긴다. 그 밖에는 REQUESTED run을 만들어 dispatch한다.
    """
    recent = list(
        db.execute(
            select(OperationRun)
            .where(
                OperationRun.operation_type == "REBUILD_SITE",
                OperationRun.hospital_id == hospital.id,
                OperationRun.requested_at >= observed_at - _REBUILD_SITE_BUDGET_WINDOW,
            )
            .order_by(OperationRun.requested_at.desc())
        )
        .scalars()
        .all()
    )
    for run in recent:
        if run.state in (OperationRunState.REQUESTED, OperationRunState.QUEUED, OperationRunState.RUNNING):
            return run if _operation_redispatch_is_due(run, observed_at) else None
    failed = [run for run in recent if run.state == OperationRunState.FAILED]
    if len(failed) >= _REBUILD_SITE_ATTEMPT_BUDGET:
        _open_rebuild_site_incident(db, hospital, failed[0], observed_at)
        return None
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        operation_type="REBUILD_SITE",
        state=OperationRunState.REQUESTED,
        idempotency_key=f"rebuild-site:{hospital.id}:{observed_at.date().isoformat()}:{len(failed)}",
        requested_by_id=None,
        task_id=str(uuid.uuid4()),
        attempt_count=len(failed),
        total_count=1,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload={"source_type": "hospital", "source_id": str(hospital.id)},
        result_summary={},
        version=1,
    )
    db.add(run)
    db.flush()
    return run
```

`_open_rebuild_site_incident(db, hospital, last_failed_run, observed_at)`: 이 파일이 `_fail_unsafe_operation_run` 등에서 인시던트를 여는 방식(`build_incident_key` + `Incident(...)` insert 또는 `build_open_incident_notification` + `enqueue_notification_sync`)을 그대로 따라 `incident_type="SITE_BUILD_RETRIES_EXHAUSTED"`, `severity=IncidentSeverity.HIGH`, dedupe key `build_incident_key("site_build", "hospital", str(hospital.id), IncidentFingerprint(...))`, `next_action="병원 기본 정보와 공개 준비 오류를 확인하고 운영센터에서 다시 시도하세요."`, `admin_path=f"/hospitals/{hospital.id}"`로 연다. 같은 병원이 열려 있으면 `last_seen_at`만 갱신(기존 helper가 dedupe를 처리하면 그대로).

`build_aeo_site` task가 REBUILD_SITE run의 상태를 SUCCEEDED/FAILED로 닫는지 확인한다(`grep -n "REBUILD_SITE" backend/app/workers/tasks.py`). 닫지 않으면, task 시작부에서 `headers["operation_run_id"]`로 run을 찾아 RUNNING → 끝에 SUCCEEDED/FAILED로 기록하는 기존 explicit-run 패턴(`explicit_run_context`/`finish_explicit_run`)을 붙인다 — 그래야 예산 카운트가 실제 실패를 센다.

- [ ] **Step 3: 통과·커밋**

```bash
git add backend/app/workers/autonomous_recovery.py backend/tests/test_autonomous_loop_recovery.py && git commit -m "fix: bound site-build recovery with the REBUILD_SITE operation-run budget (H-13)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: 사람 변경 라우트는 서명된 actor 단언을 요구한다 (H-10)

**Files:**
- Create: `admin/lib/actor-assertion.ts`, `admin/lib/actor-assertion.test.ts`
- Modify: `admin/lib/admin-api-proxy-route.ts:157-160` (헤더 추가)
- Modify: `backend/app/core/security.py` — `_resolve_admin_actor`/`capture_admin_actor`
- Modify: `backend/app/core/config.py` — `BFF_ACTOR_SECRET: str = ""`; `terraform/cloudrun.tf`·`cloudrun_frontend.tf` env(Secret Manager 참조는 기존 `ADMIN_SESSION_SECRET`과 같은 방식)
- Test: `backend/tests/test_admin_actor_verification.py`, `admin/lib/admin-api-proxy-route.test.ts`

- [ ] **Step 1: 단언 형식**

`X-Admin-Actor-Assertion: v1.<base64url(json)>.<hex hmac-sha256>` where json = `{"email": "...", "role": "OWNER|OPERATOR", "iat": <ms>, "exp": <ms>, "nonce": "<16 bytes hex>"}`; HMAC key = `BFF_ACTOR_SECRET`; 유효기간 120초. 백엔드는 `X-Admin-Key` 검증 뒤, `_WRITE_METHODS` 요청에 대해 단언이 없거나(→ 403 `ACTOR_ASSERTION_REQUIRED`) 서명/만료가 틀리면(→ 403 `ACTOR_ASSERTION_INVALID`) 거절하고, 유효하면 `email`을 actor로 채택한다(기존 `_resolve_admin_actor`의 활성 계정 매칭은 그대로 적용). 시스템/배치 호출은 `X-Admin-Actor-System: <job-name>` 헤더로 표시하며 이 경우 단언 없이 통과하되 actor는 `system:<job-name>`으로 기록한다. GET/HEAD는 단언 없이 허용(읽기).

- [ ] **Step 2: admin 테스트·구현**

`admin/lib/actor-assertion.test.ts`:

```ts
import assert from 'node:assert/strict'
import test from 'node:test'
import { buildActorAssertion, parseActorAssertionForTest } from './actor-assertion.ts'

test('assertion carries email/role and verifies with the same secret', async () => {
  const token = await buildActorAssertion('s3cret', { email: 'ae@example.com', role: 'OPERATOR' })
  const parsed = await parseActorAssertionForTest('s3cret', token)
  assert.equal(parsed?.email, 'ae@example.com')
  assert.equal(parsed?.role, 'OPERATOR')
})

test('assertion fails with a different secret or after expiry', async () => {
  const token = await buildActorAssertion('s3cret', { email: 'ae@example.com', role: 'OPERATOR' }, { ttlMs: -1 })
  assert.equal(await parseActorAssertionForTest('s3cret', token), null)
  const fresh = await buildActorAssertion('s3cret', { email: 'ae@example.com', role: 'OPERATOR' })
  assert.equal(await parseActorAssertionForTest('other', fresh), null)
})
```

`admin/lib/actor-assertion.ts`: `session.ts`의 `importSessionKey`/`bytesToHex` 패턴을 따라 HMAC-SHA256 서명; `buildActorAssertion(secret, {email, role}, {ttlMs=120_000})`, 테스트용 `parseActorAssertionForTest`. `admin-api-proxy-route.ts`: `headers['X-Admin-Actor-Assertion'] = await buildActorAssertion(process.env.BFF_ACTOR_SECRET, { email: session.email, role: session.role })` (secret 미설정 시 500 'Server misconfigured' — 기존 `ADMIN_SESSION_SECRET` 처리와 동일). `admin-api-proxy-route.test.ts`에 헤더 존재 assertion 추가.

- [ ] **Step 3: backend 테스트·구현**

`backend/tests/test_admin_actor_verification.py`에: (a) POST without assertion → 403 `ACTOR_ASSERTION_REQUIRED`; (b) POST with valid assertion (Python에서 같은 규칙으로 서명한 헬퍼 `_sign(secret, payload)`) → actor == email; (c) expired → 403; (d) wrong secret → 403; (e) GET without assertion → 200; (f) `X-Admin-Actor-System: nightly` POST → 허용, actor == `system:nightly`. 테스트는 이 파일이 이미 쓰는 `TestClient` + `X-Admin-Key` 방식.

`security.py`: `verify_actor_assertion(raw: str | None, *, secret: str, now_ms: int) -> dict | None` (base64url decode, HMAC compare_digest, exp 검사); `capture_admin_actor`에서 `is_write`이면 위 규칙 적용. `settings.BFF_ACTOR_SECRET`가 비어 있고 `APP_ENV == "production"`이면 기동 시 실패(기존 "Production critical admin secret(s)" 검사 목록에 추가).

- [ ] **Step 4: terraform** — `BFF_ACTOR_SECRET`를 API·Admin 두 서비스 env에 Secret Manager로 주입(기존 `ADMIN_SESSION_SECRET` 블록 복사). `docs/ops/deployment-runbook.md`에 시크릿 생성 한 줄 추가.

- [ ] **Step 5: 통과·커밋** — backend `tests/test_admin_actor_verification.py tests/test_admin_auth.py tests/test_config_security.py`, admin `npm test/lint/typecheck`.

```bash
git add backend/app/core/security.py backend/app/core/config.py backend/tests admin/lib/actor-assertion.ts admin/lib/actor-assertion.test.ts admin/lib/admin-api-proxy-route.ts admin/lib/admin-api-proxy-route.test.ts terraform docs/ops/deployment-runbook.md && git commit -m "feat: require a BFF-signed actor assertion on human admin mutations

The backend admin API is reachable through the public LB and accepted any
mutation with the shared key alone, with a forgeable actor header. (H-10)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: 인시던트 배정을 admin에서 할 수 있다 (H-15)

**Files:**
- Modify: `backend/app/schemas/operations.py:168-182` (`OperationsQueueRow`에 `assign: OperationsAction | None = None`), `:221-223` (`IncidentDetailResponse`에 `assignable_accounts: list[OperationsOwner] = []`)
- Modify: `backend/app/api/admin/operations_center_serializers.py:196-275` (`serialize_incident_row`에 `assign=...`), 상세 라우트(`operations_center_incident_routes.py`의 `_incident_detail`)에서 활성 계정 목록 채우기
- Modify: `backend/app/services/incidents.py:47` (`open_or_touch_incident` — 신규 인시던트에 병원 담당 AE 자동 배정)
- Modify: `admin/lib/operations-center.ts:40-44` (kind 유니온에 `'ASSIGN_INCIDENT'`), `:486-500` (`primaryOperationsMutation`), `admin/app/operations/OperationDetail.tsx:57-62` (label) + 담당자 `<select>`, `admin/app/operations/useOperationsCenter.ts:≈210-225` (body)
- Test: `backend/tests/test_admin_operations.py`, `backend/tests/test_admin_lead_attention.py`(자동 배정), `admin/lib/operations-center.test.ts`

- [ ] **Step 1: 테스트**

`backend/tests/test_admin_operations.py`(그 파일의 인시던트 직렬화 테스트 픽스처 사용):

```python
def test_unassigned_incident_row_offers_an_assign_action():
    row = serialize_incident_row(_incident(owner_id=None), _hospital(), None, None, None, _now())
    assert row.assign is not None
    assert row.assign.kind == "ASSIGN_INCIDENT"
    assert row.assign.method == "POST"
    assert row.assign.path.endswith(f"/incidents/{row.incident_id}/assign")
    assert row.assign.reason_required is True and row.assign.requires_version is True


def test_assigned_incident_row_offers_reassign_only_as_a_labelled_action():
    row = serialize_incident_row(_incident(owner_id=uuid.uuid4()), _hospital(), _owner(), None, None, _now())
    assert row.assign is not None and row.assign.label == "담당자 변경"
```

자동 배정(실 PG 통합 테스트가 있는 파일 — `tests/integration/test_incident_*.py` 또는 `test_admin_lead_attention.py`의 방식):

```python
async def test_new_incident_is_auto_assigned_to_the_hospital_ae(pg_async_session):
    hospital, handoff, ae = await _seed_hospital_with_ae(pg_async_session)  # handoff.ae_owner_id == ae.id
    incident = await open_or_touch_incident(pg_async_session, _request(hospital_id=hospital.id))
    assert incident.owner_id == ae.id


async def test_existing_owner_is_not_overwritten_on_touch(pg_async_session):
    hospital, handoff, ae = await _seed_hospital_with_ae(pg_async_session)
    other = await _seed_account(pg_async_session, email="other@example.com")
    first = await open_or_touch_incident(pg_async_session, _request(hospital_id=hospital.id))
    first.owner_id = other.id
    await pg_async_session.commit()
    touched = await open_or_touch_incident(pg_async_session, _request(hospital_id=hospital.id))
    assert touched.owner_id == other.id
```

`admin/lib/operations-center.test.ts`:

```ts
test('an unassigned incident resolves to an ASSIGN_INCIDENT mutation before recover/ack', () => {
  const detail = makeDetail({ owner: null, assign: { kind: 'ASSIGN_INCIDENT', label: '담당자 지정', method: 'POST', path: '/api/admin/operations/hospitals/h/incidents/i/assign', enabled: true, reason_required: true, requires_version: true, requires_idempotency_key: false } })
  const mutation = primaryOperationsMutation(detail, '담당 지정', { ownerId: 'acct-1' })
  assert.equal(mutation?.kind, 'ASSIGN_INCIDENT')
  assert.equal(mutation?.ownerId, 'acct-1')
})
```
(`makeDetail`은 그 테스트 파일의 기존 detail 빌더 이름으로 맞춘다.)

- [ ] **Step 2: 백엔드 구현**

`serialize_incident_row`의 `OperationsQueueRow(...)`에:

```python
        assign=(
            OperationsAction(
                kind="ASSIGN_INCIDENT",
                label="담당자 변경" if owner is not None else "담당자 지정",
                method="POST",
                path=f"/api/admin/operations/hospitals/{hospital_id}/incidents/{incident.id}/assign",
                reason_required=True,
                requires_version=True,
            )
            if hospital_id is not None
            else None
        ),
```

`_incident_detail`(상세 응답)에서 `assignable_accounts=[OperationsOwner(id=a.id, email=a.email, role=a.role) for a in 활성 AdminUser 목록]` — 활성 계정 조회는 `accounts.py`가 쓰는 쿼리(`AdminUser.is_active.is_(True)`)를 재사용. `OperationsOwner` 필드는 `owner_projection`이 만드는 것과 같게.

`open_or_touch_incident`: `base = insert(Incident).values(...)`의 `owner_id`(현재 값이 없으면 추가)를 `request.hospital_id`가 있을 때 `HospitalHandoff.ae_owner_id`(해당 병원 handoff, 있으면)로 채운다 — insert 전에 `await db.scalar(select(HospitalHandoff.ae_owner_id).where(HospitalHandoff.hospital_id == request.hospital_id))`. upsert의 `on_conflict_do_update`에서는 `owner_id`를 **갱신하지 않는다**(기존 배정 보존). `IncidentOpenRequest`는 바꾸지 않는다.

- [ ] **Step 3: admin 구현**

`operations-center.ts`: kind 유니온에 `'ASSIGN_INCIDENT'`; `OperationsMutationDescriptor`에 `ownerId?: string`; `primaryOperationsMutation(detail, reason, options?: { ownerId?: string })`가 `row.owner == null && enabledPostAction(row.assign)`이면 ASSIGN mutation을 **먼저** 돌려준다(`targetId: row.incident_id`, `version: row.version`, `requiresIdempotencyKey: false`, `ownerId: options?.ownerId`). `useOperationsCenter.ts`의 mutate: `mutation.kind === 'ASSIGN_INCIDENT'`면 body `{ reason, expected_version: mutation.version, owner_id: mutation.ownerId, sla_due_at: null }`. `OperationDetail.tsx`: `mutationLabel`에 `case 'ASSIGN_INCIDENT': return '담당자 지정'`; 담당자가 없고 `detail.assignable_accounts.length > 0`이면 이유 입력 위에 `<select>`(계정 email 목록, 기본값 = 로그인 사용자)를 그리고 선택값을 `onMutate`에 `ownerId`로 넘긴다. 배정된 뒤에는 기존 복구/확인 행동이 그대로 나온다.

- [ ] **Step 4: 통과·커밋** — backend 해당 테스트 + admin `npm test/lint/typecheck`.

```bash
git add backend/app/schemas/operations.py backend/app/api/admin/operations_center_serializers.py backend/app/api/admin/operations_center_incident_routes.py backend/app/services/incidents.py backend/tests admin/lib/operations-center.ts admin/lib/operations-center.test.ts admin/app/operations/OperationDetail.tsx admin/app/operations/useOperationsCenter.ts && git commit -m "fix: wire incident assignment and auto-assign new incidents to the hospital's AE (H-15)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: 전달 기록은 내려받은 바이트의 hash로 결합한다 (M-08)

**Files:**
- Modify: `admin/lib/report-delivery.ts`, `admin/app/hospitals/[id]/reports/page.tsx:95-105`
- Test: `admin/lib/report-delivery.test.ts`

- [ ] `report-delivery.ts`에 `export async function sha256OfDownloadedPdf(url: string): Promise<string>` — `fetch(url, { credentials: 'same-origin' })` → `arrayBuffer()` → `crypto.subtle.digest('SHA-256')` → hex. 전달 기록 다이얼로그의 "원장 전달용 보고서 열기"가 실제로 이 fetch를 통해 파일을 받게 하고(`URL.createObjectURL`로 새 탭), 그 hash를 state에 저장; `mark-sent` body의 `artifact_sha256`은 **그 state 값**을 쓴다(`fresh.doctorArtifact.sha256` echo 제거). state가 없으면 버튼 disabled + "먼저 원장 전달용 파일을 여기서 내려받아야 합니다".
- 테스트: `sha256OfDownloadedPdf`를 `globalThis.fetch` stub으로 검증(고정 바이트 → 알려진 hex).
- 커밋 `fix(admin): bind delivery records to the bytes actually downloaded (M-08)`.

---

### Task 7: 환자 질문이 비면 시스템이 시드한다 (M-18) + DB enum 정리 (M-20)

- [ ] `admin/app/hospitals/[id]/query-targets/page.tsx:≈335`의 빈 상태에서 "수동 생성" 안내 대신 자동으로 `POST /admin/hospitals/${id}/query-targets/seed-from-matrix`를 한 번 호출(V0 완료 병원만; `hospital.v0_report_done`), 결과 `created` 수를 안내. 실패하면 사유 표시. 소스 계약 테스트: 페이지에 `seed-from-matrix` 호출이 있고 "직접 만들어" 문구가 없음.
- [ ] `backend/alembic/versions/0071_plan_enum_cleanup.py`: `UPDATE hospitals SET plan='PLAN_12' WHERE plan='PLAN_8'` 재확인 → PostgreSQL enum에서 값 제거는 rename-swap: `ALTER TYPE plan RENAME TO plan_old; CREATE TYPE plan AS ENUM ('PLAN_12','PLAN_16','PLAN_20'); ALTER TABLE hospitals ALTER COLUMN plan TYPE plan USING plan::text::plan; DROP TYPE plan_old;` + `ALTER TABLE content_schedules ADD CONSTRAINT ck_content_schedules_plan CHECK (plan IN ('PLAN_12','PLAN_16','PLAN_20'))`. downgrade는 역순. `MIGRATION_UPGRADE_DATABASE_URL` 기반 기존 마이그레이션 테스트가 통과해야 한다.
- [ ] 커밋 두 개(`fix(admin): seed patient questions from the V0 matrix instead of asking for manual entry (M-18)`, `chore(db): drop PLAN_8 from the plan enum and constrain content_schedules.plan (M-20)`).

---

### Task 8: 전체 검증

- [ ] 5432 테스트 DB 재생성 → 세 DB `alembic upgrade head` → Backend 전체 `0 failed`, ruff clean.
- [ ] `make test-frontend` 초록.
- [ ] `grep -rn "quality != \"COMPLETE\"\|quality == \"COMPLETE\"" backend/app` → `coverage_is_final` 정의 안에서만.
- [ ] 완료 보고. push하지 않는다.
