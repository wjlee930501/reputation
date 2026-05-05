import os
import uuid
from datetime import date, datetime
from types import SimpleNamespace

os.environ.setdefault("ADMIN_SECRET_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///tmp/reputation-test.db")
os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///tmp/reputation-test.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

from app.models.content import ContentItem, ContentStatus, ContentType  # noqa: E402
from app.models.essence import (  # noqa: E402
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.services.essence_engine import (  # noqa: E402
    ESSENCE_STATUS_ALIGNED,
    build_monthly_essence_summary,
)
from app.utils.demo_seed import _seed_essence_chain  # noqa: E402


class FakeQueryResult:
    def __init__(self, items):
        self.items = items

    def scalars(self):
        return self

    def all(self):
        return self.items

    def scalar_one_or_none(self):
        return self.items[0] if self.items else None


class FakeSeedDb:
    def __init__(self):
        self.objects = []

    def add(self, obj):
        self.objects.append(obj)

    def add_all(self, objs):
        self.objects.extend(objs)

    def flush(self):
        for obj in self.objects:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    def execute(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is HospitalSourceAsset:
            return FakeQueryResult([obj for obj in self.objects if isinstance(obj, HospitalSourceAsset)])
        if entity is HospitalContentPhilosophy:
            return FakeQueryResult([
                obj
                for obj in self.objects
                if isinstance(obj, HospitalContentPhilosophy) and obj.status == PhilosophyStatus.APPROVED
            ])
        if entity is ContentItem:
            return FakeQueryResult([obj for obj in self.objects if isinstance(obj, ContentItem)])
        return FakeQueryResult([])


def test_demo_seed_builds_source_evidence_and_approved_operating_standard():
    db = FakeSeedDb()
    hospital = SimpleNamespace(
        id=uuid.uuid4(),
        name="장편한외과의원 데모",
        region=["강남구"],
        treatments=[{"name": "탈장 수술"}, {"name": "치질 치료"}],
    )

    philosophy = _seed_essence_chain(db, hospital)

    sources = [obj for obj in db.objects if isinstance(obj, HospitalSourceAsset)]
    notes = [obj for obj in db.objects if isinstance(obj, HospitalSourceEvidenceNote)]
    philosophies = [obj for obj in db.objects if isinstance(obj, HospitalContentPhilosophy)]

    assert len(sources) == 1
    assert sources[0].source_type == SourceType.INTERVIEW
    assert sources[0].status == SourceStatus.PROCESSED
    assert sources[0].raw_text
    assert sources[0].operator_note
    assert notes
    assert all(note.source_asset_id == sources[0].id for note in notes)
    assert philosophy is philosophies[0]
    assert philosophy.status == PhilosophyStatus.APPROVED
    assert philosophy.reviewed_by == "Demo AE"
    assert philosophy.approved_at is not None
    assert philosophy.source_asset_ids == [str(sources[0].id)]
    assert philosophy.evidence_map
    assert any(note_id for mapped in philosophy.evidence_map.values() for note_id in mapped)


def test_demo_seed_monthly_summary_includes_operating_standard_sources_and_content():
    db = FakeSeedDb()
    hospital = SimpleNamespace(
        id=uuid.uuid4(),
        name="장편한외과의원 데모",
        region=["강남구"],
        treatments=[{"name": "탈장 수술"}, {"name": "치질 치료"}],
    )

    philosophy = _seed_essence_chain(db, hospital)
    content = ContentItem(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        content_type=ContentType.FAQ,
        sequence_no=1,
        total_count=16,
        title="강남 탈장 수술 병원 선택 기준",
        body="강남에서 탈장 수술 상담 병원을 고를 때는 설명과 회복 계획을 함께 확인하세요.",
        meta_description="탈장 수술 병원 선택 전 확인할 설명과 회복 계획 기준입니다.",
        scheduled_date=date(2026, 5, 5),
        status=ContentStatus.PUBLISHED,
        content_philosophy_id=philosophy.id,
        essence_status=ESSENCE_STATUS_ALIGNED,
        essence_check_summary={"blocking": False, "philosophy_version": philosophy.version},
    )
    db.add(content)

    summary = build_monthly_essence_summary(
        db,
        hospital,
        period_start=datetime(2026, 5, 1),
        period_end=datetime(2026, 5, 31),
    )

    assert summary["approved_philosophy_exists"] is True
    assert summary["philosophy_version"] == 1
    assert summary["source_count"] == 1
    assert summary["processed_source_count"] == 1
    assert summary["source_asset_ids"] == philosophy.source_asset_ids
    assert summary["source_stale"] is False
    assert summary["generated_content_count"] == 1
    assert summary["aligned_content_count"] == 1
    assert summary["needs_review_content_count"] == 0
    assert summary["missing_philosophy_content_count"] == 0
    assert summary["medical_risk_findings"] == []
    assert summary["recommended_actions"] == []
