import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.api.admin.reports import _serialize
from app.schemas.report import ReportReviewEvidence
from app.services.report_review_evidence import build_report_review_evidence


class _Result:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values

    def scalar_one_or_none(self):
        return self._values[0] if self._values else None


class _DB:
    def __init__(self, runs=(), notifications=()):
        self.runs = list(runs)
        self.notifications = list(notifications)
        self.calls = 0
        self.statements = []

    async def execute(self, statement):
        self.calls += 1
        self.statements.append(statement)
        return _Result(self.runs if self.calls == 1 else self.notifications)


class _FilteringRunDB(_DB):
    """Emulate the JSON predicates while retaining non-matching source rows."""

    def __init__(self, report, runs=(), notifications=()):
        super().__init__(runs, notifications)
        self.report = report

    async def execute(self, statement):
        self.calls += 1
        self.statements.append(statement)
        if self.calls != 1:
            return _Result(self.notifications)
        report_id = str(self.report.id)
        matching = [
            run
            for run in self.runs
            if isinstance(run.result_summary, dict)
            and run.result_summary.get("report_id") == report_id
            and run.result_summary.get("period_year") == self.report.period_year
            and run.result_summary.get("period_month") == self.report.period_month
        ]
        return _Result(matching[:1])


def _report(**overrides):
    base = {
        "id": uuid.uuid4(),
        "hospital_id": uuid.uuid4(),
        "period_year": 2026,
        "period_month": 7,
        "version": 2,
        "supersedes_report_id": uuid.uuid4(),
        "quality": "COMPLETE",
        "planned_count": 8,
        "success_count": 8,
        "failed_count": 0,
        "excluded_count": 2,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _run(report):
    return SimpleNamespace(
        id=uuid.uuid4(),
        result_summary={
            "report_id": str(report.id),
            "period_year": report.period_year,
            "period_month": report.period_month,
        },
    )


@pytest.mark.parametrize(
    ("quality", "label", "action_word"),
    [
        ("COMPLETE", "필수 측정 완료", "확인"),
        ("DEGRADED", "일부 측정 미완료", "운영 센터"),
        ("BLOCKED", "필수 측정 차단", "운영 센터"),
        ("UNKNOWN", "측정 기준 확인 필요", "최신 리포트"),
    ],
)
async def test_review_evidence_translates_measurement_quality(quality, label, action_word):
    evidence = await build_report_review_evidence(_DB(), _report(quality=quality))

    assert evidence["measurement"]["quality_label"] == label
    assert action_word in evidence["measurement"]["next_action"]
    assert "SLA" not in json.dumps(evidence, ensure_ascii=False)


async def test_review_evidence_exposes_only_exactly_linked_notification_state():
    report = _report()
    run = _run(report)
    notification = SimpleNamespace(
        notification_type="INCIDENT_RECOVERED",
        channel="SLACK",
        state="SENT",
        sent_at=datetime(2026, 8, 10, 3, 0, tzinfo=timezone.utc),
        payload={"secret": "must-not-leak"},
        task_id="must-not-leak",
    )

    evidence = await build_report_review_evidence(_DB([run], [notification]), report)
    validated = ReportReviewEvidence.model_validate(evidence).model_dump(mode="json")

    assert validated["notification"]["state"] == "SENT"
    assert validated["notification"]["state_label"] == "운영팀 알림 전달 완료"
    assert validated["version_label"] == "새 버전 2 · 이전 리포트 보존"
    serialized = json.dumps(validated, ensure_ascii=False)
    assert "secret" not in serialized
    assert "task_id" not in serialized
    assert "payload" not in serialized
    assert "path" not in serialized


async def test_newer_wrong_period_run_is_skipped_for_exact_older_report_run():
    report = _report()
    wrong = SimpleNamespace(
        id=uuid.uuid4(),
        result_summary={
            "report_id": str(uuid.uuid4()),
            "period_year": report.period_year,
            "period_month": report.period_month + 1,
        },
    )
    exact = _run(report)
    notification = SimpleNamespace(
        notification_type="INCIDENT_OPEN",
        channel="SLACK",
        state="FAILED",
        sent_at=None,
    )

    # 데이터베이스 JSON 연결 조건이 newer wrong row를 제외하고 exact row만 반환한다.
    db = _FilteringRunDB(report, [wrong, exact], [notification])
    evidence = await build_report_review_evidence(db, report)

    assert evidence["notification"]["state"] == "FAILED"
    assert evidence["notification"]["state_label"] == "운영팀 알림 확인 필요"
    compiled = db.statements[0].compile()
    assert str(report.id) in compiled.params.values()
    assert report.period_year in compiled.params.values()
    assert report.period_month in compiled.params.values()
    assert wrong.result_summary["report_id"] not in compiled.params.values()


async def test_arbitrary_run_linked_notification_is_not_report_slack_evidence():
    report = _report()
    unrelated = SimpleNamespace(
        notification_type="CONTENT_PUBLISHED",
        channel="SLACK",
        state="SENT",
        sent_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )

    evidence = await build_report_review_evidence(_DB([_run(report)], [unrelated]), report)

    assert evidence["notification"]["state"] == "NOT_INDIVIDUALLY_LINKED"


async def test_linked_email_is_never_presented_as_slack_delivery_evidence():
    report = _report()
    email = SimpleNamespace(
        notification_type="INCIDENT_OPEN",
        channel="EMAIL",
        state="SENT",
        sent_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )

    evidence = await build_report_review_evidence(_DB([_run(report)], [email]), report)

    assert evidence["notification"]["state"] == "NOT_INDIVIDUALLY_LINKED"


async def test_unlinked_summary_is_never_claimed_as_this_report_notification():
    evidence = await build_report_review_evidence(_DB(), _report())

    notification = evidence["notification"]
    assert notification["state"] == "NOT_INDIVIDUALLY_LINKED"
    assert notification["problem"] == (
        "여러 병원을 묶은 요약 알림이라 이 리포트와 개별 연결 기록이 없습니다."
    )
    assert "단정할 수 없습니다" in notification["customer_impact"]
    assert notification["operations_url"].startswith("/operations?queue=REPORTS")


async def test_review_evidence_is_detail_only_and_schema_closed():
    report = _report(
        report_type="MONTHLY",
        pdf_path=None,
        doctor_pdf_path=None,
        sov_summary=None,
        content_summary=None,
        essence_summary=None,
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        sent_at=None,
        manifest_id=None,
        customer_ready=False,
        delivery_blockers=[],
    )
    evidence = await build_report_review_evidence(_DB(), report)

    listing = _serialize(report)
    detail = _serialize(report, full=True, review_evidence=evidence)

    assert "review_evidence" not in listing
    assert detail["review_evidence"]["version"] == 2
    assert set(ReportReviewEvidence.model_json_schema()["properties"]) == {
        "version",
        "version_label",
        "supersedes_report_id",
        "measurement",
        "notification",
    }
