# PR-0B 콘텐츠 운영 기준·근거 무결성 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 운영자가 근거 노트를 노이즈로 제외하면 승인이 stale이 되어 자동 재검수가 돌고(단, 기존 공개 글은 유지), 수동 승인이 자동 검수 결과를 우회하지 못하며, 자동화를 영구히 꺼버리는 "수동 초안" 경로와 승인을 무한 차단하는 URL 전용 자료 문제를 없앤다 (검토 등록부 H-02, H-03, H-04, M-01, M-03).

**Architecture:** `hospital_content_philosophies`에 `evidence_noise_hash`(승인 시점의 노이즈 제외 노트 집합 hash) 컬럼을 추가한다. 엄격한 `current` 판정은 `source_snapshot_hash`와 `evidence_noise_hash`를 **둘 다** 요구하고, 기존 공개 글의 근거인 `public_philosophy`는 `source_snapshot_hash`만 본다 — 그래서 노트 하나를 숨겨도 병원 공개 글이 사라지지 않고 새 생성만 재승인을 기다린다. 노이즈 관련 predicate·hash는 새 모듈 `app/services/evidence_noise.py` 한 곳에 둔다. "필수 텍스트 자료"의 정의(원문이 있는 비사진·비제외 자료)도 `essence_readiness.py`의 predicate 하나로 통일한다. 수동 초안 생성 엔드포인트는 삭제하고, ESCALATED 초안에는 `archive`와 `re-review`(보관 후 자동 검수 재실행)만 남긴다.

**Tech Stack:** FastAPI + SQLAlchemy async/sync, Alembic, pytest (SimpleNamespace fakes + 실 PostgreSQL 통합 픽스처 `pg_conn`/`pg_async_session`), Next.js 16 admin + `node:test`. 근거 문서: [설계](2026-09-08-admin-hitl-simplification-design.md) §3 PR-0B, [검토](../reviews/2026-09-08-integrity-hitl-review.md) §2.

**이 PR에서 다루지 않음(후속 PR-0B-2):** M-02(24k 청크 노트 상한), M-04(비ACTIVE ESCALATED 인시던트). 코드 근거를 더 확인한 뒤 별도 계획으로 쓴다.

---

## 환경 준비

PR-0A 계획의 "환경 준비"와 동일하다. 백엔드 테스트 명령(`backend/`에서):

```bash
cd /Users/woojinlee/Documents/projects/reputation/backend && export APP_ENV=test ADMIN_SECRET_KEY=test-admin-key DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/reputation_test" SYNC_DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/reputation_test" INTEGRATION_DATABASE_URL="postgresql://postgres:postgres@localhost:5432/reputation_test" TASK22_DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/reputation_test" INCIDENT_TEST_DATABASE_URL="postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test" OPERATIONS_TEST_DATABASE_URL="postgresql+psycopg2://reputation:reputation@localhost:5434/reputation_test" MIGRATION_UPGRADE_DATABASE_URL="postgresql+psycopg2://reputation:reputation@localhost:5434/reputation_autonomy_migration" COST_GUARD_REDIS_URL="redis://localhost:6379/2" INTEGRATION_REDIS_URL="redis://localhost:6379/3" REQUIRE_PDF_RENDER=1 DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib && .venv/bin/python -m pytest -q -p no:cacheprovider
```

통합 테스트(`tests/integration/`)는 `INTEGRATION_DATABASE_URL`이 가리키는 5432 DB가 **마이그레이션 head**여야 한다. Task 1 뒤에는 반드시 두 DB 모두 `alembic upgrade head`를 다시 실행한다(아래 Task 1 Step 4).

## 파일 구조

| 파일 | 책임 | 변경 |
|---|---|---|
| `backend/alembic/versions/0070_essence_evidence_noise_hash.py` (신규) | 컬럼 추가 | 신규 |
| `backend/app/models/essence.py` | `HospitalContentPhilosophy.evidence_noise_hash` | 1 필드 |
| `backend/app/services/evidence_noise.py` (신규) | 노이즈 노트 predicate·hash·로더 (async/sync) | 신규, 단일 책임 |
| `backend/app/services/essence_readiness.py` | `current` 이중 판정, 필수 텍스트 자료 predicate, 로더가 노이즈 hash 전달 | 수정 |
| `backend/app/services/essence_auto_review.py` | 노이즈 노트 제외, hash 비교·저장 | 수정 |
| `backend/app/api/admin/essence.py` | 수동 초안 삭제, archive/re-review 추가, 승인 게이트, hash 저장, 필수 자료 predicate 재사용 | 수정 |
| `backend/app/schemas/essence.py` | `PhilosophyDraftCreate` 삭제, `PhilosophyApprove.override_reason` | 수정 |
| `admin/app/hospitals/[id]/essence/page.tsx`, `admin/lib/admin-proxy.ts` | 수동 초안 UI 제거, 재검수/보관 버튼, 예외 사유 입력, 재처리 no-op 문구 | 수정 |
| 테스트 | `tests/test_essence_readiness.py`, `tests/integration/test_essence_auto_review_postgres.py`, `tests/integration/test_essence_approve_grounding.py`, `tests/integration/test_essence_required_sources_postgres.py`(신규), `tests/test_essence_routes.py`(신규), admin `lib/admin-proxy.test.ts`, `lib/api.test.ts` | |

---

### Task 1: `evidence_noise_hash` 컬럼과 노이즈 모듈 (H-02 기반)

**Files:**
- Create: `backend/alembic/versions/0070_essence_evidence_noise_hash.py`
- Create: `backend/app/services/evidence_noise.py`
- Modify: `backend/app/models/essence.py` (`HospitalContentPhilosophy`, `source_snapshot_hash` 바로 아래)
- Modify: `backend/app/api/admin/essence.py:1903-1907` (`_not_noise_predicate`를 새 모듈 재사용)
- Test: `backend/tests/test_evidence_noise.py` (신규)

- [ ] **Step 1: hash 함수 테스트 작성**

`backend/tests/test_evidence_noise.py`:

```python
import uuid
from types import SimpleNamespace

from app.services.evidence_noise import (
    compute_evidence_noise_hash,
    is_noise_note,
)


def test_noise_hash_is_order_independent_and_distinguishes_sets():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert compute_evidence_noise_hash([a, b]) == compute_evidence_noise_hash([str(b), a])
    assert compute_evidence_noise_hash([a]) != compute_evidence_noise_hash([a, b])
    assert compute_evidence_noise_hash([]) == compute_evidence_noise_hash(())


def test_is_noise_note_reads_only_an_explicit_true_flag():
    assert is_noise_note(SimpleNamespace(note_metadata={"is_noise": True})) is True
    assert is_noise_note(SimpleNamespace(note_metadata={"is_noise": False})) is False
    assert is_noise_note(SimpleNamespace(note_metadata={})) is False
    assert is_noise_note(SimpleNamespace(note_metadata=None)) is False
```

- [ ] **Step 2: 실패 확인**

Run: (환경 준비 명령) `tests/test_evidence_noise.py -v`
Expected: `ModuleNotFoundError: app.services.evidence_noise`.

- [ ] **Step 3: 모듈·모델·마이그레이션 구현**

`backend/app/services/evidence_noise.py`:

```python
"""운영자가 노이즈로 제외한 근거 노트 — predicate·identity·로더를 한 곳에서.

제외 표시는 노트를 지우지 않고 `note_metadata["is_noise"]`에 남긴다(제외 해제가 재처리 없이
돌아가야 하므로). 그런데 어떤 노트를 근거에서 뺐는지는 승인의 일부다: 운영자가 뺀 주장으로
계속 생성하면 안 된다. 그래서 승인 시점의 제외 집합 hash를 `evidence_noise_hash`로 남기고,
엄격한 `current` 판정이 그 hash를 다시 비교한다. 자료 snapshot hash와 분리하는 이유는
기존 공개 글의 근거(`public_philosophy`)까지 무효화하지 않기 위해서다.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import false, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.essence import HospitalSourceEvidenceNote


def is_noise_note(note: Any) -> bool:
    metadata = getattr(note, "note_metadata", None) or {}
    return metadata.get("is_noise") is True


def noise_note_predicate():
    return func.coalesce(
        HospitalSourceEvidenceNote.note_metadata["is_noise"].as_boolean(), false()
    ).is_(True)


def not_noise_note_predicate():
    return func.coalesce(
        HospitalSourceEvidenceNote.note_metadata["is_noise"].as_boolean(), false()
    ).is_(False)


def compute_evidence_noise_hash(excluded_note_ids: Iterable[uuid.UUID | str]) -> str:
    """제외된 노트 id 집합의 identity. 순서 무관, 빈 집합도 고정 hash."""
    parts = sorted(str(note_id) for note_id in excluded_note_ids)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _excluded_note_ids_stmt(hospital_id: uuid.UUID):
    """필수 텍스트 자료(비제외·비사진)에 속한 노이즈 노트만 — readiness의 자료 집합과 같은 경계.

    제외된 자료나 사진 자료의 노트를 세면, readiness가 보지 않는 자료의 노트 토글만으로
    승인이 stale이 된다(Codex 검토 지적). Task 6의 `required_text_source_predicate`로 교체 예정.
    """
    return (
        select(HospitalSourceEvidenceNote.id)
        .join(HospitalSourceAsset, HospitalSourceAsset.id == HospitalSourceEvidenceNote.source_asset_id)
        .where(
            HospitalSourceEvidenceNote.hospital_id == hospital_id,
            HospitalSourceAsset.status != SourceStatus.EXCLUDED,
            HospitalSourceAsset.source_type.notin_(list(PHOTO_SOURCE_TYPES)),
            noise_note_predicate(),
        )
    )


async def load_evidence_noise_hash(db: AsyncSession, hospital_id: uuid.UUID) -> str:
    result = await db.execute(_excluded_note_ids_stmt(hospital_id))
    return compute_evidence_noise_hash(result.scalars().all())


def load_evidence_noise_hash_sync(db: Session, hospital_id: uuid.UUID) -> str:
    return compute_evidence_noise_hash(db.execute(_excluded_note_ids_stmt(hospital_id)).scalars().all())
```

`backend/app/models/essence.py`의 `HospitalContentPhilosophy`에서 `source_snapshot_hash` 줄 바로 아래에:

```python
    # 승인 시점에 노이즈로 제외돼 있던 근거 노트 집합의 hash. NULL은 이 컬럼 이전 승인.
    evidence_noise_hash: Mapped[str | None] = mapped_column(String(64))
```

`backend/alembic/versions/0070_essence_evidence_noise_hash.py`:

```python
"""Record which evidence notes were excluded as noise when an Essence was approved.

Revision ID: 0070_essence_evidence_noise_hash
Revises: 0069_content_first_publication
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0070_essence_evidence_noise_hash"
down_revision: str | None = "0069_content_first_publication"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NULL for approvals made before this column: readiness treats them as matching any
    # noise state (legacy), and the next automatic refresh writes a real value.
    op.add_column(
        "hospital_content_philosophies",
        sa.Column("evidence_noise_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("hospital_content_philosophies", "evidence_noise_hash")
```

`backend/app/api/admin/essence.py`의 `_not_noise_predicate` 본문을 새 모듈 위임으로 교체(호출자는 그대로):

```python
from app.services.evidence_noise import not_noise_note_predicate


def _not_noise_predicate():
    return not_noise_note_predicate()
```

- [ ] **Step 4: 마이그레이션 적용·통과 확인**

Run (환경 준비 env를 export한 셸에서):
```bash
cd /Users/woojinlee/Documents/projects/reputation/backend && .venv/bin/python -m alembic upgrade head && DATABASE_URL="postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test" SYNC_DATABASE_URL="postgresql+psycopg2://reputation:reputation@localhost:5434/reputation_test" .venv/bin/python -m alembic upgrade head && DATABASE_URL="postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_autonomy_migration" SYNC_DATABASE_URL="postgresql+psycopg2://reputation:reputation@localhost:5434/reputation_autonomy_migration" .venv/bin/python -m alembic upgrade head
```
Expected: 세 DB 모두 `Running upgrade 0069_content_first_publication -> 0070_essence_evidence_noise_hash`.
Run: (환경 준비 명령) `tests/test_evidence_noise.py tests/test_essence_noise.py -v` → 전부 PASS.

- [ ] **Step 5: 커밋**

```bash
cd /Users/woojinlee/Documents/projects/reputation && git add backend/alembic/versions/0070_essence_evidence_noise_hash.py backend/app/services/evidence_noise.py backend/app/models/essence.py backend/app/api/admin/essence.py backend/tests/test_evidence_noise.py && git commit -m "feat: record the excluded-evidence set on approved Essence

Adds evidence_noise_hash and a single evidence_noise module for the noise
predicate, hash and loaders. No behavior change yet. (H-02 groundwork)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: 엄격한 `current`는 노이즈 hash도 요구하고, 공개 근거는 유지한다 (H-02)

> **실행 기록 (2026-09-08):** 구현 `eb10224` → Codex REQUEST_CHANGES → 후속 커밋에서 ① 공개 사이트 라우트(`site.py:248,315,344`)와 공개 이미지 경로는 노이즈 집합을 조회하지 않는 **`get_public_essence_readiness`**/`include_noise=False`를 쓴다(`public_philosophy`만 필요, 요청당 쿼리 1개 절약) ② 레거시 재검수 backfill의 CAS 체크포인트(`content_public_review_backfill.py:524`)도 sync 노이즈 hash를 넘긴다 ③ 리포트 전달 차단 문구를 "운영 기준 재검수 후 확인"으로 ④ readiness 테스트 fake를 statement 기반 dispatch로. **Task 3가 함께 들어가야** 노이즈 토글로 생긴 stale이 자동으로 회복된다(단독 배포 금지).

**Files:**
- Modify: `backend/app/services/essence_readiness.py` — `resolve_essence_readiness`(59-96), `get_essence_readiness`(98-116), `get_essence_readiness_sync`(119-137), `_get_lightweight_essence_readiness`(184-231)
- Test: `backend/tests/test_essence_readiness.py`

- [ ] **Step 1: 테스트 작성**

`backend/tests/test_essence_readiness.py`에 추가 (`_source`, `compute_sources_snapshot_hash`는 파일에 있음):

```python
from app.services.evidence_noise import compute_evidence_noise_hash


def test_excluding_a_note_makes_strict_current_stale_but_keeps_public_baseline():
    """H-02: 운영자가 뺀 주장으로 새 글을 만들면 안 되지만, 기존 공개 글의 근거는 그대로다."""
    source = _source()
    approved = SimpleNamespace(
        source_snapshot_hash=compute_sources_snapshot_hash([source]),
        evidence_noise_hash=compute_evidence_noise_hash([]),
        source_asset_ids=[str(source.id)],
    )

    readiness = resolve_essence_readiness(
        approved, [source], excluded_note_hash=compute_evidence_noise_hash([uuid.uuid4()])
    )

    assert readiness.current is None
    assert readiness.public_philosophy is approved
    assert readiness.is_stale is True


def test_matching_noise_hash_keeps_current():
    source = _source()
    noise_hash = compute_evidence_noise_hash([uuid.uuid4()])
    approved = SimpleNamespace(
        source_snapshot_hash=compute_sources_snapshot_hash([source]),
        evidence_noise_hash=noise_hash,
        source_asset_ids=[str(source.id)],
    )

    readiness = resolve_essence_readiness(approved, [source], excluded_note_hash=noise_hash)

    assert readiness.current is approved


def test_legacy_approval_without_noise_hash_is_not_stale_by_noise():
    """컬럼 이전 승인(NULL)은 노이즈 상태로 stale이 되지 않는다 — 다음 자동 갱신이 값을 쓴다."""
    source = _source()
    approved = SimpleNamespace(
        source_snapshot_hash=compute_sources_snapshot_hash([source]),
        evidence_noise_hash=None,
        source_asset_ids=[str(source.id)],
    )

    readiness = resolve_essence_readiness(
        approved, [source], excluded_note_hash=compute_evidence_noise_hash([uuid.uuid4()])
    )

    assert readiness.current is approved
```

- [ ] **Step 2: 실패 확인**

Run: (환경 준비 명령) `tests/test_essence_readiness.py -v -k noise`
Expected: `TypeError: resolve_essence_readiness() got an unexpected keyword argument 'excluded_note_hash'`.

- [ ] **Step 3: 구현**

`resolve_essence_readiness` 시그니처와 `fresh` 계산을 교체:

```python
def resolve_essence_readiness(
    approved: HospitalContentPhilosophy | None,
    required_sources: list[HospitalSourceAsset],
    *,
    excluded_note_hash: str | None = None,
) -> EssenceReadiness:
    processed_sources = [
        source for source in required_sources if source.status == SourceStatus.PROCESSED
    ]
    snapshot = compute_sources_snapshot_hash(processed_sources)
    processed_snapshot_matches = bool(
        approved
        and processed_sources
        and approved.source_snapshot_hash
        and approved.source_snapshot_hash == snapshot
    )
    # 노이즈 제외 집합은 엄격한 current에만 관여한다. 컬럼 이전 승인(NULL)이거나 호출자가
    # 현재 집합을 계산하지 않았으면(None) 이 조건은 통과시킨다.
    approved_noise_hash = getattr(approved, "evidence_noise_hash", None) if approved else None
    noise_matches = (
        approved_noise_hash is None
        or excluded_note_hash is None
        or approved_noise_hash == excluded_note_hash
    )
    fresh = (
        processed_snapshot_matches
        and len(processed_sources) == len(required_sources)
        and noise_matches
    )
```

(이후 `source_asset_ids`/`public_philosophy` 계산과 return은 그대로.)

`get_essence_readiness`:

```python
    excluded_note_hash = await load_evidence_noise_hash(db, hospital_id)
    return resolve_essence_readiness(
        approved, list(sources_result.scalars().all()), excluded_note_hash=excluded_note_hash
    )
```

`get_essence_readiness_sync`:

```python
    excluded_note_hash = load_evidence_noise_hash_sync(db, hospital_id)
    return resolve_essence_readiness(approved, required_sources, excluded_note_hash=excluded_note_hash)
```

`_get_lightweight_essence_readiness`: select에 `HospitalContentPhilosophy.evidence_noise_hash` 컬럼을 추가하고 `approved_id, source_snapshot_hash, source_asset_ids, evidence_noise_hash = approved_row`, `approved_stub`에 `evidence_noise_hash=evidence_noise_hash`를 넣고, `readiness = resolve_essence_readiness(approved_stub, required_sources, excluded_note_hash=await load_evidence_noise_hash(db, hospital_id))`.

import: `from app.services.evidence_noise import load_evidence_noise_hash, load_evidence_noise_hash_sync`.

- [ ] **Step 4: 통과 확인**

Run: (환경 준비 명령) `tests/test_essence_readiness.py tests/test_essence_serializers.py tests/integration/test_content_public_review_backfill_postgres.py -v`
Expected: 전부 PASS (backfill의 `resolve_essence_readiness(philosophy, sources)` 호출은 kwarg 없이 legacy 동작 유지).

- [ ] **Step 5: 커밋**

```bash
cd /Users/woojinlee/Documents/projects/reputation && git add backend/app/services/essence_readiness.py backend/tests/test_essence_readiness.py && git commit -m "fix: make strict Essence readiness depend on the excluded-evidence set

Hiding a note as noise now makes current=None (blocks new generation) while
public_philosophy still stands on the source snapshot, so published content
stays served. (H-02)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: 자동 검수가 노이즈를 제외하고, hash를 비교·저장한다 (H-02)

> **실행 기록 (2026-09-08):** Task 2 재검토에서 남은 TOCTOU(노이즈 토글 엔드포인트가 병원 advisory lock 없이 커밋 → backfill 체크포인트의 hash 읽기와 커밋 사이 경합)를 이 Task의 **Step 0**(별도 커밋)으로 흡수: `mark_evidence_notes_as_noise`가 잠금을 잡고, 체크포인트가 같은 잠금 아래인지 확인. 또한 Task 2에서 만든 `get_public_essence_readiness`는 관대한 `current`를 노출하는 footgun이라 **`public_philosophy`만 반환**하도록 좁힌다(Step 3e). 구현 `2461213` 뒤 Codex가 **실제 결함**을 잡았다: 승인 블록의 잔여 source-hash-only `UP_TO_DATE`(≈1457행)가 노이즈-only 변경·NULL 승인에서 후보 승인 전에 반환돼 hash가 영영 안 써지고 `essence_refresh_needed`가 계속 True → 15분마다 유료 합성 반복. 후속 커밋에서 그 분기에도 `_noise_hash_matches`를 요구하고, 같은 자료로 재검수를 끝까지 돌려 AUTO_APPROVED·hash 저장·두 번째 preflight False를 증명하는 통합 테스트를 추가한다.

**Files:**
- Modify: `backend/app/services/essence_auto_review.py` — `_notes_for_sources`(286-304), `essence_refresh_needed`(1179-1204), `refresh_essence_snapshot`(1207-, UP_TO_DATE·CAS·승인 기록)
- Modify: `backend/app/api/admin/essence.py` — `approve_philosophy` 승인 기록
- Test: `backend/tests/integration/test_essence_auto_review_postgres.py`

- [ ] **Step 1: 통합 테스트 작성**

`backend/tests/integration/test_essence_auto_review_postgres.py`에 추가. 파일의 `pg_session` 픽스처와 `_seed_baseline(pg_session, label=...)`을 사용한다 — `_seed_baseline`이 무엇을 반환하는지(병원·자료·노트·승인본) 파일에서 확인해 아래의 언패킹을 그 반환 형태에 맞춘다(반환이 dict/namespace면 이름으로 접근).

```python
from app.services.evidence_noise import compute_evidence_noise_hash
from app.services.essence_readiness import get_essence_readiness_sync


def test_marking_a_note_as_noise_requests_refresh_but_keeps_public_baseline(pg_session):
    """H-02: 노이즈 제외 → 재검수 필요 + 엄격 current 없음, 공개 근거는 유지."""
    hospital, source, note, approved = _seed_baseline(pg_session, label="noise")
    approved.evidence_noise_hash = compute_evidence_noise_hash([])
    pg_session.commit()
    assert essence_refresh_needed(pg_session, hospital.id) is False

    note.note_metadata = {**(note.note_metadata or {}), "is_noise": True}
    pg_session.commit()

    assert essence_refresh_needed(pg_session, hospital.id) is True
    readiness = get_essence_readiness_sync(pg_session, hospital.id)
    assert readiness.current is None
    assert readiness.public_philosophy is not None
    assert readiness.public_philosophy.id == approved.id


def test_refresh_stores_the_noise_hash_and_skips_noise_notes(pg_session, monkeypatch):
    """새 승인은 현재 노이즈 집합 hash를 기록하고 합성·검수 입력에서 노이즈 노트를 뺀다."""
    hospital, source, note, approved = _seed_baseline(pg_session, label="noise-store")
    noise = HospitalSourceEvidenceNote(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_asset_id=source.id,
        note_type=EvidenceNoteType.KEY_MESSAGE,
        claim="광고성 문구",
        source_excerpt=source.raw_text[:10],
        confidence=0.5,
        note_metadata={"is_noise": True},
    )
    pg_session.add(noise)
    approved.status = PhilosophyStatus.ARCHIVED
    pg_session.commit()

    seen_note_ids: list[str] = []

    def _synth(hospital_obj, sources, notes, **kwargs):
        seen_note_ids.extend(str(item.id) for item in notes)
        payload = _candidate_payload_from(approved, sources, notes)  # 파일에 이미 있는 helper를 쓴다; 없으면 아래 주석 참고
        return payload

    def _review(*args, **kwargs):
        return EssenceAiReview(
            decision="APPROVE",
            confidence=0.99,
            findings=(),
            reviewed_evidence_note_ids=(str(note.id),),
            summary="ok",
            model="test",
        )

    result = refresh_essence_snapshot(pg_session, hospital.id, synthesizer=_synth, reviewer=_review)

    assert result.status == EssenceRefreshStatus.AUTO_APPROVED
    assert str(noise.id) not in seen_note_ids
    stored = pg_session.get(HospitalContentPhilosophy, result.philosophy_id)
    assert stored.evidence_noise_hash == compute_evidence_noise_hash([noise.id])
```

`_candidate_payload_from`가 파일에 없으면, 같은 파일의 기존 AUTO_APPROVED 테스트가 synthesizer로 넘기는 payload 생성 방식을 그대로 복사해 이 테스트 안에서 dict를 만든다(필수 키: `source_asset_ids`, `source_snapshot_hash`, `evidence_map`, `medical_ad_risk_rules` 등 기존 테스트가 쓰는 것과 동일). 새 helper를 파일 밖에 만들지 않는다.

- [ ] **Step 2: 실패 확인**

Run: (환경 준비 명령) `tests/integration/test_essence_auto_review_postgres.py -v -k noise`
Expected: 첫 테스트 `essence_refresh_needed(...) is True` 실패(현재 False), 둘째 `evidence_noise_hash` None 또는 `seen_note_ids`에 noise 포함으로 실패.

- [ ] **Step 3: 구현**

`essence_auto_review.py` import에 `from app.services.evidence_noise import load_evidence_noise_hash_sync, not_noise_note_predicate` 추가.

`_notes_for_sources`의 `.where(...)`에 `not_noise_note_predicate()`를 추가:

```python
            .where(
                HospitalSourceEvidenceNote.hospital_id == hospital_id,
                HospitalSourceEvidenceNote.source_asset_id.in_(source_ids),
                not_noise_note_predicate(),
            )
```

`essence_refresh_needed`의 UP_TO_DATE 조건을 교체:

```python
    snapshot_hash = compute_sources_snapshot_hash(sources)
    if previous is not None and previous.source_snapshot_hash == snapshot_hash:
        if _noise_hash_matches(db, hospital_id, previous):
            return False
        # 자료는 그대로인데 제외 집합이 바뀌었다 — 재검수 대상. 같은 snapshot의 대기 초안이
        # 있으면 아래 기존 규칙이 그 초안을 존중한다.
```

새 helper (파일 내 `_status_value` 근처):

```python
def _noise_hash_matches(db: Session, hospital_id: uuid.UUID, previous: HospitalContentPhilosophy) -> bool:
    """저장된 노이즈 집합 hash가 현재와 같은가.

    NULL(컬럼 이전 승인)은 readiness에서는 관대하게(생성 차단 없음) 다루지만, 여기서는
    **한 번 재검수해 실제 값을 쓰도록** False를 돌려준다 — 그러지 않으면 기존 병원은
    자료가 바뀔 때까지 노이즈 제외가 승인에 반영되지 않는 H-02 구멍이 그대로 남는다.
    운영 병원 수만큼 1회성 유료 재검수가 발생한다(현재 7곳).
    """
    stored = getattr(previous, "evidence_noise_hash", None)
    if stored is None:
        return False
    return stored == load_evidence_noise_hash_sync(db, hospital_id)
```

주의: `refresh_essence_snapshot`의 UP_TO_DATE 조건에도 같은 helper를 쓰므로, NULL인 승인은 다음 실행에서 새 후보를 합성·검수하고 승인하며 그때 `evidence_noise_hash`가 채워진다. 같은 snapshot의 ESCALATED 초안이 이미 있으면 기존 규칙대로 사람 몫이다. 통합 테스트에 "NULL 승인은 `essence_refresh_needed`가 True"를 추가한다:

```python
def test_legacy_approval_without_noise_hash_is_refreshed_once(pg_session):
    hospital, source, note, approved = _seed_baseline(pg_session, label="legacy-null")
    approved.evidence_noise_hash = None
    pg_session.commit()
    assert essence_refresh_needed(pg_session, hospital.id) is True
```

`refresh_essence_snapshot`:
- `snapshot_hash = compute_sources_snapshot_hash(sources)` 직후에 `noise_hash = load_evidence_noise_hash_sync(db, hospital_id)`를 계산하고, UP_TO_DATE 반환 조건을 `previous.source_snapshot_hash == snapshot_hash and _noise_hash_matches(db, hospital_id, previous)`로 바꾼다.
- 외부 검수 후 CAS(현재 `compute_sources_snapshot_hash(current_sources) != snapshot_hash` 조건, ≈1355행)에 `or load_evidence_noise_hash_sync(db, hospital_id) != noise_hash`를 추가한다(같은 SNAPSHOT_CHANGED 처리).
- 승인 기록(≈1455행 `candidate.status = PhilosophyStatus.APPROVED` 다음)에 `candidate.evidence_noise_hash = noise_hash`를 추가한다.

`essence.py` `approve_philosophy`: `philosophy.status = PhilosophyStatus.APPROVED` 다음에

```python
    philosophy.evidence_noise_hash = await load_evidence_noise_hash(db, hospital_id)
```

(import: `from app.services.evidence_noise import load_evidence_noise_hash`.)

- [ ] **Step 4: 통과 확인**

Run: (환경 준비 명령) `tests/integration/test_essence_auto_review_postgres.py tests/test_essence_auto_review.py tests/test_essence_auto_review_dispatch.py tests/integration/test_essence_approve_grounding.py -v`
Expected: 전부 PASS.

- [ ] **Step 5: 커밋**

```bash
cd /Users/woojinlee/Documents/projects/reputation && git add backend/app/services/essence_auto_review.py backend/app/api/admin/essence.py backend/tests/integration/test_essence_auto_review_postgres.py && git commit -m "fix: automatic Essence review excludes noise notes and tracks the excluded set

Changing which notes are excluded now triggers a refresh and every approval
records evidence_noise_hash. (H-02)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: 예외 승인은 자동 검수 finding을 재확인하고 사유를 요구한다 (H-03)

> **실행 기록 (2026-09-08):** 구현 `e2fbdd3`. 검수에서 우회 경로 발견 — `PhilosophyPatch.unsupported_gaps`가 편집 가능해 클라이언트가 PATCH로 finding을 비운 뒤 승인하면 게이트를 지나칠 수 있었다(admin의 `persistThenApprove`가 실제로 승인 직전에 PATCH한다). 후속 커밋에서 `automatic_ai_review`·자동 복구 사이클 항목을 **서버 소유 gap 필드**로 정의해 모든 PATCH에서 보존한다. 지우는 유일한 경로는 Task 5의 재검수 요청이다.

**Files:**
- Modify: `backend/app/schemas/essence.py:108` (`PhilosophyApprove`)
- Modify: `backend/app/api/admin/essence.py` `approve_philosophy` (grounding 검사 직후)
- Modify: `admin/app/hospitals/[id]/essence/page.tsx` (approve body·예외 사유 textarea, ≈493-515, ≈1000-1040)
- Test: `backend/tests/integration/test_essence_approve_grounding.py`

- [ ] **Step 1: 통합 테스트 작성**

`backend/tests/integration/test_essence_approve_grounding.py`에 추가. 파일의 `_seed_draft(db, *, mapped_note_ids=...)`를 재사용하되, 그 함수가 만드는 draft에 `unsupported_gaps`를 덧붙이는 방식으로 자동 검수 finding을 흉내낸다. `_seed_draft`의 반환값 형태(예: `(hospital, draft, note)`)는 파일에서 확인해 맞춘다. 승인 호출 방식(`set_request_actor`로 검증된 actor 설정, `essence_api.approve_philosophy(hospital.id, draft.id, body, db=db)`)은 파일의 기존 성공 테스트를 그대로 따른다.

```python
def _approve_body(**overrides):
    base = dict(reviewed_by="reviewer@example.com", approval_note=None, confirm_evidence_reviewed=True)
    base.update(overrides)
    return essence_api.PhilosophyApprove(**base)


async def test_manual_approve_refuses_unresolved_auto_review_findings(pg_async_session):
    """H-03: 자동 검수가 보류한 사유를 체크박스 하나로 지나칠 수 없다."""
    hospital, draft, note = await _seed_draft(pg_async_session, mapped_note_ids=[str(note_id) for note_id in []]) if False else await _seed_draft_for_findings(pg_async_session)
    draft.unsupported_gaps = [{"field": "automatic_ai_review", "reason": "근거 없는 효과 주장"}]
    await pg_async_session.commit()

    token = set_request_actor("reviewer@example.com")
    try:
        with pytest.raises(HTTPException) as exc:
            await essence_api.approve_philosophy(hospital.id, draft.id, _approve_body(), db=pg_async_session)
    finally:
        reset_request_actor(token)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "AUTO_REVIEW_FINDINGS_UNRESOLVED"
    assert "근거 없는 효과 주장" in exc.value.detail["findings"]


async def test_manual_approve_with_override_reason_records_the_overridden_findings(pg_async_session):
    hospital, draft, note = await _seed_draft_for_findings(pg_async_session)
    draft.unsupported_gaps = [{"field": "automatic_ai_review", "reason": "근거 없는 효과 주장"}]
    await pg_async_session.commit()

    token = set_request_actor("reviewer@example.com")
    try:
        response = await essence_api.approve_philosophy(
            hospital.id,
            draft.id,
            _approve_body(override_reason="원장 인터뷰 원문 2문단에 해당 효과의 근거가 직접 서술되어 있음을 확인함"),
            db=pg_async_session,
        )
    finally:
        reset_request_actor(token)

    assert response.status == "APPROVED"
    audit = (
        await pg_async_session.execute(
            select(AdminAuditLog).where(AdminAuditLog.action == "approve_philosophy").order_by(AdminAuditLog.created_at.desc())
        )
    ).scalars().first()
    assert audit.detail["override_reason"].startswith("원장 인터뷰")
    assert audit.detail["overridden_auto_review_findings"] == ["근거 없는 효과 주장"]


async def test_manual_approve_rejects_a_short_override_reason():
    with pytest.raises(ValueError):
        essence_api.PhilosophyApprove(
            reviewed_by="r", approval_note=None, confirm_evidence_reviewed=True, override_reason="짧음"
        )
```

`_seed_draft_for_findings(db)`는 이 테스트 파일 안에 정의한다: 기존 `_seed_draft`를 그 파일의 성공 테스트가 쓰는 인자로 호출해 **grounding이 통과하는** draft를 만들고 `(hospital, draft, note)`를 돌려준다(기존 `_seed_draft`의 반환 형태에 맞춘다). 첫 테스트의 `if False else` 구절은 쓰지 말고 `await _seed_draft_for_findings(pg_async_session)`만 사용한다. `AdminAuditLog`는 `app.models.audit`에서 import한다. `approve_philosophy` 감사 로그 action 이름이 `approve_philosophy`가 아니면 파일에서 실제 이름을 확인해 맞춘다.

- [ ] **Step 2: 실패 확인**

Run: (환경 준비 명령) `tests/integration/test_essence_approve_grounding.py -v -k "override or unresolved"`
Expected: 첫 테스트 `DID NOT RAISE`(현재는 승인됨), 둘째 `TypeError: unexpected keyword 'override_reason'`, 셋째 `DID NOT RAISE`.

- [ ] **Step 3: 구현**

`schemas/essence.py` `PhilosophyApprove`에 필드 추가:

```python
    # 자동 검수가 보류한 초안을 사람이 승인할 때의 근거. 20자 미만은 사유가 아니다.
    override_reason: str | None = Field(default=None, min_length=20, max_length=2000)
```

`essence.py` `approve_philosophy`: `grounding_errors` 검사(422) **직후**, 필수 자료 조회 **앞**에:

```python
    auto_findings = [
        str(gap.get("reason"))
        for gap in (philosophy.unsupported_gaps or [])
        if isinstance(gap, dict) and gap.get("field") == "automatic_ai_review" and gap.get("reason")
    ]
    if auto_findings and not body.override_reason:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "AUTO_REVIEW_FINDINGS_UNRESOLVED",
                "findings": auto_findings,
                "message": (
                    "자동 검수가 보류한 사유가 남아 있습니다. 초안을 수정해 재검수를 요청하거나, "
                    "각 사유를 확인한 근거를 예외 승인 사유(20자 이상)에 적어 주세요."
                ),
            },
        )
```

승인 감사 로그의 `detail`에 두 키를 추가한다: `"override_reason": body.override_reason`, `"overridden_auto_review_findings": auto_findings`.

`admin/app/hospitals/[id]/essence/page.tsx`:
- state 추가: `const [overrideReason, setOverrideReason] = useState('')`.
- `approveDraft`의 body에 `override_reason: overrideReason.trim() || null` 추가; 성공 후 `setOverrideReason('')`.
- 승인 메모 textarea 아래에(`approvalNote` 블록 다음) 자동 검수 finding이 있을 때만 사유 입력을 그린다. `selectedDraft.unsupported_gaps`가 TS 타입에 있는지 확인(없으면 `ContentPhilosophy` 타입에 `unsupported_gaps?: Array<{ field: string; reason: string }>` 추가):

```tsx
                  {autoReviewFindings.length > 0 && (
                    <div>
                      <label htmlFor="essence-override-reason" className="block text-xs font-medium text-slate-600 mb-1">
                        자동 검수 보류 사유별 확인 근거 (20자 이상, 필수)
                      </label>
                      <ul className="mb-1 list-disc pl-4 text-[11px] text-amber-800">
                        {autoReviewFindings.map((finding) => <li key={finding}>{finding}</li>)}
                      </ul>
                      <textarea
                        id="essence-override-reason"
                        value={overrideReason}
                        onChange={(e) => setOverrideReason(e.target.value)}
                        rows={3}
                        className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                      />
                    </div>
                  )}
```

`autoReviewFindings`는 컴포넌트 안에서 `const autoReviewFindings = (selectedDraft?.unsupported_gaps ?? []).filter((g) => g.field === 'automatic_ai_review').map((g) => g.reason)`로 계산한다. 승인 버튼의 `disabled` 조건에 `|| (autoReviewFindings.length > 0 && overrideReason.trim().length < 20)`를 추가한다.

- [ ] **Step 4: 통과 확인**

Run: (환경 준비 명령) `tests/integration/test_essence_approve_grounding.py tests/test_essence_approve_actor.py -v` → PASS.
Run: `cd /Users/woojinlee/Documents/projects/reputation/admin && npm test 2>&1 | grep -E "ℹ (pass|fail)" && npm run lint && npm run typecheck` → `ℹ fail 0`, 종료 0.

- [ ] **Step 5: 커밋**

```bash
cd /Users/woojinlee/Documents/projects/reputation && git add backend/app/schemas/essence.py backend/app/api/admin/essence.py backend/tests/integration/test_essence_approve_grounding.py 'admin/app/hospitals/[id]/essence/page.tsx' admin/types/index.ts && git commit -m "fix: manual Essence approval must address automatic review findings

An ESCALATED draft can no longer be approved with a checkbox alone; the
operator either requests re-review or records a ≥20-char reason per finding,
which the audit log keeps. (H-03)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

(`admin/types/index.ts`는 수정했을 때만 add한다.)

---

### Task 5: 수동 초안 생성 삭제, 보관·재검수 요청 추가 (H-04)

> **실행 기록 (2026-09-08):** Task 4 검토의 잔여 지적을 이 Task의 **Step 0**(별도 커밋)으로 흡수 — ① `automatic_ai_review`·자동 복구 사이클 gap 필드명을 `app/models/essence.py`의 공유 상수로(두 모듈의 리터럴 중복 제거) ② `override_reason`은 strip 후 검증(공백 20자 불허) ③ 사유가 있어도 snapshot 변경 409가 우선함을 증명하는 테스트 ④ 대시보드 감사 기록 줄에 예외 승인 사유·보류 사유 표시. 재검수 요청의 계약을 명시: 초안 본문 수정은 재합성 입력이 아니며(근거 노트·자료가 입력), 사람이 고친 문장을 채택하려면 예외 승인을 쓴다. 구현 `8c6e1b1`(Step 0)·`6270576`(본) 뒤 Codex REQUEST_CHANGES → 후속 커밋에서 ① 커밋 후 broker 장애는 500 대신 200 + `re_review_dispatched=false`(15분 재조정이 회수) ② 병원당 30분 쿨다운(감사 기록 기반, 429 `RE_REVIEW_COOLDOWN`) + 유료 호출을 명시한 확인 대화상자 ③ 버튼 "자료 기준 자동 재검수"로 문구 정렬 ④ admin에 `AUTO_REVIEW_GAP_FIELD` 상수. 결정적 finding이 사람 조작 없이 반복 비용을 만들지 않는 것은 확인됨(재에스컬레이션은 복구 마커 8을 저장하고 자동 복구는 <8만 대상).

**Files:**
- Modify: `backend/app/api/admin/essence.py` — `create_philosophy_draft`(1539-1607) 삭제, `archive`/`re-review` 라우트 추가
- Modify: `backend/app/schemas/essence.py:85` — `PhilosophyDraftCreate` 삭제
- Modify: `admin/app/hospitals/[id]/essence/page.tsx` — `createDraft`(431-454)·선택 체크박스·"수동 초안 만들기" 버튼(≈740) 제거, 재검수/보관 버튼 추가
- Modify: `admin/lib/admin-proxy.ts:10` — `/essence/philosophy/draft` slow-path 제거
- Test: `backend/tests/test_essence_routes.py`(신규), `backend/tests/integration/test_essence_approve_grounding.py`, `admin/lib/admin-proxy.test.ts`, `admin/lib/api.test.ts`

- [ ] **Step 1: 라우트 계약 테스트 작성**

`backend/tests/test_essence_routes.py`:

```python
"""H-04: 수동 초안 생성 경로는 없고, 보관·재검수 요청 경로는 있다."""

from app.main import app


def _admin_paths() -> set[tuple[str, str]]:
    paths: set[tuple[str, str]] = set()
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        for method in methods:
            paths.add((method, route.path))
    return paths


def test_manual_draft_creation_route_is_gone():
    assert ("POST", "/api/v1/admin/hospitals/{hospital_id}/essence/philosophy/draft") not in _admin_paths()


def test_archive_and_re_review_routes_exist():
    paths = _admin_paths()
    assert ("POST", "/api/v1/admin/hospitals/{hospital_id}/essence/philosophy/{philosophy_id}/archive") in paths
    assert ("POST", "/api/v1/admin/hospitals/{hospital_id}/essence/philosophy/{philosophy_id}/re-review") in paths
```

라우터 prefix가 `/api/v1/admin/hospitals/{hospital_id}/essence`와 다르면(예: `hospital_id` 경로 변수명), 기존 approve 라우트 `(...)/philosophy/{philosophy_id}/approve`가 `_admin_paths()`에 어떻게 들어 있는지 출력해 같은 형태로 맞춘다.

`backend/tests/integration/test_essence_approve_grounding.py`에 추가:

```python
async def test_re_review_archives_the_draft_and_dispatches_auto_review(pg_async_session, monkeypatch):
    hospital, draft, note = await _seed_draft_for_findings(pg_async_session)
    dispatched: list[dict] = []
    monkeypatch.setattr(
        essence_api.auto_review_essence_snapshot,
        "apply_async",
        lambda **kwargs: dispatched.append(kwargs),
    )

    token = set_request_actor("reviewer@example.com")
    try:
        response = await essence_api.request_philosophy_re_review(hospital.id, draft.id, db=pg_async_session)
    finally:
        reset_request_actor(token)

    assert response.status == "ARCHIVED"
    assert dispatched and dispatched[0]["args"] == [str(hospital.id)]
    assert dispatched[0]["queue"] == "content"


async def test_archive_only_accepts_drafts(pg_async_session):
    hospital, draft, note = await _seed_draft_for_findings(pg_async_session)
    draft.status = PhilosophyStatus.APPROVED
    await pg_async_session.commit()

    with pytest.raises(HTTPException) as exc:
        await essence_api.archive_philosophy(hospital.id, draft.id, db=pg_async_session)
    assert exc.value.status_code == 400
```

- [ ] **Step 2: 실패 확인**

Run: (환경 준비 명령) `tests/test_essence_routes.py tests/integration/test_essence_approve_grounding.py -v -k "route or re_review or archive"`
Expected: `test_manual_draft_creation_route_is_gone` FAIL(경로 존재), 나머지 `AttributeError`.

- [ ] **Step 3: 백엔드 구현**

`essence.py`에서 `create_philosophy_draft` 함수와 그 데코레이터를 통째로 삭제한다. 그 함수만 쓰던 import(`PhilosophyDraftCreate`, `synthesize_philosophy`, `find_error_marker_fields`, `metered_llm_calls`, `cost_guard`, `asyncio`)는 ruff F401이 알려주는 것만 제거한다 — 다른 곳에서도 쓰이면 남긴다. `schemas/essence.py`에서 `PhilosophyDraftCreate` 클래스를 삭제한다.

`approve_philosophy` 바로 앞에 두 라우트를 추가한다:

```python
def _archive_draft_or_400(philosophy: HospitalContentPhilosophy) -> str:
    if philosophy.status != PhilosophyStatus.DRAFT:
        raise HTTPException(status_code=400, detail="초안 상태의 콘텐츠 운영 기준만 보관할 수 있습니다.")
    previous_status = philosophy.status.value if hasattr(philosophy.status, "value") else str(philosophy.status)
    philosophy.status = PhilosophyStatus.ARCHIVED
    return previous_status


@router.post("/philosophy/{philosophy_id}/archive", response_model=PhilosophyResponse)
async def archive_philosophy(
    hospital_id: uuid.UUID,
    philosophy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """보류된 초안을 보관한다. 자동 검수는 같은 snapshot의 대기 초안이 없어야 다시 돈다."""
    await acquire_hospital_advisory_lock(db, hospital_id)
    philosophy = await _get_philosophy_or_404(db, hospital_id, philosophy_id)
    previous_status = _archive_draft_or_400(philosophy)
    await write_audit_log(
        db,
        action="archive_philosophy",
        hospital_id=hospital_id,
        actor=default_actor(),
        target_type="philosophy",
        target_id=philosophy_id,
        detail={"previous_status": previous_status},
    )
    await db.commit()
    await db.refresh(philosophy)
    return _serialize_philosophy(philosophy)


@router.post("/philosophy/{philosophy_id}/re-review", response_model=PhilosophyResponse)
async def request_philosophy_re_review(
    hospital_id: uuid.UUID,
    philosophy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """보류된 초안을 보관하고 자동 검수를 즉시 다시 요청한다.

    사람이 초안을 고쳐 다시 검수받고 싶을 때의 유일한 경로다. 수동 합성은 없다 —
    합성·검수·승인은 언제나 워커의 자동 경로가 수행한다(H-04).
    """
    await acquire_hospital_advisory_lock(db, hospital_id)
    hospital = await _get_hospital_or_404(db, hospital_id)
    philosophy = await _get_philosophy_or_404(db, hospital_id, philosophy_id)
    previous_status = _archive_draft_or_400(philosophy)
    await write_audit_log(
        db,
        action="request_philosophy_re_review",
        hospital_id=hospital_id,
        actor=default_actor(),
        target_type="philosophy",
        target_id=philosophy_id,
        detail={"previous_status": previous_status},
    )
    await db.commit()
    await db.refresh(philosophy)
    # 커밋 이후의 외부 효과. 유실되면 15분 reconcile(`reconcile_essence_snapshots`)이 회수한다.
    auto_review_essence_snapshot.apply_async(
        args=[str(hospital.id)],
        queue="content",
        headers=build_dispatch_headers("auto-review-essence-snapshot", str(hospital.id)),
    )
    return _serialize_philosophy(philosophy)
```

import: `from app.workers.tasks import auto_review_essence_snapshot` 과 `from app.workers.dispatch_auth import build_dispatch_headers` (essence.py가 이미 tasks를 import하는 방식이 있으면 그 방식을 따른다 — 순환 import가 나면 함수 안에서 지역 import로 바꾼다).

- [ ] **Step 4: 백엔드 통과 확인**

Run: (환경 준비 명령) `tests/test_essence_routes.py tests/integration/test_essence_approve_grounding.py tests/test_essence_api_threading.py -v` → PASS. `.venv/bin/python -m ruff check .` → clean. `test_essence_api_threading.py`가 삭제한 draft 엔드포인트를 테스트하고 있었다면 그 테스트 함수만 삭제하고 커밋 메시지에 적는다.

- [ ] **Step 5: admin 구현**

`admin/lib/admin-proxy.ts`: `isSlowAdminProxyPath`에서 `path.endsWith('/essence/philosophy/draft')` 줄과 앞의 `||`를 제거. `admin/lib/admin-proxy.test.ts`·`admin/lib/api.test.ts`에서 `/philosophy/draft`를 기대하는 assertion을 제거하거나 `/essence/sources/x/process`로 바꾼다.

`admin/app/hospitals/[id]/essence/page.tsx`:
- `createDraft` 함수, `selectedSourceIds` state와 자료 표의 선택 체크박스, `수동 초안 만들기` 버튼(≈740) 삭제.
- 초안 패널(≈724 drafts 섹션)의 각 `DRAFT` 항목에 두 버튼 추가:

```tsx
                <button
                  onClick={() => reReviewDraft(draft.id)}
                  disabled={actionLoading === `re-review-${draft.id}`}
                  className="rounded-md border border-blue-200 px-2 py-1 text-[11px] font-medium text-blue-700 hover:bg-blue-50 disabled:opacity-50"
                >
                  수정 반영 후 자동 재검수 요청
                </button>
                <button
                  onClick={() => archiveDraft(draft.id)}
                  disabled={actionLoading === `archive-${draft.id}`}
                  className="rounded-md border border-slate-200 px-2 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50"
                >
                  보관
                </button>
```

핸들러(기존 `excludeSource` 패턴을 따른다):

```tsx
  async function reReviewDraft(draftId: string) {
    setActionLoading(`re-review-${draftId}`)
    setError(null)
    setNotice(null)
    try {
      if (selectedDraft?.id === draftId && selectedDraft.status === 'DRAFT') await persistVisibleDraft()
      await fetchAPI(`/admin/hospitals/${id}/essence/philosophy/${draftId}/re-review`, { method: 'POST' })
      setNotice('초안을 보관하고 자동 검수를 다시 요청했습니다. 결과는 이 화면과 운영 센터에 표시됩니다.')
      await load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '재검수 요청에 실패했습니다.')
    } finally {
      setActionLoading(null)
    }
  }

  async function archiveDraft(draftId: string) {
    if (!confirm('이 초안을 보관하면 화면에서 사라지고 승인할 수 없습니다. 계속하시겠습니까?')) return
    setActionLoading(`archive-${draftId}`)
    setError(null)
    setNotice(null)
    try {
      await fetchAPI(`/admin/hospitals/${id}/essence/philosophy/${draftId}/archive`, { method: 'POST' })
      setNotice('초안을 보관했습니다.')
      await load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '보관에 실패했습니다.')
    } finally {
      setActionLoading(null)
    }
  }
```

- [ ] **Step 6: admin 통과 확인**

Run: `cd /Users/woojinlee/Documents/projects/reputation/admin && npm test 2>&1 | grep -E "ℹ (pass|fail)" && npm run lint && npm run typecheck` → `ℹ fail 0`, 종료 0. `grep -rn "philosophy/draft" admin/app admin/lib` → 결과 없음.

- [ ] **Step 7: 커밋**

```bash
cd /Users/woojinlee/Documents/projects/reputation && git add backend/app/api/admin/essence.py backend/app/schemas/essence.py backend/tests/test_essence_routes.py backend/tests/integration/test_essence_approve_grounding.py backend/tests/test_essence_api_threading.py 'admin/app/hospitals/[id]/essence/page.tsx' admin/lib/admin-proxy.ts admin/lib/admin-proxy.test.ts admin/lib/api.test.ts && git commit -m "fix: replace manual Essence drafting with archive and re-review

A manual draft on the current snapshot permanently disabled automatic
approval, and subset drafts could be neither approved nor removed. Operators
now edit the escalated draft and request re-review, or archive it. (H-04)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: 원문 없는 URL 자료는 필수 자료가 아니다 (M-01)

**Files:**
- Modify: `backend/app/services/essence_readiness.py` — predicate 추가, 세 로더가 사용
- Modify: `backend/app/services/essence_auto_review.py:248-261` (`_required_sources`)
- Modify: `backend/app/api/admin/essence.py` `approve_philosophy`의 `required_result` 쿼리(≈1680)
- Test: `backend/tests/integration/test_essence_required_sources_postgres.py` (신규)

- [ ] **Step 1: 통합 테스트 작성**

```python
"""M-01: 원문을 추출할 수 없는 URL 전용 자료가 승인을 영원히 막지 않는다."""

import uuid
from datetime import datetime, timezone

from app.models.essence import (
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus
from app.services.essence_engine import compute_sources_snapshot_hash
from app.services.essence_readiness import get_essence_readiness
from app.services.evidence_noise import compute_evidence_noise_hash


async def _seed(db):
    label = uuid.uuid4().hex[:8]
    hospital = Hospital(id=uuid.uuid4(), name=f"URL 자료 {label}", slug=f"urlonly-{label}", status=HospitalStatus.ACTIVE, site_live=False)
    processed = HospitalSourceAsset(
        id=uuid.uuid4(), hospital_id=hospital.id, source_type=SourceType.INTERVIEW, title="원장 인터뷰",
        raw_text="진료 전에 충분히 설명합니다.", content_hash=f"{label}-a", status=SourceStatus.PROCESSED,
        processed_at=datetime.now(timezone.utc),
    )
    url_only = HospitalSourceAsset(
        id=uuid.uuid4(), hospital_id=hospital.id, source_type=SourceType.HOMEPAGE, title="홈페이지",
        url="https://clinic.example.com", raw_text=None, content_hash=f"{label}-b", status=SourceStatus.PENDING,
    )
    approved = HospitalContentPhilosophy(
        id=uuid.uuid4(), hospital_id=hospital.id, version=1, status=PhilosophyStatus.APPROVED,
        source_asset_ids=[str(processed.id)], source_snapshot_hash=compute_sources_snapshot_hash([processed]),
        evidence_noise_hash=compute_evidence_noise_hash([]),
    )
    db.add_all([hospital, processed, url_only, approved])
    await db.commit()
    return hospital, approved, url_only


async def test_url_only_source_without_text_does_not_block_current(pg_async_session):
    hospital, approved, url_only = await _seed(pg_async_session)

    readiness = await get_essence_readiness(pg_async_session, hospital.id)

    assert readiness.required_source_count == 1
    assert readiness.current is not None and readiness.current.id == approved.id


async def test_url_only_source_becomes_required_once_it_has_text(pg_async_session):
    hospital, approved, url_only = await _seed(pg_async_session)
    url_only.raw_text = "크롤링으로 채워진 본문"
    await pg_async_session.commit()

    readiness = await get_essence_readiness(pg_async_session, hospital.id)

    assert readiness.required_source_count == 2
    assert readiness.current is None
```

`HospitalContentPhilosophy` 생성에 필요한 NOT NULL JSON 필드(`content_principles` 등)가 default로 채워지지 않아 IntegrityError가 나면 `tests/integration/test_essence_approve_grounding.py`의 `_seed_draft`가 넣는 필드 목록을 그대로 복사한다.

- [ ] **Step 2: 실패 확인**

Run: (환경 준비 명령) `tests/integration/test_essence_required_sources_postgres.py -v`
Expected: 첫 테스트 `required_source_count == 1` 실패(현재 2).

- [ ] **Step 3: 구현**

`essence_readiness.py`에 predicate를 추가하고 세 로더(`get_essence_readiness`, `get_essence_readiness_sync`, `_get_lightweight_essence_readiness`)의 `HospitalSourceAsset` where 절을 이 함수 하나로 교체한다:

```python
from sqlalchemy import and_, func


def required_text_source_predicate():
    """콘텐츠 운영 기준의 필수 자료: 제외되지 않은, 사진이 아닌, **원문이 있는** 자료.

    URL만 있고 본문이 없는 자료는 추출할 것이 없으므로 처리도 승인도 막지 않는다.
    크롤·업로드로 본문이 생기는 순간 PENDING 필수 자료가 되어 승인이 stale이 된다.
    """
    return and_(
        HospitalSourceAsset.status != SourceStatus.EXCLUDED,
        HospitalSourceAsset.source_type.notin_(list(PHOTO_SOURCE_TYPES)),
        HospitalSourceAsset.raw_text.isnot(None),
        func.length(func.btrim(HospitalSourceAsset.raw_text)) > 0,
    )
```

각 로더: `.where(HospitalSourceAsset.hospital_id == hospital_id, required_text_source_predicate())`.

`essence_auto_review.py` `_required_sources`: 같은 predicate 사용 (`from app.services.essence_readiness import required_text_source_predicate`; 순환 import가 나면 predicate를 `app/services/evidence_noise.py` 옆의 새 파일 `app/services/essence_sources.py`로 옮기고 두 모듈이 거기서 import한다).

`essence.py` `approve_philosophy`의 `required_result` 쿼리도 같은 predicate로 교체한다.

- [ ] **Step 4: 통과 확인**

Run: (환경 준비 명령) `tests/integration/test_essence_required_sources_postgres.py tests/test_essence_readiness.py tests/integration/test_essence_auto_review_postgres.py tests/integration/test_essence_approve_grounding.py -v` → PASS.

- [ ] **Step 5: 커밋**

```bash
cd /Users/woojinlee/Documents/projects/reputation && git add backend/app/services/essence_readiness.py backend/app/services/essence_auto_review.py backend/app/api/admin/essence.py backend/tests/integration/test_essence_required_sources_postgres.py && git add backend/app/services/essence_sources.py 2>/dev/null; git commit -m "fix: URL-only sources without text are not required Essence evidence

They could never be processed, so they left approval blocked until an operator
excluded them by hand. (M-01)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: 이미 처리된 자료의 "재처리"는 거짓 완료를 띄우지 않는다 (M-03)

**Files:**
- Modify: `admin/app/hospitals/[id]/essence/page.tsx` `processSource`(365-385)

- [ ] **Step 1: 구현**

`processSource`에서 응답을 받은 뒤 `processed_at`이 바뀌지 않았으면 다른 문구를 쓴다. 기존 코드가 `setNotice('...처리를 시작했습니다')`류의 성공 문구를 쓰는 지점을 아래로 교체:

```tsx
      const before = sources.find((s) => s.id === sourceId)?.processed_at ?? null
      const detail = await fetchAPI<SourceAsset>(`/admin/hospitals/${id}/essence/sources/${sourceId}/process`, { method: 'POST' })
      if (detail.status === 'PROCESSED' && detail.processed_at === before) {
        setNotice('이미 최신 상태입니다. 자료 내용이 바뀌지 않아 다시 처리할 것이 없습니다.')
      } else {
        setNotice('자료 처리를 시작했습니다. 완료되면 근거 노트가 갱신됩니다.')
      }
```

(`sources` state 변수명과 `SourceAsset.processed_at` 필드명은 파일·타입에서 확인해 맞춘다.)

- [ ] **Step 2: 확인·커밋**

Run: `cd admin && npm run lint && npm run typecheck` → 종료 0.

```bash
cd /Users/woojinlee/Documents/projects/reputation && git add 'admin/app/hospitals/[id]/essence/page.tsx' && git commit -m "fix(admin): say 'already current' instead of a false completion on no-op reprocess (M-03)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: 전체 검증

- [ ] Backend 전체(환경 준비 명령) → `0 failed`. ruff clean.
- [ ] `make test-frontend` → admin·site `ℹ fail 0`, lint·typecheck 0.
- [ ] `grep -rn "philosophy/draft\|PhilosophyDraftCreate\|create_philosophy_draft" backend/app admin/app admin/lib` → 결과 없음.
- [ ] 완료 보고: Task별 테스트 수·결과, 마이그레이션 적용 로그(세 DB), 바뀐 파일 목록. push하지 않는다.
