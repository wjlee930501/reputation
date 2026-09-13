"""외부 파이프라인 감시 — Celery 없이 생존을 판정하고 한 건만 알린다."""

import json
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

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def _guard(self):
        if self.broken:
            raise RedisError("redis down")

    def exists(self, key):
        self._guard()
        return 1 if key in self.values or key in self.hashes else 0

    def hget(self, key, field):
        self._guard()
        return self.hashes.get(key, {}).get(field)

    def get(self, key):
        self._guard()
        return self.values.get(key)

    def set(self, key, value, nx=False, ex=None):
        self._guard()
        if nx and key in self.values:
            return None
        self.values[key] = value.encode("utf-8") if isinstance(value, str) else value
        return True

    def delete(self, key):
        self._guard()
        self.deleted.append(key)
        self.values.pop(key, None)
        return 1


class FakeSession:
    """`scalar` 호출 순서대로 값을 돌려주는 최소 세션(발행 예정 → 공개 → 배치 시각)."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def scalar(self, _stmt):
        self.calls += 1
        return self.results.pop(0)


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
    report = pipeline_watchdog.evaluate(FakeSession(list(session_results)), now=now)
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


def test_each_audience_is_alerted_once_per_hour_and_recovers_once(monkeypatch):
    monkeypatch.setattr(pipeline_watchdog.settings, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/ops")
    monkeypatch.setattr(
        pipeline_watchdog.settings, "SLACK_WEBHOOK_URL_DEV", "https://hooks.slack.com/dev"
    )
    now = datetime(2026, 9, 12, 23, 40, tzinfo=UTC)
    client = FakeRedis()
    broken = _broken_report(monkeypatch, now)
    monkeypatch.setattr(pipeline_watchdog, "_connect_redis", lambda: client)

    first = pipeline_watchdog.decide_alerts(broken, now=now)
    second = pipeline_watchdog.decide_alerts(broken, now=now + timedelta(minutes=5))
    next_hour = pipeline_watchdog.decide_alerts(broken, now=now + timedelta(hours=1))

    assert [(d.audience, d.send, d.kind, d.webhook_url) for d in first] == [
        ("developer", True, "ALERT", "https://hooks.slack.com/dev"),
        ("operator", True, "ALERT", "https://hooks.slack.com/ops"),
    ]
    assert [(d.send, d.reason) for d in second] == [(False, "deduped"), (False, "deduped")]
    assert [d.send for d in next_hour] == [True, True]

    later = now + timedelta(hours=2)
    healthy, _ = _install(
        monkeypatch,
        now=later,
        beat_last_run=later - timedelta(minutes=2),
        session_results=(0, 9, now - timedelta(hours=3)),
    )
    monkeypatch.setattr(pipeline_watchdog, "_connect_redis", lambda: client)
    recovery = pipeline_watchdog.decide_alerts(healthy, now=later)
    repeat = pipeline_watchdog.decide_alerts(healthy, now=later + timedelta(minutes=5))

    assert [(d.audience, d.kind) for d in recovery] == [
        ("developer", "RECOVERY"),
        ("operator", "RECOVERY"),
    ]
    assert all("정상으로 돌아왔습니다" in d.text or "다시 공개되고 있습니다" in d.text for d in recovery)
    assert all("확인 완료" not in d.text for d in recovery)
    assert [(d.send, d.reason) for d in repeat] == [(False, "healthy"), (False, "healthy")]


def test_infra_only_failure_does_not_alert_the_operations_channel(monkeypatch):
    now = _healthy_now()
    client = FakeRedis()
    report, _ = _install(
        monkeypatch,
        now=now,
        stale_queues=("content",),
        lock_held=False,
        session_results=(0, 12, now - timedelta(hours=10)),
    )
    monkeypatch.setattr(pipeline_watchdog, "_connect_redis", lambda: client)

    developer, operator = pipeline_watchdog.decide_alerts(report, now=now)

    assert developer.send is True and developer.audience == "developer"
    assert operator.send is False and operator.reason == "healthy"


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


def test_dedupe_fails_open_when_redis_is_down(monkeypatch):
    now = datetime(2026, 9, 12, 23, 40, tzinfo=UTC)
    broken = _broken_report(monkeypatch, now)
    monkeypatch.setattr(pipeline_watchdog, "_connect_redis", lambda: FakeRedis(broken=True))

    decisions = pipeline_watchdog.decide_alerts(broken, now=now)

    assert [(d.send, d.reason) for d in decisions] == [
        (True, "redis_unavailable"),
        (True, "redis_unavailable"),
    ]


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
