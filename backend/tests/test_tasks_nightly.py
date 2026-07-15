"""P1-3/R1 — 야간 생성 catch-up window + cap 절단 감지 + 아침 누락 경보 윈도우."""

from datetime import date
from types import SimpleNamespace
import uuid

import arrow
import httpx
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.models.content import ContentItem
from app.models.hospital import HospitalStatus
from app.workers import tasks


def test_nightly_generation_stmt_covers_window_bounds():
    """야간 배치가 며칠 누락돼도 catch-up window 안의 슬롯을 다시 집어야 한다."""
    window_start = date(2026, 6, 3)
    tomorrow = date(2026, 6, 11)

    stmt = tasks._nightly_generation_stmt(window_start, tomorrow)
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))

    assert "scheduled_date >= '2026-06-03'" in sql
    assert "scheduled_date <= '2026-06-11'" in sql
    assert "body IS NULL" in sql
    # cap+1로 읽어 절단 발생을 감지한다
    assert f"LIMIT {tasks.NIGHTLY_GENERATION_CAP + 1}" in sql


def test_generation_catchup_window_is_seven_days():
    """야간 catch-up과 아침 누락 경보가 공유하는 윈도우 (R1) — 경보 문구의 약속과 결합."""
    assert tasks.GENERATION_CATCHUP_DAYS == 7


def test_nightly_generation_stmt_filters_hospital_status():
    """야간 생성은 ACTIVE/PENDING_DOMAIN 병원만 대상 — PAUSED 등에 생성 비용 발생 방지 (결함 8)."""
    stmt = tasks._nightly_generation_stmt(date(2026, 6, 3), date(2026, 6, 11))
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))

    assert "JOIN hospitals" in sql
    assert "'ACTIVE'" in sql
    assert "'PENDING_DOMAIN'" in sql
    # PAUSED/ONBOARDING 은 IN 목록에 없어야 한다.
    assert "'PAUSED'" not in sql


# ── 결함 5: 콘텐츠 허브 공개 URL — 존재하지 않던 하드코딩 preview 도메인 제거 ──


def test_build_aeo_site_has_autoretry(monkeypatch):
    """STEP4 허브 준비 태스크는 일시 장애 시 재시도돼야 한다 (결함 10)."""
    assert tasks.build_aeo_site.max_retries == 3
    assert Exception in tasks.build_aeo_site.autoretry_for


@pytest.mark.parametrize(
    ("profile_complete", "v0_report_done", "expected"),
    [(True, True, True), (False, True, False), (True, False, False)],
)
def test_site_build_requires_profile_and_v0(profile_complete, v0_report_done, expected):
    hospital = SimpleNamespace(
        profile_complete=profile_complete,
        v0_report_done=v0_report_done,
    )
    assert tasks._site_build_prerequisites_met(hospital) is expected


def test_custom_domain_https_health_contract_and_hashed_incident_key():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "healthy.example.com":
            return httpx.Response(200)
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert tasks._check_custom_domain_https(client, "healthy.example.com") == (
            True,
            "http_200",
        )
        assert tasks._check_custom_domain_https(client, "broken.example.com") == (
            False,
            "http_503",
        )

    key = tasks._domain_health_incident_key("healthy.example.com")
    assert key.startswith("reputation:domain-health:incident:")
    assert "healthy.example.com" not in key


def test_public_site_url_prefers_aeo_domain():
    assert (
        tasks._public_site_url("clinic.example.com", "jangpyeonhan")
        == "https://clinic.example.com/"
    )


def test_public_site_url_falls_back_to_platform_subdomain(monkeypatch):
    monkeypatch.setattr(tasks.settings, "SITE_BASE_URL", "https://reputation.motionlabs.kr")
    assert (
        tasks._public_site_url(None, "jangpyeonhan")
        == "https://jangpyeonhan.reputation.motionlabs.kr/"
    )
    assert "preview.motionlabs.io" not in tasks._public_site_url(None, "jangpyeonhan")


# ── 결함 7: priority 게이팅 + HIGH 상한 ──


def test_priority_included_gating_rules():
    # HIGH: 항상 / NORMAL: 짝수주만 / LOW: 월초만
    assert tasks._priority_included("HIGH", is_even_week=False, is_month_start=False) is True
    assert tasks._priority_included("NORMAL", is_even_week=True, is_month_start=False) is True
    assert tasks._priority_included("NORMAL", is_even_week=False, is_month_start=False) is False
    assert tasks._priority_included("LOW", is_even_week=False, is_month_start=True) is True
    assert tasks._priority_included("LOW", is_even_week=False, is_month_start=False) is False


def test_apply_high_priority_cap_trims_excess_high_specs():
    specs = [{"priority": "HIGH", "n": i} for i in range(5)] + [{"priority": "NORMAL", "n": 99}]
    kept, dropped = tasks._apply_high_priority_cap(specs, cap=3)

    assert dropped == 2
    high_kept = [s for s in kept if s["priority"] == "HIGH"]
    assert len(high_kept) == 3
    # 결정론적: 앞에서부터 유지
    assert [s["n"] for s in high_kept] == [0, 1, 2]
    # NORMAL은 상한과 무관하게 유지
    assert any(s["priority"] == "NORMAL" for s in kept)


class _SpecDB:
    def __init__(self, query_matrices):
        self._qm = {qm.id: qm for qm in query_matrices}

    def get(self, _model, obj_id):
        return self._qm.get(obj_id)


def _variant(vid, qm_id, platform="CHATGPT"):
    return SimpleNamespace(
        id=vid, query_matrix_id=qm_id, platform=platform, query_text=f"Q-{vid}", is_active=True
    )


def _qm(qm_id):
    return SimpleNamespace(id=qm_id, hospital_id="h1")


def test_build_measurement_specs_gates_target_priority(monkeypatch):
    """target/variant 유래 spec도 target.priority 기준으로 주간 게이팅돼야 한다 (결함 7)."""
    hospital = SimpleNamespace(id="h1")
    qm_high, qm_normal = _qm("qm-high"), _qm("qm-normal")
    target_high = SimpleNamespace(
        id="t-high", priority="HIGH", variants=[_variant("v-high", "qm-high")]
    )
    target_normal = SimpleNamespace(
        id="t-normal", priority="NORMAL", variants=[_variant("v-normal", "qm-normal")]
    )
    db = _SpecDB([qm_high, qm_normal])
    monkeypatch.setattr(tasks.settings, "GEMINI_API_KEY", "")

    # 홀수 주차(is_even_week=False), 월초 아님 → NORMAL target 제외, HIGH만 포함
    specs, trimmed = tasks._build_measurement_specs(
        db=db,
        hospital=hospital,
        query_targets=[target_high, target_normal],
        fallback_queries=[],
        is_even_week=False,
        is_month_start=False,
        high_priority_cap=30,
    )

    assert trimmed == 0
    target_ids = {s["target_id"] for s in specs}
    assert target_ids == {"t-high"}


def test_build_measurement_specs_applies_high_cap(monkeypatch):
    hospital = SimpleNamespace(id="h1")
    qms = [_qm(f"qm-{i}") for i in range(5)]
    targets = [
        SimpleNamespace(id=f"t-{i}", priority="HIGH", variants=[_variant(f"v-{i}", f"qm-{i}")])
        for i in range(5)
    ]
    db = _SpecDB(qms)
    monkeypatch.setattr(tasks.settings, "GEMINI_API_KEY", "")

    specs, trimmed = tasks._build_measurement_specs(
        db=db,
        hospital=hospital,
        query_targets=targets,
        fallback_queries=[],
        is_even_week=True,
        is_month_start=True,
        high_priority_cap=2,
    )

    assert trimmed == 3
    assert len(specs) == 2


def test_nightly_generation_orders_carried_over_items_first():
    """전월 이월(carried_over_from) 슬롯이 cap 안에서 가장 먼저 생성돼야 한다."""
    stmt = tasks._nightly_generation_stmt(date(2026, 7, 1), date(2026, 7, 2))
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))

    order_clause = sql.split("ORDER BY", 1)[1]
    assert "carried_over_from IS NOT NULL DESC" in order_clause
    # 이월 우선 정렬이 발행 예정일 정렬보다 앞선다.
    assert order_clause.index("carried_over_from") < order_clause.index("scheduled_date")


def test_nightly_generation_stmt_uses_row_level_claiming():
    stmt = tasks._nightly_generation_stmt(date(2026, 7, 1), date(2026, 7, 2))
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    assert "FOR UPDATE" in sql
    assert "SKIP LOCKED" in sql
    assert "generation_claimed_at IS NULL" in sql


def test_generate_single_content_item_stays_draft_until_manual_publish(monkeypatch):
    item = SimpleNamespace(
        id="content-1",
        hospital_id="hospital-1",
        content_type=SimpleNamespace(value="FAQ"),
        title=None,
        body=None,
        meta_description=None,
        image_url="gs://bucket/existing.png",
        image_prompt=None,
        generated_at=None,
        body_updated_at=None,
        status=None,
        published_at=None,
        published_by=None,
        content_philosophy_id=None,
        brief_status=None,
        content_brief=None,
        essence_status=None,
        essence_check_summary=None,
        references_list=None,
        faq_question=None,
        faq_answer_summary=None,
    )
    hospital = SimpleNamespace(id="hospital-1", slug="test-clinic")
    philosophy = SimpleNamespace(id="philosophy-1")

    class _ExistingTitles:
        def all(self):
            return []

    class _ApprovedPhilosophy:
        def scalar_one_or_none(self):
            return philosophy

    class _GenerationDB:
        def __init__(self):
            self._results = [_ExistingTitles(), _ApprovedPhilosophy()]
            self.commit_calls = 0

        def execute(self, _stmt):
            return self._results.pop(0)

        def commit(self):
            self.commit_calls += 1

    async def fake_generate_content(*_args, **_kwargs):
        return {
            "title": "치질 수술 전 확인할 점",
            "body": "환자 상태에 따라 진료 방향을 설명합니다.",
            "meta_description": "진료 전 확인할 점.",
            "references": [{"title": "질병관리청", "url": "https://www.kdca.go.kr/example"}],
            "faq_question": "치질 수술 전 무엇을 확인하나요?",
            "faq_answer_summary": "증상 단계와 회복 계획을 함께 확인합니다.",
        }

    monkeypatch.setattr(tasks, "generate_content", fake_generate_content)
    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", lambda *_args: philosophy)
    monkeypatch.setattr(
        tasks,
        "screen_content_against_philosophy",
        lambda _item, _philosophy: SimpleNamespace(status="ALIGNED", summary={"ok": True}),
    )

    db = _GenerationDB()
    tasks._generate_single_content_item(db, item, hospital)

    assert item.status == tasks.ContentStatus.DRAFT
    assert item.published_at is None
    assert item.published_by is None
    assert item.generated_at is not None
    assert item.content_philosophy_id == philosophy.id
    assert db.commit_calls == 1


def test_content_item_schedule_slots_have_db_uniqueness():
    indexes = {index.name: index for index in ContentItem.__table__.indexes}
    slot_index = indexes.get("uq_content_items_schedule_slot")

    assert slot_index is not None
    assert slot_index.unique is True


def test_v0_report_requires_at_least_one_successful_measurement():
    with pytest.raises(RuntimeError, match="성공 측정 결과가 없습니다"):
        tasks._ensure_v0_has_successful_measurements(success_count=0, failure_count=5)


def test_monthly_report_failures_are_raised_for_celery_autoretry():
    with pytest.raises(RuntimeError, match="월간 리포트 실패"):
        tasks._raise_if_monthly_report_failures([("장편한외과의원", RuntimeError("pdf boom"))])


class _NestedSlotTransaction:
    def __init__(self, db):
        self._db = db

    def __enter__(self):
        self._db._staged = []
        return self

    def __exit__(self, exc_type, *_exc):
        if exc_type is None:
            self._db.persisted.extend(self._db._staged)
        self._db._staged = None
        return False


class _MonthlySlotDB:
    def __init__(self, schedules):
        self._results = [_Result(items=schedules), _Result(scalar=None), _Result(scalar=None)]
        self.execute_calls = 0
        self.flush_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.persisted = []
        self._staged = None

    def execute(self, _stmt):
        result = self._results[self.execute_calls]
        self.execute_calls += 1
        return result

    def begin_nested(self):
        return _NestedSlotTransaction(self)

    def add(self, item):
        assert self._staged is not None
        self._staged.append(item)

    def flush(self):
        self.flush_calls += 1
        if self.flush_calls == 2:
            raise IntegrityError("insert", {}, Exception("duplicate slot"))

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1
        self.persisted.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_monthly_slot_generation_isolates_valueerror_and_alerts_ops(monkeypatch):
    """발행요일이 적은 스케줄이 generate_monthly_slots ValueError를 내도 이전 병원 슬롯은
    유지되고, 이후 병원 처리도 계속되며, ops Slack 알림에 실패 병원명이 담긴다 (결함 1)."""
    hospitals = [
        SimpleNamespace(id="h1", name="첫번째의원", status=HospitalStatus.ACTIVE),
        SimpleNamespace(id="h2", name="문제의원", status=HospitalStatus.ACTIVE),
    ]
    schedules = [
        SimpleNamespace(id="s1", hospital=hospitals[0], plan="PLAN_8", publish_days=[0, 2]),
        SimpleNamespace(id="s2", hospital=hospitals[1], plan="PLAN_8", publish_days=[1]),
    ]
    db = _MonthlySlotDB(schedules)

    calls = {"n": 0}

    def fake_generate(plan, publish_days, next_month):
        calls["n"] += 1
        if publish_days == [1]:
            raise ValueError("발행요일 대비 편수가 과다")
        return [(date(2026, 3, 2), "FAQ", 1, 1)]

    alerts: list[dict] = []

    async def fake_ops_alert(**kwargs):
        alerts.append(kwargs)
        return True

    # 2월(28일) 다음 달 슬롯 생성 상황 — 25일 트리거.
    monkeypatch.setattr(
        tasks.arrow, "now", lambda *_a, **_k: arrow.get(2026, 2, 25, tzinfo="Asia/Seoul")
    )
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(tasks.notifier, "notify_ops_alert", fake_ops_alert)
    monkeypatch.setattr("app.workers.monthly_slots.generate_monthly_slots", fake_generate)

    tasks.monthly_slot_generation()

    # h1은 커밋됐고, h2 실패는 루프를 죽이지 않았다.
    assert calls["n"] == 2
    assert db.commit_calls == 1
    assert len(db.persisted) == 1
    assert db.persisted[0].hospital_id == "h1"
    # ops 알림에 실패 병원명 포함
    assert len(alerts) == 1
    assert "문제의원" in alerts[0]["message"]


def test_monthly_slot_generation_keeps_prior_success_when_later_schedule_conflicts(monkeypatch):
    hospitals = [
        SimpleNamespace(id="h1", name="첫번째의원", status=HospitalStatus.ACTIVE),
        SimpleNamespace(id="h2", name="두번째의원", status=HospitalStatus.ACTIVE),
    ]
    schedules = [
        SimpleNamespace(id="s1", hospital=hospitals[0], plan="PLAN_4", publish_days=[0]),
        SimpleNamespace(id="s2", hospital=hospitals[1], plan="PLAN_4", publish_days=[0]),
    ]
    db = _MonthlySlotDB(schedules)

    monkeypatch.setattr(
        tasks.arrow, "now", lambda *_args, **_kwargs: arrow.get(2026, 6, 25, tzinfo="Asia/Seoul")
    )
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(
        "app.workers.monthly_slots.generate_monthly_slots",
        lambda *_args, **_kwargs: [(date(2026, 7, 1), "FAQ", 1, 1)],
    )

    tasks.monthly_slot_generation()

    assert db.rollback_calls == 0
    assert db.commit_calls == 1
    assert len(db.persisted) == 1
    assert db.persisted[0].hospital_id == "h1"


def test_morning_missed_stmt_bounds_and_filters():
    """R1 — 누락 경보는 catch-up 윈도우 내, ACTIVE 병원, 승인된 운영 기준 보유만 본다."""
    today = date(2026, 6, 10)

    stmt = tasks._morning_missed_stmt(today)
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))

    assert "scheduled_date <= '2026-06-10'" in sql
    # 야간 catch-up 윈도우와 동일한 하한 — 윈도우 밖 슬롯은 영원히 재경보되지 않는다.
    assert "scheduled_date >= '2026-06-03'" in sql
    assert "body IS NULL" in sql
    assert "JOIN hospitals" in sql
    assert "'ACTIVE'" in sql
    # 운영 기준 미승인 병원은 전용 '생성 차단' 알림이 커버하므로 제외.
    assert "IN (SELECT" in sql
    assert "'APPROVED'" in sql


class _Scalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _Result:
    def __init__(self, items=None, scalar=None):
        self._items = items
        self._scalar = scalar

    def scalars(self):
        return _Scalars(self._items)

    def scalar_one(self):
        return self._scalar

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        if self._items is not None:
            return self._items[0] if self._items else None
        return self._scalar


class _FakeSyncDB:
    """첫 execute는 batch 조회, 두 번째 execute는 overflow count 조회."""

    def __init__(self, items, total_count):
        self._results = [_Result(items=items), _Result(scalar=total_count)]
        self.execute_calls = 0
        self.commit_calls = 0

    def execute(self, _stmt):
        result = self._results[self.execute_calls]
        self.execute_calls += 1
        return result

    def commit(self):
        self.commit_calls += 1


def _items(n):
    return [SimpleNamespace(id=i) for i in range(n)]


def test_load_nightly_generation_batch_without_truncation():
    db = _FakeSyncDB(_items(3), total_count=3)

    items, truncated = tasks._load_nightly_generation_batch(
        db, date(2026, 6, 10), date(2026, 6, 11)
    )

    assert len(items) == 3
    assert truncated == 0
    assert db.execute_calls == 1  # overflow count 조회 불필요
    assert db.commit_calls == 1
    assert all(item.generation_claimed_at is not None for item in items)


def test_load_nightly_generation_batch_detects_cap_truncation():
    cap = tasks.NIGHTLY_GENERATION_CAP
    db = _FakeSyncDB(_items(cap + 1), total_count=cap + 7)

    items, truncated = tasks._load_nightly_generation_batch(
        db, date(2026, 6, 10), date(2026, 6, 11)
    )

    assert len(items) == cap
    assert truncated == 7  # 정확한 잔여 건수 보고
    assert db.execute_calls == 2
    assert db.commit_calls == 1
    assert all(item.generation_claimed_at is not None for item in items)


# ── 08:00 자동 발행: due/public 상태와 Slack 복구 대상의 DB 필터 ──


def test_auto_publish_due_statement_requires_live_active_draft():
    sql = str(
        tasks._auto_publish_due_stmt(date(2026, 6, 10)).compile(
            compile_kwargs={"literal_binds": True}
        )
    )

    assert "content_items.status = 'DRAFT'" in sql
    assert "content_items.body IS NOT NULL" in sql
    assert "hospitals.status = 'ACTIVE'" in sql
    assert "hospitals.site_live IS true" in sql
    assert "content_items.scheduled_date <= '2026-06-10'" in sql


def test_pending_post_publish_notification_only_targets_system_publications():
    sql = str(
        tasks._post_publish_notification_pending_stmt(date(2026, 6, 10)).compile(
            compile_kwargs={"literal_binds": True}
        )
    )

    assert "content_items.status = 'PUBLISHED'" in sql
    assert "content_items.published_by = 'SYSTEM_AUTO_PUBLISH'" in sql
    assert "content_items.post_publish_notified_at IS NULL" in sql


def test_auto_publish_one_commits_publication_before_external_effects(monkeypatch):
    content_id = uuid.uuid4()
    hospital = SimpleNamespace(
        id=uuid.uuid4(),
        name="테스트의원",
        slug="test-clinic",
        aeo_domain="test.example.com",
        treatments=[],
        status=HospitalStatus.ACTIVE,
        site_live=True,
    )
    item = SimpleNamespace(
        id=content_id,
        hospital_id=hospital.id,
        hospital=hospital,
        status=tasks.ContentStatus.DRAFT,
        title="진료 전 확인할 점",
        body="상태에 따라 진료 방향을 설명합니다.",
        sequence_no=1,
        total_count=8,
        content_type=SimpleNamespace(value="FAQ"),
        scheduled_date=date(2026, 6, 10),
        carried_over_from=None,
        content_philosophy_id=None,
        essence_status=None,
        essence_check_summary=None,
        published_at=None,
        published_by=None,
        post_publish_notified_at=None,
        post_publish_reviewed_at=None,
        post_publish_reviewed_by=None,
    )

    class DB:
        commits = 0
        execute_calls = 0

        def execute(self, _stmt):
            results = [_Result(items=[item]), _Result(items=[hospital])]
            result = results[self.execute_calls]
            self.execute_calls += 1
            return result

        def commit(self):
            self.commits += 1

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    db = DB()
    philosophy = SimpleNamespace(id=uuid.uuid4())
    assessment = SimpleNamespace(
        publishable=True,
        code=None,
        message=None,
        violations=(),
        essence_status="ALIGNED",
        essence_summary={"blocking": False},
        philosophy_id=philosophy.id,
    )
    audits = []
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", lambda *_args: philosophy)
    monkeypatch.setattr(tasks, "assess_content_publication", lambda *_args: assessment)
    monkeypatch.setattr(
        tasks, "write_audit_log_sync", lambda *_args, **kwargs: audits.append(kwargs)
    )

    payload = tasks._auto_publish_one(content_id)

    assert db.commits == 1
    assert item.status == tasks.ContentStatus.PUBLISHED
    assert item.published_by == tasks.AUTO_PUBLISH_ACTOR
    assert item.published_at is not None
    assert payload["public_url"] == f"https://test.example.com/contents/{content_id}"
    assert audits[0]["action"] == "auto_publish_content"


def test_deliver_post_publish_notification_marks_durable_timestamp(monkeypatch):
    content_id = uuid.uuid4()
    item = SimpleNamespace(
        id=content_id,
        hospital_id=uuid.uuid4(),
        post_publish_notified_at=None,
    )
    audit_calls = []

    class DB:
        committed = False

        def execute(self, _stmt):
            return _Result(items=[item])

        def commit(self):
            self.committed = True

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    db = DB()

    async def sent(**_kwargs):
        return True

    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(tasks.notifier, "notify_content_auto_published", sent)
    monkeypatch.setattr(
        tasks,
        "write_audit_log_sync",
        lambda *_args, **kwargs: audit_calls.append(kwargs),
    )
    payload = {
        "hospital_name": "테스트의원",
        "title": "공개 글",
        "sequence_no": 1,
        "total_count": 8,
        "content_type": "FAQ",
        "scheduled_date": "2026-06-10",
        "public_url": "https://example.com/contents/1",
        "admin_url": "https://admin.example.com/content?content=1",
        "carried_over": False,
    }

    assert tasks._deliver_post_publish_notification(content_id, payload) is True
    assert item.post_publish_notified_at is not None
    assert db.committed is True
    assert audit_calls[0]["action"] == "post_publish_notification_sent"
