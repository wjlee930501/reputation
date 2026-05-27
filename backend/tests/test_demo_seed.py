import os
import uuid
from datetime import date, datetime
from types import SimpleNamespace

os.environ.setdefault("ADMIN_SECRET_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///tmp/reputation-test.db")
os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///tmp/reputation-test.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType  # noqa: E402
from app.models.essence import (  # noqa: E402
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.report import MonthlyReport  # noqa: E402
from app.models.sov import AIQueryVariant, QueryMatrix  # noqa: E402
from app.services.content_brief import BRIEF_STATUS_APPROVED  # noqa: E402
from app.services.essence_engine import (  # noqa: E402
    ESSENCE_STATUS_ALIGNED,
    build_monthly_essence_summary,
)
from app.utils.demo_seed import (  # noqa: E402
    DEMO_CONTENT_COUNT,
    DEMO_MIN_GENERATED_COUNT,
    _create_hospital,
    _run_generation_pipeline,
    _seed_content_slots,
    _seed_essence_chain,
    _seed_query_targets,
)


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
        if entity is MonthlyReport:
            return FakeQueryResult([obj for obj in self.objects if isinstance(obj, MonthlyReport)])
        return FakeQueryResult([])


def test_demo_seed_builds_source_evidence_and_approved_operating_standard():
    db = FakeSeedDb()
    hospital = SimpleNamespace(
        id=uuid.uuid4(),
        name="모션랩스정형외과의원",
        region=["성동구", "성수동"],
        treatments=[{"name": "무릎 통증 진료"}, {"name": "어깨 통증 진료"}],
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
        name="모션랩스정형외과의원",
        region=["성동구", "성수동"],
        treatments=[{"name": "무릎 통증 진료"}, {"name": "어깨 통증 진료"}],
    )

    philosophy = _seed_essence_chain(db, hospital)
    content = ContentItem(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        content_type=ContentType.FAQ,
        sequence_no=1,
        total_count=16,
        title="성수동 무릎 통증 병원 선택 기준",
        body="성수동에서 무릎 통증 상담 병원을 고를 때는 설명과 회복 계획을 함께 확인하세요.",
        meta_description="무릎 통증 병원 선택 전 확인할 설명과 회복 계획 기준입니다.",
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


def test_demo_seed_creates_pipeline_ready_slots_and_approved_briefs():
    db = FakeSeedDb()
    hospital = _create_hospital()
    hospital.id = uuid.uuid4()

    philosophy = _seed_essence_chain(db, hospital)
    query_targets = _seed_query_targets(db, hospital)
    slots = _seed_content_slots(db, hospital, philosophy, query_targets, "/api/v1/public/demo-asset")

    query_rows = [obj for obj in db.objects if isinstance(obj, QueryMatrix)]
    query_variants = [obj for obj in db.objects if isinstance(obj, AIQueryVariant)]
    schedules = [obj for obj in db.objects if isinstance(obj, ContentSchedule)]

    assert len(query_targets) == 12
    assert len(query_rows) == 12
    assert len(query_variants) == 12
    assert len(schedules) == 1
    assert len(slots) == DEMO_CONTENT_COUNT
    assert all(item.status == ContentStatus.DRAFT for item in slots)
    assert all(item.body is None for item in slots)
    assert all(item.query_target_id for item in slots)
    assert all(item.content_brief for item in slots)
    assert all(item.brief_status == BRIEF_STATUS_APPROVED for item in slots)


class FakePipelineDb(FakeSeedDb):
    def __init__(self, hospital, items):
        super().__init__()
        self.hospital = hospital
        self.objects = list(items)
        self.commits = 0

    def get(self, model, obj_id):
        return self.hospital if obj_id == self.hospital.id else None

    def refresh(self, obj):
        return None

    def commit(self):
        self.commits += 1


def test_demo_generation_pipeline_continues_after_single_item_failures(monkeypatch):
    from app.workers import tasks

    hospital = _create_hospital()
    hospital.id = uuid.uuid4()
    philosophy_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    items = [
        ContentItem(
            id=uuid.uuid4(),
            hospital_id=hospital.id,
            schedule_id=schedule_id,
            content_type=ContentType.FAQ,
            sequence_no=index,
            total_count=DEMO_CONTENT_COUNT,
            scheduled_date=date(2026, 6, index),
            status=ContentStatus.DRAFT,
            content_philosophy_id=philosophy_id,
        )
        for index in range(1, DEMO_CONTENT_COUNT + 1)
    ]
    db = FakePipelineDb(hospital, items)

    def fake_generate(_db, item, _hospital):
        if item.sequence_no in {1, 2}:
            raise RuntimeError("temporary model failure")
        item.title = f"pipeline title {item.sequence_no}"
        item.body = f"pipeline body {item.sequence_no}"
        item.meta_description = "pipeline meta"
        item.essence_status = ESSENCE_STATUS_ALIGNED

    monkeypatch.setattr(tasks, "_generate_single_content_item", fake_generate)

    generated_count, published_count = _run_generation_pipeline(
        db,
        hospital_id=hospital.id,
        publish=True,
        fallback_image_url="/api/v1/public/demo-asset",
    )

    assert generated_count == DEMO_CONTENT_COUNT - 2
    assert published_count == DEMO_CONTENT_COUNT - 2
    assert generated_count >= DEMO_MIN_GENERATED_COUNT
    assert all(item.status == ContentStatus.PUBLISHED for item in items[2:])
    assert all(item.status == ContentStatus.DRAFT for item in items[:2])
    assert all(item.image_url == "/api/v1/public/demo-asset" for item in items[2:])
