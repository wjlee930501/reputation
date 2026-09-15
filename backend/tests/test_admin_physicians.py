"""Admin 의료진 목록 치환 — 사진 연결 검증, upsert, 삭제."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.admin.physicians import PhysicianInput, list_physicians, replace_physicians
from app.models.essence import SourceType
from app.models.physician import HospitalPhysician

HOSPITAL_ID = uuid.uuid4()


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


class FakeDB:
    """statement가 어느 테이블을 읽는지로만 분기한다 — 실제 SQL은 실행하지 않는다."""

    def __init__(self, physicians=None, assets=None):
        self.physicians = list(physicians or [])
        self.assets = list(assets or [])
        self.added = []
        self.deleted = []

    async def execute(self, stmt):
        text = str(stmt)
        if "hospital_source_assets" in text:
            if "hospital_source_assets.title" in text:
                return FakeResult([(asset.id, asset.title) for asset in self.assets])
            doctor_only = "source_type" in text
            return FakeResult(
                [
                    (asset.id,)
                    for asset in self.assets
                    if not doctor_only or asset.source_type == SourceType.PHOTO_DOCTOR
                ]
            )
        return FakeResult(list(self.physicians))

    def add(self, row):
        self.added.append(row)

    async def delete(self, row):
        self.deleted.append(row)


def _asset(source_type=SourceType.PHOTO_DOCTOR, title="원장 사진"):
    return SimpleNamespace(id=uuid.uuid4(), source_type=source_type, title=title)


def _row(**overrides):
    base = {
        "id": uuid.uuid4(),
        "hospital_id": HOSPITAL_ID,
        "name": "김성열",
        "title": "원장",
        "specialties": [],
        "career": None,
        "credentials": None,
        "photo_source_id": None,
        "display_order": 0,
        "is_representative": True,
        "created_at": None,
    }
    base.update(overrides)
    return HospitalPhysician(**base)


async def test_new_rows_are_added_and_missing_rows_are_deleted():
    existing = _row(name="이전원장")
    db = FakeDB(physicians=[existing])

    await replace_physicians(
        db,
        HOSPITAL_ID,
        [PhysicianInput(name="김성열", title="대표원장"), PhysicianInput(name="전상훈")],
    )

    assert [row.name for row in db.added] == ["김성열", "전상훈"]
    assert db.deleted == [existing]
    # 대표 표시가 없으면 첫 행이 대표가 된다.
    assert [row.is_representative for row in db.added] == [True, False]
    assert [row.display_order for row in db.added] == [0, 1]


async def test_existing_row_is_updated_in_place_by_id():
    existing = _row(name="김성열", title="원장")
    db = FakeDB(physicians=[existing])

    await replace_physicians(
        db,
        HOSPITAL_ID,
        [PhysicianInput(id=existing.id, name="김성열", title="대표원장", career="외과 전문의")],
    )

    assert db.added == []
    assert db.deleted == []
    assert existing.title == "대표원장"
    assert existing.career == "외과 전문의"


async def test_photo_from_another_source_type_is_rejected():
    facility = _asset(source_type=SourceType.PHOTO_CLINIC_INTERIOR)
    db = FakeDB(assets=[facility])

    with pytest.raises(HTTPException) as exc:
        await replace_physicians(
            db, HOSPITAL_ID, [PhysicianInput(name="김성열", photo_source_id=facility.id)]
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "PHYSICIAN_PHOTO_INVALID"
    assert db.added == []


async def test_doctor_photo_from_the_same_hospital_is_accepted():
    doctor = _asset()
    db = FakeDB(assets=[doctor])

    await replace_physicians(
        db, HOSPITAL_ID, [PhysicianInput(name="김성열", photo_source_id=doctor.id)]
    )

    assert db.added[0].photo_source_id == doctor.id


async def test_blank_only_list_is_rejected():
    db = FakeDB()

    with pytest.raises(HTTPException) as exc:
        await replace_physicians(db, HOSPITAL_ID, [PhysicianInput(name=" ")])

    assert exc.value.detail["code"] == "PHYSICIAN_NAME_REQUIRED"


async def test_empty_list_deletes_every_row():
    existing = _row()
    db = FakeDB(physicians=[existing])

    assert await replace_physicians(db, HOSPITAL_ID, []) == []
    assert db.deleted == [existing]


async def test_unknown_physician_id_is_rejected_instead_of_silently_inserted():
    """다른 병원·이미 삭제된 행의 id는 새 행으로 만들지 않는다 — 중복 행이 조용히 생긴다."""
    db = FakeDB(physicians=[])
    stale_id = uuid.uuid4()

    with pytest.raises(HTTPException) as exc:
        await replace_physicians(db, HOSPITAL_ID, [PhysicianInput(id=stale_id, name="김성열")])

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "PHYSICIAN_NOT_FOUND"
    assert exc.value.detail["unknown_physician_ids"] == [str(stale_id)]
    assert db.added == []
    assert db.deleted == []


async def test_explicit_display_order_zero_is_kept_for_a_later_row():
    """0은 화면이 정한 순서다 — falsy라는 이유로 목록 index로 덮어쓰지 않는다."""
    db = FakeDB()

    await replace_physicians(
        db,
        HOSPITAL_ID,
        [
            PhysicianInput(name="전상훈", display_order=1),
            PhysicianInput(name="김성열", display_order=0, is_representative=True),
        ],
    )

    assert [(row.name, row.display_order) for row in db.added] == [("전상훈", 1), ("김성열", 0)]


async def test_list_physicians_resolves_the_admin_photo_route():
    doctor = _asset()
    row = _row(photo_source_id=doctor.id)
    db = FakeDB(physicians=[row], assets=[doctor])

    payload = await list_physicians(db, HOSPITAL_ID)

    assert payload[0]["photo_title"] == "원장 사진"
    assert payload[0]["photo_access_url"] == (
        f"/api/admin/hospitals/{HOSPITAL_ID}/essence/sources/{doctor.id}/file"
    )
