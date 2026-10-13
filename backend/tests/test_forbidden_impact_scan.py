"""배포 전 영향 조회: 새 필터가 숨길 공개 글·병원 문구를 읽기 전용으로 찾는다(2026-10-13 D24)."""

from __future__ import annotations

import io
import json
import uuid
from types import SimpleNamespace

from app.utils import forbidden_impact_scan


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return iter(self._rows)


class _FakeSession:
    def __init__(self, items, hospitals, philosophies):
        self.batches = [items, hospitals, philosophies]
        self.events: list[str] = []
        self.commits = 0

    def rollback(self):
        self.events.append("ROLLBACK")

    def commit(self):
        self.commits += 1

    def execute(self, statement):
        self.events.append(str(statement).split()[0])
        if str(statement).startswith("SET TRANSACTION"):
            return None
        return _Rows(self.batches.pop(0))


def _published(body: str):
    # active_revision_id가 없는 옛 모양이면 approved_public_view가 행 그대로를 본다.
    return SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        title="허리 통증 안내",
        body=body,
        meta_description="안내",
        faq_question=None,
        faq_answer_summary=None,
        references_list=[],
    )


def test_scan_reports_only_flagged_public_text_and_never_writes():
    flagged = _published("## 안내\n개원 이벤트로 무료 상담을 드립니다.")
    clean = _published("## 안내\n허리 통증은 원인이 다양합니다.")
    hospital = SimpleNamespace(
        id=uuid.uuid4(),
        hero_headline="허리 통증 전문",
        hero_description="첫 방문 무료 상담",
        treatments=["도수치료", {"name": "체외충격파", "description": "선착순 할인"}],
    )
    philosophy = SimpleNamespace(
        id=uuid.uuid4(), hospital_id=hospital.id,
        positioning_statement="과잉진료를 피합니다.", patient_promise="",
        must_use_messages=["정확한 진단은 전문의 진료로 확인합니다.", "첫 진료 무료 상담을 드립니다."],
    )
    db = _FakeSession([flagged, clean], [hospital], [philosophy])
    out = io.StringIO()

    labels = forbidden_impact_scan.scan(db, out=out)

    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [line.get("kind") for line in lines[:-1]] == [
        "content", "hospital", "hospital", "philosophy"
    ]
    assert lines[0]["id"] == str(flagged.id)
    assert [line.get("field") for line in lines[1:4]] == [
        "hero_description", "treatments[1]", "must_use_messages[1]"
    ]
    assert {line["change"] for line in lines[:-1]} == {"newly_blocked"}
    assert all(line["baseline_labels"] == [] for line in lines[:-1])
    assert lines[-1] == {
        "summary": {"할인·유인": 4},
        "changes": {"newly_blocked": 4},
        "baseline": "7a1e2174",
    }
    assert labels["할인·유인"] == 4
    assert db.events[:2] == ["ROLLBACK", "SET"]
    assert db.events[-1] == "ROLLBACK"
    assert db.commits == 0


def test_scan_also_reports_posts_the_production_filter_hid_that_now_reappear():
    """문맥 예외가 넓어져 다시 보이게 되는 글도 배포 전에 보인다(교차 검토 2026-10-13)."""

    reappears = _published("## 안내\n수술 후 완전 회복까지 3개월이 걸립니다.")
    still = _published("## 안내\n최고의 의료진이 진료합니다.")
    db = _FakeSession([reappears, still], [], [])
    out = io.StringIO()

    forbidden_impact_scan.scan(db, out=out)

    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert lines[0] == {
        "kind": "content", "id": str(reappears.id), "hospital_id": str(reappears.hospital_id),
        "labels": [], "baseline_labels": ["완치"], "change": "newly_unblocked",
    }
    assert lines[1]["change"] == "still_blocked"
    assert lines[-1]["changes"] == {"newly_unblocked": 1, "still_blocked": 1}
    assert lines[-1]["summary"] == {"최고": 1}


def test_the_baseline_filter_is_the_frozen_production_copy():
    from app.utils import medical_filter_baseline

    assert medical_filter_baseline.BASELINE_COMMIT == "7a1e2174"
    # 동결본은 Phase 3 이전 판정 그대로다 — 새 예외·새 라벨이 없다.
    assert "할인·유인" not in medical_filter_baseline.FORBIDDEN_PATTERNS
    assert medical_filter_baseline.check_forbidden("수술 후 완전 회복까지 3개월이 걸립니다.") == ["완치"]
