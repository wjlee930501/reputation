"""외부 파이프라인 감시 — Celery 없이 생존을 판정하고 한 건만 알린다."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from redis.exceptions import RedisError

from app.services import pipeline_watchdog
from app.workers.canary_tasks import EXPECTED_QUEUES, QueueCanaryFacts

KST = ZoneInfo("Asia/Seoul")


class FakeRedis:
    """감시가 실제로 쓰는 명령만 흉내 낸다(exists/hget/get/set/delete)."""

    def __init__(self, *, hashes=None, values=None, broken=False):
        self.hashes = hashes or {}
        self.values = dict(values or {})
        self.broken = broken
        self.deleted: list[str] = []
        # 만료(ex)를 흉내 내는 시계 — 테스트가 판정 시각으로 맞춘다(epoch 초).
        self.clock = 0.0
        self.expires: dict[str, float] = {}

    def _expire(self, key):
        deadline = self.expires.get(key)
        if deadline is not None and self.clock >= deadline:
            self.values.pop(key, None)
            self.expires.pop(key, None)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def _guard(self):
        if self.broken:
            raise RedisError("redis down")

    def exists(self, key):
        self._guard()
        self._expire(key)
        return 1 if key in self.values or key in self.hashes else 0

    def hget(self, key, field):
        self._guard()
        return self.hashes.get(key, {}).get(field)

    def get(self, key):
        self._guard()
        self._expire(key)
        return self.values.get(key)

    def set(self, key, value, nx=False, ex=None):
        self._guard()
        self._expire(key)
        if nx and key in self.values:
            return None
        self.values[key] = value.encode("utf-8") if isinstance(value, str) else value
        if ex is None:
            self.expires.pop(key, None)
        else:
            self.expires[key] = self.clock + ex
        return True

    def delete(self, key):
        self._guard()
        self.deleted.append(key)
        self.values.pop(key, None)
        self.expires.pop(key, None)
        return 1


class FakeResult:
    def __init__(self, rows):
        self.rows = list(rows)

    def all(self):
        return list(self.rows)


class FakeSession:
    """`scalar` 호출 순서대로 값을 돌려주는 최소 세션(발행 예정 → 공개 → 배치 시각).

    `execute`는 차단 감사 기록 조회 하나뿐이라 행 목록을 그대로 돌려준다.
    """

    def __init__(self, results, block_rows=()):
        self.results = list(results)
        self.block_rows = list(block_rows)
        self.calls = 0

    def scalar(self, _stmt):
        self.calls += 1
        return self.results.pop(0)

    def execute(self, _stmt):
        return FakeResult(self.block_rows)


def _block_row(
    code: str,
    *,
    hospital_name: str = "테스트의원",
    content_id: str | None = None,
    observed: datetime | None = None,
    scheduled_date: str = "2026-09-13",
):
    """`_publish_block_facts`가 읽는 (hospital_id, target_id, detail, created_at, name) 행."""

    return (
        "11111111-1111-1111-1111-111111111111",
        content_id or f"content-{code}",
        {"code": code, "reason": "차단 사유", "scheduled_date": scheduled_date},
        observed or datetime(2026, 9, 12, 23, 5, tzinfo=UTC),
        hospital_name,
    )


def _beat_meta(last_run: datetime) -> dict[str, dict[str, bytes]]:
    encoded = json.dumps(
        {
            "last_run_at": {
                "__type__": "datetime",
                "year": last_run.year,
                "month": last_run.month,
                "day": last_run.day,
                "hour": last_run.hour,
                "minute": last_run.minute,
                "second": last_run.second,
                "microsecond": last_run.microsecond,
                "timezone": last_run.utcoffset().total_seconds(),
            }
        }
    )
    return {
        pipeline_watchdog.beat_entry_key("canary-control"): {"meta": encoded.encode("utf-8")}
    }


def _install(
    monkeypatch,
    *,
    now: datetime,
    stale_queues=(),
    lock_held=True,
    beat_last_run: datetime | None = None,
    session_results=(0, 0, None),
    block_rows=(),
    redis_broken=False,
):
    hashes = _beat_meta(beat_last_run) if beat_last_run is not None else {}
    values = {pipeline_watchdog.beat_lock_key(): b"1"} if lock_held else {}
    client = FakeRedis(hashes=hashes, values=values, broken=redis_broken)
    monkeypatch.setattr(pipeline_watchdog, "_connect_redis", lambda: client)
    monkeypatch.setattr(
        pipeline_watchdog,
        "read_queue_canaries",
        lambda **_kwargs: QueueCanaryFacts(not stale_queues, tuple(stale_queues), "rev", {}),
    )
    report = pipeline_watchdog.evaluate(
        FakeSession(list(session_results), block_rows), now=now
    )
    return report, client


def _healthy_now() -> datetime:
    # KST 09:10 — 08:30 발행 확인 시점 이후.
    return datetime(2026, 9, 13, 0, 10, tzinfo=UTC)


def test_healthy_pipeline_reports_no_condition(monkeypatch):
    now = _healthy_now()
    report, _ = _install(
        monkeypatch,
        now=now,
        beat_last_run=now - timedelta(minutes=3),
        session_results=(0, 12, now - timedelta(hours=10)),
    )

    assert report.beat_alive is True
    assert report.queue_canaries_current is True
    assert report.publish_checked is True
    assert report.publish_missing is False
    assert report.generation_batch_stale is False
    assert report.critical_conditions == ()
    assert report.healthy is True


def test_beat_lock_without_recent_schedule_run_is_not_alive(monkeypatch):
    now = _healthy_now()
    report, _ = _install(
        monkeypatch,
        now=now,
        beat_last_run=now - timedelta(minutes=40),
        session_results=(0, 12, now - timedelta(hours=10)),
    )

    assert report.beat_lock_held is True
    assert report.beat_alive is False
    assert "15분" in report.beat_evidence
    assert pipeline_watchdog.CONDITION_BEAT_DOWN in report.critical_conditions


def test_missing_beat_lock_says_what_it_cannot_prove(monkeypatch):
    now = _healthy_now()
    report, _ = _install(
        monkeypatch,
        now=now,
        lock_held=False,
        beat_last_run=None,
        session_results=(0, 12, now - timedelta(hours=10)),
    )

    assert report.beat_alive is False
    assert "구분할 수는 없다" in report.beat_evidence


def test_only_critical_queue_staleness_becomes_an_alert_condition(monkeypatch):
    now = _healthy_now()
    non_critical, _ = _install(
        monkeypatch,
        now=now,
        stale_queues=("reports",),
        beat_last_run=now - timedelta(minutes=3),
        session_results=(0, 12, now - timedelta(hours=10)),
    )
    critical, _ = _install(
        monkeypatch,
        now=now,
        stale_queues=("content", "reports"),
        beat_last_run=now - timedelta(minutes=3),
        session_results=(0, 12, now - timedelta(hours=10)),
    )

    assert non_critical.stale_queues == ("reports",)
    assert non_critical.critical_conditions == ()
    assert critical.stale_critical_queues == ("content",)
    assert critical.critical_conditions == ("queue_stale:content",)


def test_publish_missing_only_after_0830_kst(monkeypatch):
    early = datetime(2026, 9, 12, 23, 10, tzinfo=UTC)  # KST 08:10
    late = datetime(2026, 9, 12, 23, 40, tzinfo=UTC)  # KST 08:40
    before, _ = _install(
        monkeypatch,
        now=early,
        beat_last_run=early - timedelta(minutes=2),
        session_results=(9, 0, early - timedelta(hours=9)),
    )
    after, _ = _install(
        monkeypatch,
        now=late,
        beat_last_run=late - timedelta(minutes=2),
        session_results=(9, 0, late - timedelta(hours=9)),
    )

    assert before.publish_checked is False
    assert before.publish_missing is False
    assert after.publish_missing is True
    assert after.critical_conditions == (pipeline_watchdog.CONDITION_PUBLISH_MISSING,)


def test_partial_publish_is_informational_not_an_alert(monkeypatch):
    now = _healthy_now()
    report, _ = _install(
        monkeypatch,
        now=now,
        beat_last_run=now - timedelta(minutes=2),
        session_results=(4, 8, now - timedelta(hours=10)),
    )

    assert report.publish_partial is True
    assert report.publish_missing is False
    assert report.critical_conditions == ()


def test_stale_generation_batch_is_reported_without_alerting(monkeypatch):
    now = _healthy_now()
    report, _ = _install(
        monkeypatch,
        now=now,
        beat_last_run=now - timedelta(minutes=2),
        session_results=(0, 12, now - timedelta(hours=30)),
    )

    assert report.generation_batch_stale is True
    assert report.critical_conditions == ()
    assert "26시간" in "\n".join(pipeline_watchdog._context_lines(report))


def test_redis_failure_reports_unknown_beat_and_keeps_evaluating(monkeypatch):
    now = _healthy_now()
    report, _ = _install(
        monkeypatch,
        now=now,
        stale_queues=EXPECTED_QUEUES,
        redis_broken=True,
        session_results=(0, 12, now - timedelta(hours=10)),
    )

    assert report.redis_available is False
    assert report.beat_alive is False
    assert "Redis" in report.beat_evidence
    assert pipeline_watchdog.CONDITION_BEAT_DOWN in report.critical_conditions


def test_naive_now_is_rejected():
    with pytest.raises(ValueError):
        pipeline_watchdog.evaluate(FakeSession([0, 0, None]), now=datetime(2026, 9, 13, 0, 0))


# ── 08:00~23:00 발행기가 성공했는데 due가 그대로인 하루를 어떻게 부르는가 ──
#
# 2026-09-20 운영 사고: 5건이 매시간 공개 직전 안전검사에서 되돌아왔는데 감시 문구는
# "아침 자동 발행이 실행되지 않은 상태"라고 말했다. 실행기 재시작은 아무것도 고치지
# 못한다. 발행기가 남긴 차단 감사 기록이 있으면 그 진단은 거짓이다.


def test_hourly_success_with_residual_due_is_gate_residual_not_a_missing_task(monkeypatch):
    now = datetime(2026, 9, 12, 23, 40, tzinfo=UTC)  # KST 08:40
    report, _ = _install(
        monkeypatch,
        now=now,
        beat_last_run=now - timedelta(minutes=2),
        session_results=(5, 0, now - timedelta(hours=9)),
        block_rows=(
            _block_row("CONTENT_AI_REVIEW_UNAVAILABLE", content_id="a"),
            _block_row("CONTENT_AI_REVIEW_UNAVAILABLE", content_id="b"),
            _block_row("CONTENT_IMAGE_NOT_VERIFIED", content_id="c"),
        ),
    )

    assert report.publish_gate_residual is True
    assert report.publish_missing is False
    # 게이트 보류는 판정에만 남고 알림 근거가 아니다 — 콘텐츠 파이프라인 알림이 소유한다.
    assert report.critical_conditions == ()
    assert report.publish_block_reasons == (
        ("CONTENT_AI_REVIEW_UNAVAILABLE", 2),
        ("CONTENT_IMAGE_NOT_VERIFIED", 1),
    )


def test_no_block_evidence_keeps_the_task_did_not_run_diagnosis(monkeypatch):
    now = datetime(2026, 9, 12, 23, 40, tzinfo=UTC)
    report, _ = _install(
        monkeypatch,
        now=now,
        beat_last_run=now - timedelta(minutes=2),
        session_results=(5, 0, now - timedelta(hours=9)),
    )

    assert report.publish_missing is True
    assert report.publish_gate_residual is False
    assert report.critical_conditions == (pipeline_watchdog.CONDITION_PUBLISH_MISSING,)


def test_the_same_item_blocked_every_hour_counts_once(monkeypatch):
    now = datetime(2026, 9, 12, 23, 40, tzinfo=UTC)
    report, _ = _install(
        monkeypatch,
        now=now,
        beat_last_run=now - timedelta(minutes=2),
        session_results=(1, 0, now - timedelta(hours=9)),
        block_rows=(
            _block_row(
                "CONTENT_AI_REVIEW_UNAVAILABLE",
                content_id="same",
                observed=datetime(2026, 9, 12, 23, 5, tzinfo=UTC),
            ),
            _block_row(
                "CONTENT_AI_HARD_FINDING",
                content_id="same",
                observed=datetime(2026, 9, 12, 22, 5, tzinfo=UTC),
            ),
        ),
    )

    assert len(report.publish_blocked_today) == 1
    assert report.publish_block_reasons == (("CONTENT_AI_REVIEW_UNAVAILABLE", 1),)


def test_gate_residual_is_owned_by_no_watchdog_audience(monkeypatch):
    now = datetime(2026, 9, 12, 23, 40, tzinfo=UTC)
    report, _ = _install(
        monkeypatch,
        now=now,
        beat_last_run=now - timedelta(minutes=2),
        session_results=(5, 0, now - timedelta(hours=9)),
        block_rows=(_block_row("CONTENT_AI_REVIEW_UNAVAILABLE"),),
    )

    developer = pipeline_watchdog.conditions_for(report, pipeline_watchdog.AUDIENCE_DEVELOPER)
    operator = pipeline_watchdog.conditions_for(report, pipeline_watchdog.AUDIENCE_OPERATOR)

    assert developer == ()
    assert operator == ()


def test_the_report_payload_names_hospital_and_code_for_each_blocked_item(monkeypatch):
    now = datetime(2026, 9, 12, 23, 40, tzinfo=UTC)
    report, _ = _install(
        monkeypatch,
        now=now,
        beat_last_run=now - timedelta(minutes=2),
        session_results=(1, 0, now - timedelta(hours=9)),
        block_rows=(
            _block_row("CONTENT_AI_HARD_FINDING", hospital_name="행복드림의원", content_id="x"),
        ),
    )

    payload = report.as_dict()

    assert payload["publish_gate_residual"] is True
    assert payload["publish_blocked_today"] == [
        {
            "hospital_id": "11111111-1111-1111-1111-111111111111",
            "hospital_name": "행복드림의원",
            "content_id": "x",
            "code": "CONTENT_AI_HARD_FINDING",
            "reason": "차단 사유",
            "scheduled_date": "2026-09-13",
            "observed_at": "2026-09-12T23:05:00+00:00",
        }
    ]
    assert payload["publish_block_reasons"] == [
        {"code": "CONTENT_AI_HARD_FINDING", "count": 1}
    ]


def test_redbeat_datetime_parsing_handles_offset_zone_and_iso():
    offset = pipeline_watchdog._parse_redbeat_datetime(
        {
            "__type__": "datetime",
            "year": 2026,
            "month": 9,
            "day": 13,
            "hour": 9,
            "minute": 0,
            "second": 0,
            "microsecond": 0,
            "timezone": 32400.0,
        }
    )
    named = pipeline_watchdog._parse_redbeat_datetime(
        {
            "__type__": "datetime",
            "year": 2026,
            "month": 9,
            "day": 13,
            "hour": 9,
            "minute": 0,
            "second": 0,
            "microsecond": 0,
            "timezone": "Asia/Seoul",
        }
    )

    assert offset == datetime(2026, 9, 13, 0, 0, tzinfo=UTC)
    assert named == datetime(2026, 9, 13, 0, 0, tzinfo=UTC)
    assert pipeline_watchdog._parse_redbeat_datetime("2026-09-13T00:00:00+00:00") == datetime(
        2026, 9, 13, 0, 0, tzinfo=UTC
    )
    assert pipeline_watchdog._parse_redbeat_datetime(None) is None
    assert pipeline_watchdog._parse_redbeat_datetime({"__type__": "datetime"}) is None


def _broken_report(monkeypatch, now):
    report, _ = _install(
        monkeypatch,
        now=now,
        stale_queues=("content",),
        lock_held=False,
        session_results=(9, 0, now - timedelta(hours=40)),
    )
    return report


def test_alert_copy_is_split_by_audience_without_codes_or_pii(monkeypatch):
    monkeypatch.setattr(
        pipeline_watchdog.settings, "ADMIN_BASE_URL", "https://admin.example.com", raising=False
    )
    now = datetime(2026, 9, 12, 23, 40, tzinfo=UTC)
    report = _broken_report(monkeypatch, now)
    developer = pipeline_watchdog.build_alert_text(report, pipeline_watchdog.AUDIENCE_DEVELOPER)
    operator = pipeline_watchdog.build_alert_text(report, pipeline_watchdog.AUDIENCE_OPERATOR)

    for text in (developer, operator):
        assert text.index("무슨 문제인지") < text.index("고객 영향") < text.index("지금 할 일")
        assert text.count("https://admin.example.com/operations") == 1
        assert "@" not in text
        for code in ("PUBLISH_MISSING", "BEAT_DOWN", "queue_stale", "CONTENT_", "RETRYING"):
            assert code not in text
    assert "예약 실행기" in developer
    assert "공개된 글이" in operator
    assert "예약 실행기" not in operator


def test_conditions_are_owned_by_exactly_one_audience(monkeypatch):
    now = datetime(2026, 9, 12, 23, 40, tzinfo=UTC)
    report = _broken_report(monkeypatch, now)

    developer = pipeline_watchdog.conditions_for(report, pipeline_watchdog.AUDIENCE_DEVELOPER)
    operator = pipeline_watchdog.conditions_for(report, pipeline_watchdog.AUDIENCE_OPERATOR)

    assert set(developer) | set(operator) == set(report.critical_conditions)
    assert not set(developer) & set(operator)
    assert operator == (pipeline_watchdog.CONDITION_PUBLISH_MISSING,)


def test_infra_alerts_use_the_dev_webhook_and_fall_back_when_unset(monkeypatch):
    monkeypatch.setattr(pipeline_watchdog.settings, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/ops")
    monkeypatch.setattr(
        pipeline_watchdog.settings, "SLACK_WEBHOOK_URL_DEV", "https://hooks.slack.com/dev"
    )

    assert pipeline_watchdog.webhook_for(pipeline_watchdog.AUDIENCE_DEVELOPER) == (
        "https://hooks.slack.com/dev"
    )
    assert pipeline_watchdog.webhook_for(pipeline_watchdog.AUDIENCE_OPERATOR) == (
        "https://hooks.slack.com/ops"
    )

    # 감시 전용 예외 — 개발 채널이 없으면 HOLD로 침묵하지 않고 운영 채널로 내린다.
    monkeypatch.setattr(pipeline_watchdog.settings, "SLACK_WEBHOOK_URL_DEV", "  ")
    assert pipeline_watchdog.webhook_for(pipeline_watchdog.AUDIENCE_DEVELOPER) == (
        "https://hooks.slack.com/ops"
    )


# ── 에피소드: 같은 원인 집합은 수신자별로 한 번만 알린다 ──
#
# 2026-10-09~10 운영 채널: 같은 발행 경보가 매시 다섯 번, 배포 중 Beat 재시작이 '멈춤 →
# 복구 → 멈춤 → 멈춤 → 복구', 자정에 날짜가 바뀌어 사라진 조건이 '복구'로 나갔다.


@pytest.fixture
def webhooks(monkeypatch):
    monkeypatch.setattr(pipeline_watchdog.settings, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/ops")
    monkeypatch.setattr(
        pipeline_watchdog.settings, "SLACK_WEBHOOK_URL_DEV", "https://hooks.slack.com/dev"
    )


def _at(report, now):
    """같은 사실을 다른 하트비트 시각의 보고서로 옮긴다."""
    return replace(report, observed_at=now, kst_date=now.astimezone(KST).date().isoformat())


def _infra_report(monkeypatch, now):
    """Beat 정지 하나만 성립하는 보고서(발행은 정상)."""
    report, _ = _install(
        monkeypatch,
        now=now,
        lock_held=False,
        session_results=(0, 12, now - timedelta(hours=10)),
    )
    return report


def _publish_missing_report(monkeypatch, now):
    report, _ = _install(
        monkeypatch,
        now=now,
        beat_last_run=now - timedelta(minutes=2),
        session_results=(5, 0, now - timedelta(hours=9)),
    )
    assert report.critical_conditions == (pipeline_watchdog.CONDITION_PUBLISH_MISSING,)
    return report


def _healthy_report(monkeypatch, now):
    report, _ = _install(
        monkeypatch,
        now=now,
        beat_last_run=now - timedelta(minutes=2),
        session_results=(0, 9, now - timedelta(hours=3)),
    )
    assert report.critical_conditions == ()
    return report


class Heartbeats:
    """하나의 Redis 기억을 공유하며 하트비트를 차례로 돌리고 전달 결과를 기록한다."""

    def __init__(self, monkeypatch, *, deliver=True):
        self.client = FakeRedis()
        self.deliver = deliver
        self.sent: list[tuple[str, str, str]] = []
        monkeypatch.setattr(pipeline_watchdog, "_connect_redis", lambda: self.client)

    def beat(self, report, now, *, deliver=None):
        self.client.clock = now.timestamp()
        decisions = pipeline_watchdog.decide_alerts(_at(report, now), now=now)
        ok = self.deliver if deliver is None else deliver
        delivered = tuple(bool(d.send and ok) for d in decisions)
        pipeline_watchdog.record_deliveries(decisions, delivered)
        for decision in decisions:
            if decision.send:
                self.sent.append((decision.audience, decision.kind, decision.reason))
        return decisions


def test_persistent_infra_condition_alerts_once_across_three_hours(monkeypatch, webhooks):
    start = datetime(2026, 10, 9, 5, 30, tzinfo=UTC)  # KST 14:30
    report = _infra_report(monkeypatch, start)
    beats = Heartbeats(monkeypatch)

    for step in range(36):  # 5분 간격 3시간
        beats.beat(report, start + timedelta(minutes=5 * step))

    assert beats.sent == [("developer", "ALERT", "new_condition_set")]


def test_persistent_publish_missing_alerts_once_per_day(monkeypatch, webhooks):
    start = datetime(2026, 10, 9, 23, 30, tzinfo=UTC)  # KST 10/10 08:30
    report = _publish_missing_report(monkeypatch, start)
    beats = Heartbeats(monkeypatch)

    # 08:30 발행 확인 job과 같은 분의 하트비트가 몇 초 차이로 겹친다.
    beats.beat(report, start)
    beats.beat(report, start + timedelta(seconds=4))
    for step in range(1, 48):  # 12:25까지
        beats.beat(report, start + timedelta(minutes=5 * step))

    assert beats.sent == [("operator", "ALERT", "new_condition_set")]


def test_a_new_condition_re_alerts_once_and_a_shrinking_set_stays_quiet(monkeypatch, webhooks):
    start = datetime(2026, 10, 9, 5, 30, tzinfo=UTC)
    beat_only = _infra_report(monkeypatch, start)
    both = replace(
        beat_only,
        stale_queues=("content",),
        stale_critical_queues=("content",),
        queue_canaries_current=False,
    )
    beats = Heartbeats(monkeypatch)

    beats.beat(beat_only, start)
    beats.beat(beat_only, start + timedelta(minutes=5))
    for step in range(2, 8):
        beats.beat(both, start + timedelta(minutes=5 * step))
    for step in range(8, 14):
        beats.beat(beat_only, start + timedelta(minutes=5 * step))

    assert beats.sent == [
        ("developer", "ALERT", "new_condition_set"),
        ("developer", "ALERT", "new_condition_set"),
    ]


def test_failed_delivery_retries_every_fifteen_minutes_until_delivered(monkeypatch, webhooks):
    start = datetime(2026, 10, 9, 23, 30, tzinfo=UTC)
    report = _publish_missing_report(monkeypatch, start)
    beats = Heartbeats(monkeypatch, deliver=False)

    beats.beat(report, start - timedelta(minutes=5))  # 첫 관측 — 확정은 다음 하트비트다.
    beats.beat(report, start)
    beats.beat(report, start + timedelta(minutes=5))
    beats.beat(report, start + timedelta(minutes=10))
    retried = beats.beat(report, start + timedelta(minutes=15))
    beats.deliver = True
    beats.beat(report, start + timedelta(minutes=30))
    for step in range(7, 30):
        beats.beat(report, start + timedelta(minutes=5 * step))

    assert [d.reason for d in retried if d.send] == ["retry_undelivered"]
    assert beats.sent == [
        ("operator", "ALERT", "new_condition_set"),
        ("operator", "ALERT", "retry_undelivered"),
        ("operator", "ALERT", "retry_undelivered"),
    ]


def test_recovery_is_sent_once_after_two_healthy_heartbeats(monkeypatch, webhooks):
    start = datetime(2026, 10, 9, 5, 30, tzinfo=UTC)
    broken = _infra_report(monkeypatch, start)
    healthy = _healthy_report(monkeypatch, start)
    beats = Heartbeats(monkeypatch)
    beats.beat(broken, start)
    beats.beat(broken, start + timedelta(minutes=5))

    first_healthy = beats.beat(healthy, start + timedelta(minutes=10))
    # 같은 분에 겹친 호출은 두 번째 정상 관측이 아니다(4분 간격).
    beats.beat(healthy, start + timedelta(minutes=10, seconds=5))
    recovery = beats.beat(healthy, start + timedelta(minutes=15))
    after = [beats.beat(healthy, start + timedelta(minutes=15 + 5 * step)) for step in (1, 2)]

    assert [d.send for d in first_healthy] == [False, False]
    assert [(d.audience, d.kind) for d in recovery if d.send] == [("developer", "RECOVERY")]
    assert "정상으로 돌아왔습니다" in recovery[0].text
    assert all(not d.send for decisions in after for d in decisions)
    assert beats.sent == [
        ("developer", "ALERT", "new_condition_set"),
        ("developer", "RECOVERY", "recovered"),
    ]


def test_a_single_heartbeat_beat_restart_during_deploy_sends_nothing(monkeypatch, webhooks):
    start = datetime(2026, 10, 9, 5, 30, tzinfo=UTC)
    broken = _infra_report(monkeypatch, start)
    healthy = _healthy_report(monkeypatch, start)
    beats = Heartbeats(monkeypatch)

    beats.beat(healthy, start - timedelta(minutes=5))
    beats.beat(broken, start)  # Beat 재시작 1~3분 동안 RedBeat 락이 비었다.
    for step in range(1, 6):
        beats.beat(healthy, start + timedelta(minutes=5 * step))
    # 한 시간 뒤 같은 한 번짜리 관측이 다시 와도 마찬가지다.
    beats.beat(broken, start + timedelta(minutes=30))
    beats.beat(healthy, start + timedelta(minutes=35))

    assert beats.sent == []


def test_kst_midnight_rollover_is_not_a_recovery(monkeypatch, webhooks):
    morning = datetime(2026, 10, 8, 23, 30, tzinfo=UTC)  # KST 10/9 08:30
    missing = _publish_missing_report(monkeypatch, morning)
    beats = Heartbeats(monkeypatch)
    beats.beat(missing, morning)
    beats.beat(missing, morning + timedelta(minutes=5))  # 두 번째 관측에서 확정된다.
    beats.beat(missing, datetime(2026, 10, 9, 14, 55, tzinfo=UTC))  # KST 23:55

    # 자정이 지나면 08:30 전이라 발행 판정 자체가 없다 — 정상 보고서와 같다.
    midnight = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)  # KST 10/10 00:00
    after_midnight, _ = _install(
        monkeypatch,
        now=midnight,
        beat_last_run=midnight - timedelta(minutes=2),
        session_results=(5, 0, midnight - timedelta(hours=1)),
    )
    assert after_midnight.critical_conditions == ()
    beats.beat(after_midnight, midnight)
    beats.beat(after_midnight, midnight + timedelta(minutes=5))
    # 다음 날 08:30에 또 0건이면 그날의 새 사실로 한 번 알린다(전날 확정은 이어받지 않는다).
    next_morning = datetime(2026, 10, 9, 23, 30, tzinfo=UTC)
    first_look = beats.beat(missing, next_morning)
    assert all(not decision.send for decision in first_look)
    beats.beat(missing, next_morning + timedelta(minutes=5))

    assert beats.sent == [
        ("operator", "ALERT", "new_condition_set"),
        ("operator", "ALERT", "new_condition_set"),
    ]


def test_a_gate_residual_only_day_sends_nothing(monkeypatch, webhooks):
    start = datetime(2026, 10, 9, 23, 30, tzinfo=UTC)  # KST 10/10 08:30
    report, _ = _install(
        monkeypatch,
        now=start,
        beat_last_run=start - timedelta(minutes=2),
        session_results=(5, 0, start - timedelta(hours=9)),
        block_rows=(
            _block_row("MISSING_REFERENCES", content_id="a"),
            _block_row("MISSING_REFERENCES", content_id="b"),
            _block_row("CONTENT_AI_HARD_FINDING", content_id="c"),
        ),
    )
    beats = Heartbeats(monkeypatch)

    for step in range(0, 50):
        beats.beat(report, start + timedelta(minutes=5 * step))

    payload = report.as_dict()
    assert payload["publish_gate_residual"] is True
    assert len(payload["publish_blocked_today"]) == 3
    assert beats.sent == []


def test_infra_only_failure_does_not_alert_the_operations_channel(monkeypatch, webhooks):
    now = _healthy_now()
    report, _ = _install(
        monkeypatch,
        now=now,
        stale_queues=("content",),
        lock_held=False,
        session_results=(0, 12, now - timedelta(hours=10)),
    )
    beats = Heartbeats(monkeypatch)

    first = beats.beat(report, now)
    developer, operator = beats.beat(report, now + timedelta(minutes=5))

    assert [(d.send, d.reason) for d in first] == [
        (False, "healthy"),
        (False, "healthy"),
    ]
    assert developer.send is True and developer.webhook_url == "https://hooks.slack.com/dev"
    assert operator.send is False and operator.reason == "healthy"


def test_an_episode_stored_in_the_old_format_is_not_announced_again(monkeypatch, webhooks):
    start = datetime(2026, 10, 9, 5, 30, tzinfo=UTC)
    report = _infra_report(monkeypatch, start)
    beats = Heartbeats(monkeypatch)
    beats.client.values[pipeline_watchdog._state_key("developer")] = json.dumps(
        [pipeline_watchdog.CONDITION_BEAT_DOWN]
    ).encode("utf-8")

    beats.beat(report, start)
    beats.beat(report, start + timedelta(minutes=5))

    assert beats.sent == []


def test_a_remembered_gate_residual_alert_is_dropped_without_a_recovery(monkeypatch, webhooks):
    start = datetime(2026, 10, 10, 3, 0, tzinfo=UTC)  # KST 12:00, 배포 직후
    healthy = _healthy_report(monkeypatch, start)
    beats = Heartbeats(monkeypatch)
    for value in (
        [pipeline_watchdog.CONDITION_PUBLISH_GATE_RESIDUAL],
        {
            "conditions": [pipeline_watchdog.CONDITION_PUBLISH_GATE_RESIDUAL],
            "kst_date": "2026-10-10",
            "opened_at": "",
            "delivered": True,
        },
    ):
        beats.client.values[pipeline_watchdog._state_key("operator")] = json.dumps(
            value
        ).encode("utf-8")
        beats.beat(healthy, start)
        start += timedelta(minutes=5)

    assert beats.sent == []


def test_redis_outage_fails_open_at_most_once_per_twenty_minutes(monkeypatch, webhooks):
    start = datetime(2026, 10, 9, 5, 0, tzinfo=UTC)  # KST 14:00
    broken = _broken_report(monkeypatch, start)
    monkeypatch.setattr(pipeline_watchdog, "_connect_redis", lambda: FakeRedis(broken=True))

    sent = []
    for step in range(24):  # 두 시간
        now = start + timedelta(minutes=5 * step)
        for decision in pipeline_watchdog.decide_alerts(_at(broken, now), now=now):
            if decision.send:
                local = now.astimezone(KST)
                sent.append((f"{local:%H:%M}", decision.audience, decision.reason))
    # 전달 기록도 Redis 없이 예외 없이 넘어가야 한다.
    pipeline_watchdog.record_deliveries(
        pipeline_watchdog.decide_alerts(broken, now=start), (True, True)
    )

    # 실제 장애가 알려지기까지 최대 20분, 시간당 최대 세 건.
    assert sent == [
        (slot, audience, "redis_unavailable")
        for slot in ("14:00", "14:20", "14:40", "15:00", "15:20", "15:40")
        for audience in ("developer", "operator")
    ]


def test_healthy_state_without_prior_alert_sends_nothing(monkeypatch):
    now = _healthy_now()
    client = FakeRedis()
    healthy, _ = _install(
        monkeypatch,
        now=now,
        beat_last_run=now - timedelta(minutes=2),
        session_results=(0, 12, now - timedelta(hours=10)),
    )
    monkeypatch.setattr(pipeline_watchdog, "_connect_redis", lambda: client)

    decisions = pipeline_watchdog.decide_alerts(healthy, now=now)

    assert [(d.send, d.reason) for d in decisions] == [(False, "healthy"), (False, "healthy")]


def test_healthy_state_sends_no_recovery_when_redis_is_down(monkeypatch):
    now = _healthy_now()
    healthy, _ = _install(
        monkeypatch,
        now=now,
        beat_last_run=now - timedelta(minutes=2),
        session_results=(0, 12, now - timedelta(hours=10)),
    )
    monkeypatch.setattr(pipeline_watchdog, "_connect_redis", lambda: FakeRedis(broken=True))

    decisions = pipeline_watchdog.decide_alerts(healthy, now=now)

    assert all(d.send is False for d in decisions)
    assert {d.reason for d in decisions} == {"redis_unavailable_no_state"}


async def test_deliver_posts_directly_to_the_audience_webhook(monkeypatch):
    import httpx

    monkeypatch.setattr(
        pipeline_watchdog.settings,
        "SLACK_WEBHOOK_ALLOWED_HOSTS",
        "hooks.slack.com",
        raising=False,
    )
    seen: list[tuple[str, dict]] = []

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            seen.append((url, json))
            return httpx.Response(200, text="ok", request=httpx.Request("POST", url))

    monkeypatch.setattr(pipeline_watchdog.httpx, "AsyncClient", FakeAsyncClient)
    decision = pipeline_watchdog.AlertDecision(
        True, "ALERT", "developer", "본문", "new_condition_set", "https://hooks.slack.com/dev"
    )
    skipped = pipeline_watchdog.AlertDecision(False, None, "operator", None, "healthy", None)

    assert await pipeline_watchdog.deliver_all((decision, skipped)) == (True, False)
    assert seen == [("https://hooks.slack.com/dev", {"text": "본문"})]


def _recording_client(seen, responses):
    import httpx

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            seen.append((url, json))
            status = responses.get(url, 200)
            headers = {"Location": "https://example.invalid/"} if 300 <= status <= 399 else {}
            return httpx.Response(
                status, text="ok", headers=headers, request=httpx.Request("POST", url)
            )

    return FakeAsyncClient


async def test_a_dead_developer_webhook_falls_back_to_the_operator_channel(monkeypatch, caplog):
    # 2026-09-19부터 개발 웹훅이 302를 돌려줘 감시 경보가 85번 ERROR 로그로만 사라졌다.
    ops, dev = "https://hooks.slack.com/ops", "https://hooks.slack.com/dev"
    monkeypatch.setattr(pipeline_watchdog.settings, "SLACK_WEBHOOK_URL", ops)
    monkeypatch.setattr(pipeline_watchdog.settings, "SLACK_WEBHOOK_URL_DEV", dev)
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        pipeline_watchdog.httpx, "AsyncClient", _recording_client(seen, {dev: 302})
    )
    decision = pipeline_watchdog.AlertDecision(
        True, "ALERT", "developer", "[Error : 오류 발생] 본문", "new_condition_set", dev
    )

    assert await pipeline_watchdog.deliver(decision) is True
    assert [url for url, _ in seen] == [dev, ops]
    assert seen[1][1] == {"text": "[Error : 오류 발생] [채널 대체 전송] 본문"}
    assert not [record for record in caplog.records if record.levelname == "ERROR"]


async def test_single_channel_mode_marks_developer_alerts_on_the_operator_channel(monkeypatch):
    ops = "https://hooks.slack.com/ops"
    monkeypatch.setattr(pipeline_watchdog.settings, "SLACK_WEBHOOK_URL", ops)
    monkeypatch.setattr(pipeline_watchdog.settings, "SLACK_WEBHOOK_URL_DEV", "")
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(pipeline_watchdog.httpx, "AsyncClient", _recording_client(seen, {}))
    decision = pipeline_watchdog.AlertDecision(
        True,
        "ALERT",
        "developer",
        "[Error : 오류 발생] 본문",
        "new_condition_set",
        pipeline_watchdog.webhook_for("developer"),
    )

    assert await pipeline_watchdog.deliver(decision) is True
    assert seen == [(ops, {"text": "[Error : 오류 발생] [개발 확인] 본문"})]


async def test_a_developer_timeout_is_not_duplicated_on_the_operator_channel(monkeypatch, caplog):
    import httpx

    ops, dev = "https://hooks.slack.com/ops", "https://hooks.slack.com/dev"
    monkeypatch.setattr(pipeline_watchdog.settings, "SLACK_WEBHOOK_URL", ops)
    monkeypatch.setattr(pipeline_watchdog.settings, "SLACK_WEBHOOK_URL_DEV", dev)
    seen: list[str] = []

    class TimeoutClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            seen.append(url)
            raise httpx.ReadTimeout("slow", request=httpx.Request("POST", url))

    monkeypatch.setattr(pipeline_watchdog.httpx, "AsyncClient", TimeoutClient)
    decision = pipeline_watchdog.AlertDecision(True, "ALERT", "developer", "본문", "x", dev)

    # 개발 채널이 받았을 수도 있다 — 운영 채널로 중복 전송하지 않고 경고만 남긴다.
    assert await pipeline_watchdog.deliver(decision) is False
    assert seen == [dev, dev]
    assert not [record for record in caplog.records if record.levelname == "ERROR"]


async def test_operator_alerts_never_fall_back(monkeypatch):
    ops, dev = "https://hooks.slack.com/ops", "https://hooks.slack.com/dev"
    monkeypatch.setattr(pipeline_watchdog.settings, "SLACK_WEBHOOK_URL", ops)
    monkeypatch.setattr(pipeline_watchdog.settings, "SLACK_WEBHOOK_URL_DEV", dev)
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(pipeline_watchdog.httpx, "AsyncClient", _recording_client(seen, {ops: 404}))
    decision = pipeline_watchdog.AlertDecision(True, "ALERT", "operator", "본문", "x", ops)

    assert await pipeline_watchdog.deliver(decision) is False
    assert [url for url, _ in seen] == [ops]


async def test_deliver_refuses_a_webhook_outside_the_allowlist(monkeypatch):
    monkeypatch.setattr(
        pipeline_watchdog.settings,
        "SLACK_WEBHOOK_ALLOWED_HOSTS",
        "hooks.slack.com",
        raising=False,
    )
    decision = pipeline_watchdog.AlertDecision(
        True, "ALERT", "developer", "본문", "new_condition_set", "https://evil.example.com/x"
    )

    assert await pipeline_watchdog.deliver(decision) is False


def test_watchdog_registers_no_celery_task():
    from app.core.celery_app import celery_app

    assert not [name for name in celery_app.tasks if "watchdog" in name]
    assert "watchdog" not in json.dumps(list(celery_app.conf.beat_schedule))


# ── 리뷰 #236: 당일 발행 히스테리시스·정직한 복구·전달 기록 경합 ──────────────────


def _gate_residual_report(monkeypatch, now):
    """발행기는 돌았지만 오늘 예정 글을 공개 직전 안전검사가 모두 보류한 보고서."""
    report, _ = _install(
        monkeypatch,
        now=now,
        beat_last_run=now - timedelta(minutes=2),
        session_results=(5, 0, now - timedelta(hours=9)),
        block_rows=(_block_row("MISSING_REFERENCES", content_id="a"),),
    )
    assert report.publish_gate_residual and report.critical_conditions == ()
    return report


def test_a_publisher_still_running_at_0830_sends_nothing(monkeypatch, webhooks):
    """08:30에 08:00 발행기가 아직 도는 중이면 한 번 0건으로 보인다 — 알리고 복구하지 않는다."""
    start = datetime(2026, 10, 9, 23, 30, tzinfo=UTC)  # KST 08:30
    missing = _publish_missing_report(monkeypatch, start)
    healthy = _healthy_report(monkeypatch, start)
    beats = Heartbeats(monkeypatch)

    beats.beat(missing, start)
    for step in range(1, 6):
        beats.beat(healthy, start + timedelta(minutes=5 * step))

    assert beats.sent == []


def test_operator_recovery_after_gate_holds_does_not_claim_publication(monkeypatch, webhooks):
    start = datetime(2026, 10, 9, 23, 30, tzinfo=UTC)
    missing = _publish_missing_report(monkeypatch, start)
    residual = _gate_residual_report(monkeypatch, start)
    beats = Heartbeats(monkeypatch)
    beats.beat(missing, start)
    beats.beat(missing, start + timedelta(minutes=5))

    beats.beat(residual, start + timedelta(minutes=10))
    recovery = beats.beat(residual, start + timedelta(minutes=15))

    operator = [d for d in recovery if d.send and d.audience == "operator"]
    assert [d.kind for d in operator] == ["RECOVERY"]
    assert "다시 공개되고 있습니다" not in operator[0].text
    assert "모두 보류됐습니다" in operator[0].text
    assert "콘텐츠 보류 알림" in operator[0].text
    assert "0건" in operator[0].text


def test_operator_recovery_with_publications_still_says_publishing_resumed(monkeypatch, webhooks):
    start = datetime(2026, 10, 9, 23, 30, tzinfo=UTC)
    missing = _publish_missing_report(monkeypatch, start)
    published = replace(
        _healthy_report(monkeypatch, start),
        publish_checked=True,
        publish_due_remaining=2,
        publish_published_today=3,
        publish_partial=True,
    )
    beats = Heartbeats(monkeypatch)
    beats.beat(missing, start)
    beats.beat(missing, start + timedelta(minutes=5))
    beats.beat(published, start + timedelta(minutes=10))
    recovery = beats.beat(published, start + timedelta(minutes=15))

    operator = [d for d in recovery if d.send and d.audience == "operator"]
    assert "다시 공개되고 있습니다" in operator[0].text
    assert "3건" in operator[0].text


def test_a_database_outage_is_not_a_publish_recovery(monkeypatch, webhooks):
    start = datetime(2026, 10, 9, 23, 30, tzinfo=UTC)
    missing = _publish_missing_report(monkeypatch, start)
    unknown = replace(
        missing,
        database_available=False,
        publish_checked=False,
        publish_missing=False,
        publish_due_remaining=None,
        publish_published_today=None,
    )
    beats = Heartbeats(monkeypatch)
    beats.beat(missing, start)
    beats.beat(missing, start + timedelta(minutes=5))
    for step in range(2, 8):
        beats.beat(unknown, start + timedelta(minutes=5 * step))
    beats.beat(missing, start + timedelta(minutes=40))

    assert beats.sent == [("operator", "ALERT", "new_condition_set")]


def test_an_overlapping_heartbeat_cannot_erase_a_recorded_delivery(monkeypatch, webhooks):
    """08:30:00 발행 확인과 08:30:04 하트비트가 겹친다. 뒤 호출이 전달 기록 전에 읽은 에피소드를
    기록 뒤에 저장해도 전달 사실은 남는다 — 15분 뒤 같은 ALERT가 다시 나가지 않는다."""
    start = datetime(2026, 10, 9, 23, 30, tzinfo=UTC)
    report = _publish_missing_report(monkeypatch, start)
    beats = Heartbeats(monkeypatch)
    beats.beat(report, start - timedelta(minutes=5))

    beats.client.clock = start.timestamp()
    first = pipeline_watchdog.decide_alerts(_at(report, start), now=start)
    assert [d.reason for d in first if d.send] == ["new_condition_set"]

    real_load = pipeline_watchdog._load_episode
    raced = []

    def load_then_other_call_records(client, audience):
        stored = real_load(client, audience)
        if audience == "operator" and not raced:
            raced.append(True)
            # 이 호출이 에피소드를 읽은 직후, 앞 호출이 2xx를 받고 전달을 기록한다.
            pipeline_watchdog.record_deliveries(first, tuple(d.send for d in first))
        return stored

    monkeypatch.setattr(pipeline_watchdog, "_load_episode", load_then_other_call_records)
    overlapping = start + timedelta(seconds=4)
    second = pipeline_watchdog.decide_alerts(_at(report, overlapping), now=overlapping)
    monkeypatch.setattr(pipeline_watchdog, "_load_episode", real_load)

    later = [
        beats.beat(report, start + timedelta(minutes=5 * step)) for step in range(1, 12)
    ]

    assert raced and all(not d.send for d in second)
    assert all(not d.send for decisions in later for d in decisions)
