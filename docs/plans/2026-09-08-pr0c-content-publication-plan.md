# PR-0C 콘텐츠 공개·발행 무결성 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** admin이 보여주는 "공개 중"이 실제 공개 사이트가 보는 것과 같아지게 하고, 공개 글 편집으로 무효화된 이미지 인증을 시스템이 스스로 복구하며, 매일 리셋되던 생성 재시도 예산·수동 발행의 병원 게이트 누락·요청 본문의 `published_by`·일정 화면의 요금제 변경을 없앤다 (검토 등록부 H-01, H-08, H-09, H-14, M-15, M-21, M-22 일부).

**Architecture:** 공개 가시성 판정을 `app/services/content_visibility.py`의 `assess_public_visibility()` 하나로 만들고 공개 사이트(`api/public/site.py`)와 admin 직렬화(`api/admin/content.py`)가 **같이** 쓴다. 공개 글의 제목 편집이 이미지 인증을 지우면 새 worker task `recertify_published_content_image`가 저장된 바이트를 재검수해 인증을 복구하고, 실패는 OperationRun FAILED → 인시던트로 사람에게 간다. 수동 발행은 자동 발행과 같은 병원 게이트(ACTIVE·site_live·일정) 아래서 병원 advisory lock을 잡고, 발행자는 검증된 로그인 actor다. 요금제의 권위는 계약 기록이며 일정 저장은 그 값을 읽기만 한다.

**Tech Stack:** FastAPI + SQLAlchemy async/sync, Celery, pytest(SimpleNamespace fakes + 실 PostgreSQL `pg_async_session`/`pg_conn`), Next.js 16 admin + `node:test`. 근거: [설계](2026-09-08-admin-hitl-simplification-design.md) §3 PR-0C, [검토](../reviews/2026-09-08-integrity-hitl-review.md) §2.

**이 PR에서 다루지 않음(후속 PR-0C-2):** M-05(새 승인 시 relabel 대신 재검사), M-06(운영자 재생성의 생성 lease), M-07(일정 교체 시 고아 슬롯 이관). `_generate_single_content_item`과 slot 이관 로직을 더 읽고 별도 계획으로 쓴다.

---

## 실행 결과 (2026-09-08)

| Task | 결함 | 커밋 | 검수 |
|---|---|---|---|
| 0 | 공백 문자 집합 parity 가드 | `41affe3` | Codex: 이스케이프 느슨 → `fd`-라운드에서 `\uXXXX`만 허용하는 엄격 토크나이저로 수정 |
| 1 | H-01 표시 | `6e4d5b2` → `db71a26` → `a16a764` → `691f186` → `fd00a49` | Fable·Codex 5라운드. 남아 있던 "공개" 경로를 순차 발견: 운영 상태 버킷/필터/집계 → 병원 목록·오늘 큐 → 기준 조회 배치화(병원 수 무관 2쿼리)·오늘 큐 SQL CASE·readiness 공개/보류 분리 → 대시보드·온보딩·`load_only`·배열 파라미터. 최종 Fable APPROVE, Codex는 H-16(리포트) 신규 지적 → 등록부로 이관 |
| 2 | H-01 복구 | `8f8d7a3` → `8921d13` → `dd14c73` → `0bbbdeb` → `259db40` `720c847` `20bbe45` | 재검수 task·CAS write-back·PATCH 디스패치. 검토에서 유료 루프·무음 정지 경로가 연쇄 발견되어 재설계: 예산·마커·인시던트 키를 `content_revision`이 아니라 **이미지 subject(유형+제목)**로, 시작 게이트(보류 코드·예산 소진이면 결제 없음), 행 잠금, 실행 단위 회계(`attempt_count`·`provider_called`), SQL 차단 마커(+가시 인시던트 EXISTS), 쿨다운·in-flight 30분, 운영자 재시도 동일 규칙. Fable 최종 APPROVE; Codex 잔여 2건 반영 후 **검토 종료**(사용자 지시: 라운드 상한) |
| 3 | H-08 | `96bda45` | fingerprint에서 `scheduled_date` 제거(배포 후 stranded 글마다 1회 신규 시도 허용) |
| 4 | H-09 | `907a33e` → `1e95338` | Codex: 우회 없음. admin이 409 게이트 문구를 의료광고 안내로 덮던 것 수정. Low 3건(테스트 보강) 등록부 |
| 5 | H-14 | `4cba988` → `320ace8` | Codex High: 계약 정정이 활성 일정 plan을 동기화하지 않음 → `_sync_active_schedule_plan`(감사 기록). 다운그레이드 시 현재 월 slot 상한만 갱신되는 reconciler 동작은 기존 그대로(LOW 등록) |
| 6 | M-15·M-21·M-22 | `3553105` → `320ace8` | 계획의 M-15 줄 참조 오류(콘텐츠 편집 게이트가 아니라 brief 편집)를 구현자가 잡아 정정. NOT_REQUIRED 알림·비본문 편집도 표본 규칙에 정렬 |
| 테스트 위생 | LOW 2건 | `adff1e6` | `recover_ops_incident`가 만든 전역 async 엔진이 닫힌 루프에 남아 `provider_usage._persist`가 예외를 삼키던 문제(루트 conftest dispose fixture) + 두 모듈의 롤백 밖 커밋 행 정리 |

## 환경 준비

PR-0A/0B와 동일. 백엔드 테스트 명령(`backend/`에서, 단일 파일은 끝에 `tests/<file>.py -v`):

```bash
cd /Users/woojinlee/Documents/projects/reputation/backend && export APP_ENV=test ADMIN_SECRET_KEY=test-admin-key DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/reputation_test" SYNC_DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/reputation_test" INTEGRATION_DATABASE_URL="postgresql://postgres:postgres@localhost:5432/reputation_test" TASK22_DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/reputation_test" INCIDENT_TEST_DATABASE_URL="postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test" OPERATIONS_TEST_DATABASE_URL="postgresql+psycopg2://reputation:reputation@localhost:5434/reputation_test" MIGRATION_UPGRADE_DATABASE_URL="postgresql+psycopg2://reputation:reputation@localhost:5434/reputation_autonomy_migration" COST_GUARD_REDIS_URL="redis://localhost:6379/2" INTEGRATION_REDIS_URL="redis://localhost:6379/3" REQUIRE_PDF_RENDER=1 DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib && .venv/bin/python -m pytest -q -p no:cacheprovider
```

## 파일 구조

| 파일 | 책임 | 변경 |
|---|---|---|
| `backend/app/services/content_visibility.py` (신규) | 공개 가시성 단일 판정 + 차단 코드·한국어 라벨 | 신규 |
| `backend/app/api/public/site.py` | `_is_public_safe_content`가 판정 함수 위임 | 수정 |
| `backend/app/api/admin/content.py` | 직렬화에 `public_visibility`·표본 여부, 수동 발행 게이트·actor, 일정의 요금제 검증, PATCH 재인증 dispatch | 수정 |
| `backend/app/workers/tasks.py` | `recertify_published_content_image` task | 추가 |
| `backend/app/workers/nightly_generation_batch.py` | `write_back_published_image_certificate` | 추가 |
| `backend/app/workers/generation_retry_policy.py` / `tasks.py:_generation_attempt_context` | fingerprint에서 `scheduled_date` 제거 | 수정 |
| `backend/app/core/celery_app.py` | 새 task 큐 라우팅 | 1줄 |
| `backend/app/services/content_publication.py` | 공백 제목/본문 차단 | 수정 |
| `admin/app/hospitals/[id]/content/page.tsx`, `admin/app/hospitals/[id]/schedule/page.tsx`, `admin/types/index.ts` | 공개 보류 표시, 표본만 확인 버튼, 공개 글 brief 편집 숨김, `published_by` 미전송, 요금제 읽기 전용, 타입 보정 | 수정 |
| 테스트 | `tests/test_content_visibility.py`(신규), `tests/integration/test_published_image_recertification_postgres.py`(신규), 기존 `tests/test_admin_content*.py`·`tests/test_content_publication*.py`·`tests/test_generation_*`에 추가 | |

---

### Task 1: 공개 가시성 판정을 사이트와 admin이 공유한다 (H-01 표시 부분, C-02)

**Files:**
- Create: `backend/app/services/content_visibility.py`, `backend/tests/test_content_visibility.py`
- Modify: `backend/app/api/public/site.py:615-649` (`_is_public_safe_content`)
- Modify: `backend/app/api/admin/content.py` — `_content_review_display`(≈1267-1293), `_build_compliance_summary`(≈1339-1372), `_serialize_item`(≈1375), list/detail 엔드포인트가 `public_philosophy_id`를 한 번 계산해 전달
- Modify: `admin/types/index.ts` (`ContentItem.compliance`), `admin/app/hospitals/[id]/content/page.tsx` (`getReviewState` ≈160-202, 공개 링크 ≈1709-1718)

- [ ] **Step 1: 판정 함수 테스트 작성**

`backend/tests/test_content_visibility.py`:

```python
"""H-01: 공개 사이트가 숨기는 글을 admin이 '공개 중'이라고 말하지 못하게 하는 단일 판정."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.models.content import ContentStatus, ContentType
from app.services.content_publication import IMAGE_POLICY_VERSION, image_subject_hash
from app.services.content_visibility import (
    VISIBILITY_BLOCKER_LABELS,
    assess_public_visibility,
)
from app.services.image_engine import image_content_hash_from_url


def _published(**overrides):
    philosophy_id = uuid.uuid4()
    image_url = "https://img.example.com/x.png?v=" + "a" * 64
    base = dict(
        id=uuid.uuid4(),
        status=ContentStatus.PUBLISHED,
        content_type=ContentType.DISEASE,
        title="치질 원인과 치료",
        body="본문 " * 400,
        published_at=datetime.now(timezone.utc),
        essence_status="ALIGNED",
        content_philosophy_id=philosophy_id,
        faq_question=None,
        faq_answer_summary=None,
        references_list=[{"title": "대한대장항문학회", "url": "https://www.colon.or.kr/"}],
        image_url=image_url,
        image_policy_verified_at=datetime.now(timezone.utc),
        image_content_hash=image_content_hash_from_url(image_url),
        image_subject_hash=image_subject_hash(ContentType.DISEASE, "치질 원인과 치료"),
        image_policy_version=IMAGE_POLICY_VERSION,
        essence_check_summary={},
        meta_description=None,
    )
    base.update(overrides)
    item = SimpleNamespace(**base)
    return item, philosophy_id


def test_fully_certified_published_item_is_visible():
    item, philosophy_id = _published()
    result = assess_public_visibility(item, philosophy_id)
    assert result.visible is True
    assert result.blockers == ()


def test_title_edit_that_invalidates_the_image_certificate_withholds_with_a_reason():
    item, philosophy_id = _published(image_policy_verified_at=None, image_content_hash=None)
    result = assess_public_visibility(item, philosophy_id)
    assert result.visible is False
    assert result.blockers == ("IMAGE_NOT_CERTIFIED",)
    assert VISIBILITY_BLOCKER_LABELS["IMAGE_NOT_CERTIFIED"] == "대표 이미지 재인증 대기"


def test_every_blocker_has_a_korean_label_and_a_stable_order():
    item, philosophy_id = _published(
        status=ContentStatus.PUBLISHED,
        title="   ",
        body="",
        published_at=None,
        essence_status="NEEDS_ESSENCE_REVIEW",
        references_list=[],
        image_url=None,
    )
    result = assess_public_visibility(item, uuid.uuid4())
    assert result.visible is False
    assert result.blockers[0] == "PHILOSOPHY_MISMATCH"
    assert set(result.blockers) <= set(VISIBILITY_BLOCKER_LABELS)


def test_unset_philosophy_means_no_philosophy_check():
    item, _ = _published()
    assert assess_public_visibility(item).visible is True
```

`image_content_hash_from_url`·`image_subject_hash`·`IMAGE_POLICY_VERSION`의 실제 import 경로는 `backend/app/services/content_publication.py` 상단 import를 보고 맞춘다(그 파일이 쓰는 것과 같은 곳에서 가져온다).

- [ ] **Step 2: 실패 확인**

Run: (환경 준비 명령) `tests/test_content_visibility.py -v` → `ModuleNotFoundError: app.services.content_visibility`.

- [ ] **Step 3: 판정 모듈 구현**

`backend/app/services/content_visibility.py`:

```python
"""공개 가시성 — 공개 사이트와 admin이 같은 답을 내는 유일한 판정.

`api/public/site.py`는 이 판정으로 글을 숨기고(fail-closed), admin은 같은 판정으로
"공개 중 / 공개 보류(사유)"를 표시한다. 두 곳이 각자 판정하면 admin은 초록인데 공개
페이지에는 없는 글이 생기고 운영자는 알 방법이 없다(H-01).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.models.content import ContentStatus
from app.services.content_publication import (
    PUBLICATION_CHECK_FIELDS,
    has_required_faq_fields,
    has_required_references,
    image_certification_current,
    public_candidate_review_safe,
    publication_field_values,
)
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED
from app.utils.medical_filter import check_forbidden_content_fields

_UNSET = object()

VISIBILITY_BLOCKER_LABELS: dict[str, str] = {
    "PHILOSOPHY_MISMATCH": "현재 승인된 콘텐츠 운영 기준과 다른 기준으로 생성됨",
    "STATUS_NOT_PUBLISHED": "발행 상태가 아님",
    "ESSENCE_NOT_ALIGNED": "콘텐츠 운영 기준 재검토 필요",
    "EMPTY_TITLE": "제목이 비어 있음",
    "EMPTY_BODY": "본문이 비어 있음",
    "NOT_PUBLISHED_AT": "발행 시각이 없음",
    "FAQ_FIELDS_MISSING": "FAQ 질문·직접 답변 누락",
    "MISSING_REFERENCES": "인용 가능한 참고 자료 없음",
    "IMAGE_NOT_CERTIFIED": "대표 이미지 재인증 대기",
    "AI_REVIEW_UNRESOLVED": "독립 검수 지적 미해결",
    "FORBIDDEN_EXPRESSION": "의료광고 금지 표현 포함",
}


@dataclass(frozen=True, slots=True)
class PublicVisibility:
    visible: bool
    blockers: tuple[str, ...]

    @property
    def blocker_labels(self) -> list[str]:
        return [VISIBILITY_BLOCKER_LABELS.get(code, code) for code in self.blockers]


def assess_public_visibility(
    item: Any,
    current_philosophy_id: uuid.UUID | None | object = _UNSET,
) -> PublicVisibility:
    """저장된 그대로의 글이 지금 공개 페이지에 나가도 되는가. 모든 차단 사유를 모은다."""
    blockers: list[str] = []
    if current_philosophy_id is not _UNSET and not (
        current_philosophy_id is not None
        and getattr(item, "content_philosophy_id", None) == current_philosophy_id
    ):
        blockers.append("PHILOSOPHY_MISMATCH")
    status = getattr(item, "status", None)
    if getattr(status, "value", status) != ContentStatus.PUBLISHED.value:
        blockers.append("STATUS_NOT_PUBLISHED")
    if getattr(item, "essence_status", None) != ESSENCE_STATUS_ALIGNED:
        blockers.append("ESSENCE_NOT_ALIGNED")
    if not (getattr(item, "title", None) or "").strip():
        blockers.append("EMPTY_TITLE")
    if not (getattr(item, "body", None) or "").strip():
        blockers.append("EMPTY_BODY")
    if getattr(item, "published_at", None) is None:
        blockers.append("NOT_PUBLISHED_AT")
    if not has_required_faq_fields(item):
        blockers.append("FAQ_FIELDS_MISSING")
    if not has_required_references(item):
        blockers.append("MISSING_REFERENCES")
    if not image_certification_current(item):
        blockers.append("IMAGE_NOT_CERTIFIED")
    if not public_candidate_review_safe(item):
        blockers.append("AI_REVIEW_UNRESOLVED")
    if check_forbidden_content_fields(publication_field_values(item), PUBLICATION_CHECK_FIELDS):
        blockers.append("FORBIDDEN_EXPRESSION")
    return PublicVisibility(visible=not blockers, blockers=tuple(blockers))
```

`ESSENCE_STATUS_ALIGNED`의 실제 정의 위치는 `grep -rn "ESSENCE_STATUS_ALIGNED =" backend/app`으로 확인해 import한다.

- [ ] **Step 4: 사이트 위임**

`site.py`의 `_is_public_safe_content` 본문을 교체(시그니처·`_CURRENT_PHILOSOPHY_UNSET` 호출 규약 유지):

```python
def _is_public_safe_content(
    item: ContentItem,
    current_philosophy_id: uuid.UUID | None | object = _CURRENT_PHILOSOPHY_UNSET,
) -> bool:
    visibility = assess_public_visibility(
        item,
        _VISIBILITY_UNSET if current_philosophy_id is _CURRENT_PHILOSOPHY_UNSET else current_philosophy_id,
    )
    if "FORBIDDEN_EXPRESSION" in visibility.blockers:
        logger.warning(
            "Public content withheld by the medical-ad filter: content_id=%s",
            getattr(item, "id", None),
        )
    return visibility.visible
```

`_VISIBILITY_UNSET`은 `content_visibility.py`에서 `_UNSET`을 공개 이름 `UNSET_PHILOSOPHY`로 export해 쓴다(모듈에 `UNSET_PHILOSOPHY = _UNSET` 추가). `_forbidden_content_violations`가 다른 곳에서 안 쓰이면 삭제(ruff가 알려준다).

- [ ] **Step 5: admin 직렬화**

`content.py`:
- `_build_compliance_summary(item, status_value)`의 blockers에 세 항목 추가(모두 순수 함수):
  - `if item.title and item.body and not has_required_faq_fields(item): blockers.append("FAQ 질문과 직접 답변 요약이 필요합니다.")`
  - `if item.title and item.body and not image_certification_current(item): blockers.append("대표 이미지 자동 정책 검사가 필요합니다.")`
  - `if not public_candidate_review_safe(item): blockers.append("독립 검수 지적이 해결되지 않았습니다.")`
- `_serialize_item(item, full=False, *, public_philosophy_id=UNSET_PHILOSOPHY)` 시그니처로 바꾸고, `compliance` dict에 `"public_visibility": {"visible": v.visible, "blockers": list(v.blockers), "blocker_labels": v.blocker_labels}` (v = `assess_public_visibility(item, public_philosophy_id)`)를 넣는다. 공개 글이 아닐 때도 계산하되 `STATUS_NOT_PUBLISHED`가 들어가는 것은 정상이다.
- `_content_review_display`의 PUBLISHED 분기 맨 앞에: 가시성 결과를 받아 `visible`이 아니면 `{"label": "공개 보류", "reason": ", ".join(labels), "publishable": False}`를 돌려준다. 이를 위해 `_serialize_item_display(item, content_type, status_value, visibility)`로 인자를 하나 늘리고 `_serialize_item`이 넘긴다.
- 목록·상세·월 표 엔드포인트(`_serialize_item`을 호출하는 모든 라우트: `grep -n "_serialize_item(" content.py`)에서 `public_philosophy_id = await get_public_approved_philosophy_id(db, hospital_id)`를 **한 번** 계산해 전달한다(import: `from app.services.essence_readiness import get_public_approved_philosophy_id`).

- [ ] **Step 6: admin 화면**

`admin/types/index.ts`의 `ContentItem.compliance` 타입에 `public_visibility?: { visible: boolean; blockers: string[]; blocker_labels: string[] }` 추가.

`content/page.tsx` `getReviewState`: PUBLISHED 분기 맨 앞에

```ts
    const visibility = item.compliance?.public_visibility
    if (visibility && !visibility.visible) {
      return {
        key: 'withheld',
        label: '공개 보류',
        badge: 'bg-amber-100 text-amber-800',
        reason: visibility.blocker_labels.join(' · '),
        publishable: false,
      }
    }
```

`ReviewState['key']` 유니온에 `'withheld'` 추가. 공개 링크(≈1709)는 `selected.status === 'PUBLISHED' && selectedPublicUrl && selected.compliance?.public_visibility?.visible !== false`일 때만 렌더하고, 보류면 대신 `<span className="text-sm text-amber-800">공개 페이지에서 보류 중 — {reason}</span>`를 보여준다.

- [ ] **Step 7: 통과 확인**

Run: (환경 준비 명령) `tests/test_content_visibility.py tests/test_content_publication*.py tests/test_public_site*.py tests/test_admin_content*.py -v` (glob이 안 맞으면 `ls tests | grep -E "public_site|admin_content|content_publication"`으로 실제 파일명을 쓴다) → PASS. `cd admin && npm test && npm run lint && npm run typecheck` → 초록.

- [ ] **Step 8: 커밋**

```bash
cd /Users/woojinlee/Documents/projects/reputation && git add backend/app/services/content_visibility.py backend/tests/test_content_visibility.py backend/app/api/public/site.py backend/app/api/admin/content.py admin/types/index.ts 'admin/app/hospitals/[id]/content/page.tsx' && git commit -m "fix: one public-visibility judgment shared by the site and admin

Admin now shows 공개 보류 with the same blockers the public site applies
instead of a green 공개 완료 for a withheld article. (H-01 display, C-02)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: 공개 글 편집이 지운 이미지 인증을 시스템이 복구한다 (H-01 복구 부분)

**Files:**
- Modify: `backend/app/workers/nightly_generation_batch.py` — `write_back_published_image_certificate` 추가
- Modify: `backend/app/workers/tasks.py` — `recertify_published_content_image` task 추가 (`regenerate_content_item` 바로 아래)
- Modify: `backend/app/core/celery_app.py:167` 부근 task_routes에 `"app.workers.tasks.recertify_published_content_image": {"queue": "content"}`
- Modify: `backend/app/api/admin/content.py` PATCH(`@router.patch("/{hospital_id}/content/{content_id}")` 아래 함수, ≈560-620): 인증이 지워졌으면 IndexNow 대신 재인증 dispatch
- Test: `backend/tests/integration/test_published_image_recertification_postgres.py` (신규)

- [ ] **Step 1: 통합 테스트 작성**

```python
"""H-01: 공개 글 제목 편집으로 무효화된 이미지 인증을 시스템이 재검수해 복구한다."""

import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus
from app.services.content_publication import IMAGE_POLICY_VERSION, image_certification_current, image_subject_hash
from app.services.image_engine import image_content_hash_from_url
from app.services.image_policy import ImagePolicyRejectedError
from app.workers import tasks as worker_tasks
from app.workers.nightly_generation_batch import write_back_published_image_certificate


@pytest.fixture
def pg_session(pg_conn):
    session = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


def _seed_published(pg_session, *, certified: bool):
    label = uuid.uuid4().hex[:8]
    hospital = Hospital(id=uuid.uuid4(), name=f"재인증 {label}", slug=f"recert-{label}", status=HospitalStatus.ACTIVE, site_live=True)
    image_url = f"https://img.example.com/{label}.png?v=" + "b" * 64
    item = ContentItem(
        id=uuid.uuid4(), hospital_id=hospital.id, content_type=ContentType.DISEASE, sequence_no=1, total_count=12,
        title="새 제목", body="본문 " * 400, scheduled_date=date.today(), status=ContentStatus.PUBLISHED,
        published_at=datetime.now(timezone.utc), published_by="ae@example.com", essence_status="ALIGNED",
        image_url=image_url, content_revision=3,
        image_policy_verified_at=datetime.now(timezone.utc) if certified else None,
        image_content_hash=image_content_hash_from_url(image_url) if certified else None,
        image_subject_hash=image_subject_hash(ContentType.DISEASE, "새 제목") if certified else None,
        image_policy_version=IMAGE_POLICY_VERSION if certified else None,
    )
    pg_session.add_all([hospital, item])
    pg_session.commit()
    return hospital, item


def test_write_back_only_touches_the_published_row_at_the_expected_revision(pg_session):
    hospital, item = _seed_published(pg_session, certified=False)
    values = {
        "image_content_hash": image_content_hash_from_url(item.image_url),
        "image_subject_hash": image_subject_hash(ContentType.DISEASE, "새 제목"),
        "image_policy_version": IMAGE_POLICY_VERSION,
        "image_policy_verified_at": datetime.now(timezone.utc),
    }
    assert write_back_published_image_certificate(pg_session, item_id=item.id, expected_title="새 제목", expected_revision=3, values=values) == 1
    assert write_back_published_image_certificate(pg_session, item_id=item.id, expected_title="다른 제목", expected_revision=3, values=values) == 0
    assert write_back_published_image_certificate(pg_session, item_id=item.id, expected_title="새 제목", expected_revision=99, values=values) == 0
    pg_session.commit()
    pg_session.refresh(item)
    assert item.content_revision == 3  # 재인증은 판을 바꾸지 않는다
    assert image_certification_current(item)


def test_task_recertifies_stored_bytes_and_leaves_status_published(pg_session, monkeypatch):
    hospital, item = _seed_published(pg_session, certified=False)

    async def _certify(image_url, *, content_type, topic, hospital_id=None):
        return image_content_hash_from_url(image_url), image_subject_hash(content_type, topic)

    monkeypatch.setattr(worker_tasks, "certify_existing_image", _certify)
    monkeypatch.setattr(worker_tasks, "SyncSessionLocal", lambda: _SessionProxy(pg_session))
    recorded: list[str] = []
    monkeypatch.setattr(worker_tasks, "_run_async", lambda coro: __import__("asyncio").run(coro))
    monkeypatch.setattr(worker_tasks.indexnow, "enqueue_content_published_sync", lambda *a, **k: recorded.append("indexnow"), raising=False)

    worker_tasks.recertify_published_content_image.run(str(item.id))

    pg_session.refresh(item)
    assert item.status == ContentStatus.PUBLISHED
    assert image_certification_current(item)


def test_task_marks_rejection_as_operator_incident_without_touching_the_row(pg_session, monkeypatch):
    hospital, item = _seed_published(pg_session, certified=False)

    async def _reject(image_url, *, content_type, topic, hospital_id=None):
        raise ImagePolicyRejectedError("subject mismatch")

    monkeypatch.setattr(worker_tasks, "certify_existing_image", _reject)
    monkeypatch.setattr(worker_tasks, "SyncSessionLocal", lambda: _SessionProxy(pg_session))
    monkeypatch.setattr(worker_tasks, "_run_async", lambda coro: __import__("asyncio").run(coro))
    finished: list[tuple] = []
    monkeypatch.setattr(worker_tasks, "finish_explicit_run", lambda db, task, item_id, state, **kw: finished.append((state, kw.get("safe_error_code"))))

    worker_tasks.recertify_published_content_image.run(str(item.id))

    pg_session.refresh(item)
    assert not image_certification_current(item)
    assert item.status == ContentStatus.PUBLISHED
    assert finished and finished[-1][1] == "PUBLISHED_IMAGE_RECERTIFY_REJECTED"


class _SessionProxy:
    """`with SyncSessionLocal() as db:` 문법을 테스트 세션에 그대로 붙인다."""

    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *exc):
        return False
```

기존 통합 테스트(`tests/integration/test_generation_write_back_guard.py`)가 worker task를 어떻게 실행·격리하는지(`SyncSessionLocal` 패치 방식, `_run_async` 패치 여부, `explicit_run_context` 처리)를 먼저 읽고, 위 monkeypatch 대상 이름을 그 파일과 **동일한 것**으로 맞춘다. `indexnow`에 sync 함수가 없으면 task 안에서 `_run_async(indexnow.enqueue_content_published(...))`를 쓰고 테스트는 `enqueue_content_published`를 패치한다. `finish_explicit_run`의 실제 시그니처(`regenerate_content_item`에서 쓰는 형태)에 맞춰 람다 인자를 조정한다.

- [ ] **Step 2: 실패 확인**

Run: (환경 준비 명령) `tests/integration/test_published_image_recertification_postgres.py -v` → `ImportError` (write_back 함수·task 없음).

- [ ] **Step 3: write-back 구현**

`nightly_generation_batch.py`에 `write_back_generated_image` 바로 아래:

```python
def write_back_published_image_certificate(
    db,
    *,
    item_id,
    expected_title: str | None,
    expected_revision: int,
    values: dict[str, Any],
) -> int:
    """공개 중인 글의 이미지 인증만 갱신한다. 판(content_revision)·claim은 건드리지 않는다.

    `write_back_generated_image`는 생성 상태(DRAFT/REJECTED/READY)만 대상이라 공개 글에는
    0행을 돌려준다. 공개 글의 재인증은 상태·제목·판이 그대로일 때만 저장한다 — 그 사이
    편집이 있었다면 그 편집이 다시 재인증을 요청한다.
    """
    result = db.execute(
        update(ContentItem)
        .where(
            ContentItem.id == item_id,
            ContentItem.status == ContentStatus.PUBLISHED,
            ContentItem.title == expected_title,
            ContentItem.content_revision == expected_revision,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount
```

- [ ] **Step 4: task 구현**

`tasks.py`의 `regenerate_content_item` 바로 아래:

```python
@celery_app.task(name="app.workers.tasks.recertify_published_content_image", bind=True, max_retries=2)
def recertify_published_content_image(self, content_id: str):
    """공개 글의 제목 편집이 지운 이미지 인증을 저장된 바이트 재검수로 복구한다 (H-01).

    성공하면 공개 페이지가 다시 글을 내보내므로 IndexNow와 사이트 캐시를 갱신한다.
    정책 거절은 사람의 결정(이미지 교체 또는 제목 되돌리기)이 필요하므로 FAILED로 끝내
    인시던트가 되게 한다. 공개 글의 이미지를 임의로 새로 생성하지 않는다.
    """
    item_id = uuid.UUID(content_id)
    if explicit_run_context(self) is None:
        require_dispatch(self, "recertify-published-image", str(item_id))
    with SyncSessionLocal() as db:
        item = db.get(ContentItem, item_id)
        if not item or item.status != ContentStatus.PUBLISHED:
            finish_explicit_run(db, self, item_id, OperationRunState.CANCELLED)
            return
        if explicit_run_context(self) is not None and not explicit_run_matches(db, self, item_id, item.hospital_id):
            raise PermissionError("operation run does not authorize this content target")
        from app.services.content_publication import image_certification_current

        if image_certification_current(item):
            finish_explicit_run(db, self, item_id, OperationRunState.SUCCEEDED)
            return
        hospital = db.get(Hospital, item.hospital_id)
        if not hospital or not item.image_url:
            finish_explicit_run(
                db, self, item_id, OperationRunState.FAILED,
                safe_error_code="PUBLISHED_IMAGE_MISSING",
                safe_error_message="공개 글에 대표 이미지가 없어 재인증할 수 없습니다. 이미지를 등록해 주세요.",
            )
            return
        expected_title = item.title
        expected_revision = int(getattr(item, "content_revision", 1) or 1)
        try:
            content_hash, subject_hash = _run_async(
                certify_existing_image(
                    item.image_url, content_type=item.content_type, topic=expected_title, hospital_id=hospital.id,
                )
            )
        except ImagePolicyRejectedError:
            finish_explicit_run(
                db, self, item_id, OperationRunState.FAILED,
                safe_error_code="PUBLISHED_IMAGE_RECERTIFY_REJECTED",
                safe_error_message="제목이 바뀌어 대표 이미지가 글 주제와 맞지 않습니다. 이미지를 교체하거나 제목을 되돌려 주세요.",
            )
            return
        written = write_back_published_image_certificate(
            db, item_id=item.id, expected_title=expected_title, expected_revision=expected_revision,
            values={
                "image_content_hash": content_hash,
                "image_subject_hash": subject_hash,
                "image_policy_version": IMAGE_POLICY_VERSION,
                "image_policy_verified_at": datetime.now(timezone.utc),
            },
        )
        if written == 0:
            db.rollback()
            finish_explicit_run(db, self, item_id, OperationRunState.CANCELLED)
            return
        db.commit()
        db.refresh(item)
        finish_explicit_run(db, self, item_id, OperationRunState.SUCCEEDED)
    _run_async(
        indexnow.enqueue_content_published_standalone(
            slug=hospital.slug, content_id=item.id, aeo_domain=hospital.aeo_domain,
            treatments=hospital.treatments, revision=expected_revision,
        )
    ) if hasattr(indexnow, "enqueue_content_published_standalone") else None
    _run_async(
        trigger_content_site_revalidate_safe(hospital.slug, item.id, hospital_name=hospital.name, treatments=hospital.treatments)
    )
```

IndexNow 큐잉: `indexnow.enqueue_content_published(db, ...)`는 async+db 세션이 필요하다. 위 `hasattr` 삼항은 쓰지 말고, 이 task 안에서 실제로 존재하는 호출 방식을 쓴다 — 다른 worker task가 IndexNow를 큐잉하는 곳(`grep -n "enqueue_content_published" backend/app/workers/tasks.py`)과 **같은 방식**으로 세션 안에서 호출한 뒤 commit한다. import 필요: `ImagePolicyRejectedError`(`app.services.image_policy`), `certify_existing_image`(`app.services.image_engine`), `write_back_published_image_certificate`, `IMAGE_POLICY_VERSION`(이미 tasks.py에 있으면 생략), `trigger_content_site_revalidate_safe`. `require_dispatch` 목적 문자열 `"recertify-published-image"`는 `app/workers/dispatch_auth.py`의 허용 목적 목록에 등록해야 하면(다른 목적이 목록에 있는지 `grep -n "regenerate-content" backend/app/workers/dispatch_auth.py`로 확인) 같은 방식으로 추가한다.

`celery_app.py` task_routes에 `"app.workers.tasks.recertify_published_content_image": {"queue": "content"},` 추가.

- [ ] **Step 5: PATCH가 재인증을 dispatch한다**

`content.py` PATCH 함수에서 인증을 지우는 블록 뒤(`item.image_policy_version = None` 다음)에 플래그 `certificate_invalidated = True`를 두고(블록 밖 기본값 False), IndexNow enqueue 조건을 `if was_published and public_fields_changed and not certificate_invalidated and isinstance(item, ContentItem):`로 바꾼다(인증이 지워졌으면 지금은 404가 될 URL이므로 재인증 성공 후 task가 제출한다). `await db.commit()` 뒤에:

```python
    if was_published and certificate_invalidated and isinstance(item, ContentItem):
        try:
            await dispatch_operation(
                db,
                OperationCommand(
                    operation_type="RECERTIFY_PUBLISHED_IMAGE",
                    hospital_id=hospital.id,
                    requested_by_id=None,
                    idempotency_key=f"recertify:{item.id}:{int(item.content_revision or 1)}",
                    audit_actor=default_actor(),
                    target_type="content_item",
                    target_id=str(item.id),
                    queue="content",
                    task_args=(str(item.id),),
                ),
                recertify_published_content_image,
            )
        except OperationQueueUnavailable:
            logger.warning("Published image recertification enqueue failed for %s", item.id)
```

import는 `hospitals.py:87-90`과 같은 곳(`app.services.operation_runs`)에서 `OperationCommand, OperationQueueUnavailable, dispatch_operation`을, task는 `app.workers.tasks`에서 가져온다(content.py가 이미 tasks를 import하는 방식을 따른다; 순환 import면 함수 내부 지역 import).

- [ ] **Step 6: 통과 확인**

Run: (환경 준비 명령) `tests/integration/test_published_image_recertification_postgres.py tests/test_celery_routing.py tests/test_admin_content*.py -v` → PASS. `ruff check .` clean. `tests/test_celery_routing.py`가 task 목록을 고정하고 있으면 새 task를 그 기대 목록에 추가한다.

- [ ] **Step 7: 커밋**

```bash
cd /Users/woojinlee/Documents/projects/reputation && git add backend/app/workers/nightly_generation_batch.py backend/app/workers/tasks.py backend/app/core/celery_app.py backend/app/api/admin/content.py backend/app/workers/dispatch_auth.py backend/tests/integration/test_published_image_recertification_postgres.py backend/tests/test_celery_routing.py && git commit -m "fix: recertify a published article's image after a title edit

Editing a published title cleared the image certificate, the site withheld
the article, and nothing recertified it. A worker task now re-reviews the
stored bytes and restores the certificate, or fails into an incident. (H-01)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

(`dispatch_auth.py`·`test_celery_routing.py`는 수정했을 때만 add.)

---

### Task 3: 생성 재시도 예산이 날짜에 따라 리셋되지 않는다 (H-08)

**Files:**
- Modify: `backend/app/workers/tasks.py:391-400` (`_generation_attempt_context`)
- Test: `_generation_attempt_context`를 테스트하는 기존 파일(`grep -rln "_generation_attempt_context\|_remember_generation_attempt" backend/tests`)에 추가; 없으면 `backend/tests/test_generation_attempt_context.py` 신규

- [ ] **Step 1: 테스트 작성**

```python
from types import SimpleNamespace
from datetime import date

from app.workers.tasks import _generation_attempt_context


def test_rescheduling_a_stranded_slot_does_not_reset_the_attempt_budget():
    """H-08: 22:30 복구가 scheduled_date를 매일 다시 쓰므로 날짜는 fingerprint가 아니다."""
    philosophy = SimpleNamespace(id="phil-1")
    before = SimpleNamespace(content_type=SimpleNamespace(value="DISEASE"), scheduled_date=date(2026, 9, 8), query_target_id="q-1")
    after = SimpleNamespace(content_type=SimpleNamespace(value="DISEASE"), scheduled_date=date(2026, 9, 9), query_target_id="q-1")
    assert _generation_attempt_context(before, philosophy) == _generation_attempt_context(after, philosophy)


def test_changed_essence_or_target_still_grants_a_fresh_attempt():
    item = SimpleNamespace(content_type=SimpleNamespace(value="DISEASE"), scheduled_date=date(2026, 9, 8), query_target_id="q-1")
    assert _generation_attempt_context(item, SimpleNamespace(id="a")) != _generation_attempt_context(item, SimpleNamespace(id="b"))
    other_target = SimpleNamespace(content_type=SimpleNamespace(value="DISEASE"), scheduled_date=date(2026, 9, 8), query_target_id="q-2")
    assert _generation_attempt_context(item, SimpleNamespace(id="a")) != _generation_attempt_context(other_target, SimpleNamespace(id="a"))
```

- [ ] **Step 2: 실패 확인** → 첫 테스트 FAIL(문자열에 날짜 포함).

- [ ] **Step 3: 구현**

```python
def _generation_attempt_context(
    item: ContentItem, philosophy: HospitalContentPhilosophy | None
) -> str:
    """Fingerprint inputs whose change can justify one more body attempt.

    scheduled_date는 넣지 않는다 — 22:30 복구가 미발행 슬롯의 날짜를 매일 다시 쓰므로
    날짜를 넣으면 결정적으로 실패하는 글이 하루 4회씩 유료 호출을 영원히 반복한다(H-08).
    """
    philosophy_id = str(getattr(philosophy, "id", "") or "MISSING")
    content_type = str(getattr(getattr(item, "content_type", None), "value", "") or "")
    query_target_id = str(getattr(item, "query_target_id", "") or "")
    return f"philosophy={philosophy_id};content_type={content_type};query_target={query_target_id}"
```

- [ ] **Step 4: 통과 확인** — 위 테스트 + `tests/test_generation_*` + `tests/test_content_calendar.py` → PASS. 저장된 예전 fingerprint(날짜 포함)는 새 fingerprint와 달라 **한 번** 새 시도가 허용되는데, 이는 의도된 1회 리셋이다 — 커밋 메시지에 적는다.

- [ ] **Step 5: 커밋**

```bash
cd /Users/woojinlee/Documents/projects/reputation && git add backend/app/workers/tasks.py backend/tests && git commit -m "fix: keep the generation attempt budget across daily rescheduling

scheduled_date was part of the attempt fingerprint, so the 22:30 stranded-slot
recovery reset the 4-attempt provider budget every day. Existing stored
fingerprints get exactly one fresh attempt after this change. (H-08)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

(`git add backend/tests`는 새 테스트 파일만 추가되도록 `git status`로 확인한 뒤 실행한다.)

---

### Task 4: 수동 발행은 병원 게이트·병원 lock·검증된 actor를 쓴다 (H-09)

**Files:**
- Modify: `backend/app/api/admin/content.py` — `PublishBody`(≈173), `publish_content`(≈771-900)
- Modify: `backend/app/services/content_publication.py:168-175` (`assess_content_publication` 시작)
- Modify: `admin/app/hospitals/[id]/content/page.tsx:557` (publish 호출 본문)
- Test: `publish_content(`를 테스트하는 기존 파일(`grep -rln "publish_content(" backend/tests`)에 추가

- [ ] **Step 1: 테스트 작성** (기존 파일의 fake DB·hospital·item 픽스처를 그대로 사용; 아래 이름은 그 파일의 것으로 맞춘다)

```python
from app.services.audit_log import reset_request_actor, set_request_actor


async def test_manual_publish_refuses_a_hospital_that_is_not_publicly_serving():
    hospital = _hospital(status=HospitalStatus.PAUSED, site_live=True, schedule_set=True)
    item = _publishable_item(hospital)
    db = _PublishDB(hospital, item)
    token = set_request_actor("ae@example.com")
    try:
        with pytest.raises(HTTPException) as exc:
            await content_api.publish_content(hospital.id, item.id, content_api.PublishBody(), db=db)
    finally:
        reset_request_actor(token)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "HOSPITAL_NOT_PUBLIC"
    assert item.status != ContentStatus.PUBLISHED


async def test_manual_publish_refuses_without_a_schedule():
    hospital = _hospital(status=HospitalStatus.ACTIVE, site_live=True, schedule_set=False)
    item = _publishable_item(hospital)
    db = _PublishDB(hospital, item)
    token = set_request_actor("ae@example.com")
    try:
        with pytest.raises(HTTPException) as exc:
            await content_api.publish_content(hospital.id, item.id, content_api.PublishBody(), db=db)
    finally:
        reset_request_actor(token)
    assert exc.value.detail["code"] == "SCHEDULE_NOT_SET"


async def test_manual_publish_records_the_verified_actor_not_the_request_body():
    hospital = _hospital(status=HospitalStatus.ACTIVE, site_live=True, schedule_set=True)
    item = _publishable_item(hospital)
    db = _PublishDB(hospital, item)
    token = set_request_actor("ae@example.com")
    try:
        await content_api.publish_content(hospital.id, item.id, content_api.PublishBody(), db=db)
    finally:
        reset_request_actor(token)
    assert item.published_by == "ae@example.com"
    assert item.first_published_by == "ae@example.com"


async def test_manual_publish_requires_a_verified_actor():
    hospital = _hospital(status=HospitalStatus.ACTIVE, site_live=True, schedule_set=True)
    item = _publishable_item(hospital)
    db = _PublishDB(hospital, item)
    token = set_request_actor(None)
    try:
        with pytest.raises(HTTPException) as exc:
            await content_api.publish_content(hospital.id, item.id, content_api.PublishBody(), db=db)
    finally:
        reset_request_actor(token)
    assert exc.value.status_code == 403


def test_whitespace_only_title_or_body_is_not_generated():
    from app.services.content_publication import assess_content_publication
    item = _publishable_item(_hospital())
    item.title = "   "
    assert assess_content_publication(item, None).code == "CONTENT_NOT_GENERATED"
```

`_publishable_item(hospital)`은 그 파일에 있는 "발행 가능한 항목" 픽스처 이름으로 바꾼다(없으면 그 파일의 성공 발행 테스트가 만드는 item 생성 코드를 함수로 추출한다).

- [ ] **Step 2: 실패 확인** → 5개 모두 FAIL.

- [ ] **Step 3: 구현**

`PublishBody`: `published_by` 필드와 validator를 삭제하고 빈 모델로 둔다(`class PublishBody(BaseModel): pass` — 향후 필드용). `publish_content`:

```python
    publisher = verified_request_actor()
    if publisher is None:
        raise HTTPException(status_code=403, detail="발행자의 로그인 계정을 확인할 수 없습니다. 다시 로그인해 주세요.")
    if hasattr(db, "execute"):
        await acquire_hospital_advisory_lock(db, hospital_id)
    item = await _get_content(db, content_id, hospital_id)
    hospital = await _get_hospital(db, hospital_id)
    if not (hospital.status == HospitalStatus.ACTIVE and bool(hospital.site_live)):
        raise HTTPException(status_code=409, detail={"code": "HOSPITAL_NOT_PUBLIC", "message": "공개 운영 중인 병원만 발행할 수 있습니다. 일시정지 상태면 먼저 재개해 주세요."})
    if not hospital.schedule_set:
        raise HTTPException(status_code=409, detail={"code": "SCHEDULE_NOT_SET", "message": "발행 일정이 설정된 병원만 발행할 수 있습니다."})
```

이후 `record_publication_identity(..., published_by=publisher)`, 감사 로그 `"claimed_by": publisher`. import: `from app.services.audit_log import verified_request_actor`, `from app.utils.db_locks import acquire_hospital_advisory_lock`, `HospitalStatus`.

`assess_content_publication` 첫 조건: `if not (item.title or "").strip() or not (item.body or "").strip():`.

admin `content/page.tsx:557` publish 호출: `body: JSON.stringify({})`로 바꾸고 `published_by` 관련 state/입력이 있으면 제거한다(`grep -n "published_by\|publishedBy" content/page.tsx`).

- [ ] **Step 4: 통과 확인** — 해당 테스트 파일 + `tests/test_content_publication*.py` + admin `npm test/lint/typecheck` → 초록. 기존 발행 테스트가 `PublishBody(published_by=...)`를 쓰면 `PublishBody()`로, actor를 설정하지 않아 403이 나면 `set_request_actor(...)`를 추가한다(테스트 의도는 유지).

- [ ] **Step 5: 커밋**

```bash
cd /Users/woojinlee/Documents/projects/reputation && git add backend/app/api/admin/content.py backend/app/services/content_publication.py backend/tests 'admin/app/hospitals/[id]/content/page.tsx' && git commit -m "fix: manual publish enforces the hospital gate and records the verified actor

Manual recovery publishing skipped the ACTIVE/site_live/schedule gate the
nightly path enforces, accepted whitespace-only text, and took published_by
from the request body. (H-09)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: 요금제의 권위는 계약 기록이다 (H-14)

**Files:**
- Modify: `backend/app/api/admin/content.py` `set_schedule`(≈195-330): `hospital.plan = Plan(body.plan)` 삭제, 불일치 409
- Modify: `admin/app/hospitals/[id]/schedule/page.tsx` (≈286-300 select → 읽기 전용)
- Test: `set_schedule(`를 테스트하는 기존 파일(`grep -rln "set_schedule(" backend/tests`)에 추가; `admin/lib/schedule-page-r2.test.ts`

- [ ] **Step 1: 테스트 작성**

```python
async def test_schedule_cannot_change_the_contracted_plan():
    """H-14: 일정 저장이 병원 요금제를 바꾸면 계약 기록과 가격이 어긋난다."""
    hospital = _hospital(plan=Plan.PLAN_12, schedule_set=False)
    db = _ScheduleDB(hospital)
    body = content_api.ScheduleCreate(plan="PLAN_20", publish_days=[0, 2, 4], active_from=_tomorrow())
    with pytest.raises(HTTPException) as exc:
        await content_api.set_schedule(hospital.id, body, db=db)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "PLAN_MISMATCH"
    assert hospital.plan == Plan.PLAN_12


async def test_schedule_with_the_contracted_plan_leaves_plan_untouched():
    hospital = _hospital(plan=Plan.PLAN_12, schedule_set=False)
    db = _ScheduleDB(hospital)
    body = content_api.ScheduleCreate(plan="PLAN_12", publish_days=[0, 2, 4], active_from=_tomorrow())
    await content_api.set_schedule(hospital.id, body, db=db)
    assert hospital.plan == Plan.PLAN_12
    assert hospital.schedule_set is True
```

(`_ScheduleDB`, `_hospital`, `_tomorrow`는 그 테스트 파일의 픽스처 이름으로 맞춘다. 파일이 readiness blocker를 어떻게 통과시키는지 — `_schedule_readiness_blockers` 패치 — 그대로 따른다.)

`admin/lib/schedule-page-r2.test.ts`에 소스 계약 테스트 추가:

```ts
test('the schedule page never offers a plan selector — the contract owns the plan', () => {
  assert.doesNotMatch(schedulePageSource, /<select[^>]*id="schedule-plan"/)
  assert.match(schedulePageSource, /요금제는 계약 기록에서만 변경/)
})
```

(`schedulePageSource`는 그 파일이 이미 페이지 소스를 읽고 있으면 그 변수; 없으면 `readFileSync(new URL('../app/hospitals/[id]/schedule/page.tsx', import.meta.url), 'utf8')`.)

- [ ] **Step 2: 실패 확인** → 첫 파이썬 테스트 FAIL(`hospital.plan == PLAN_20`), TS 테스트 FAIL.

- [ ] **Step 3: 구현**

`set_schedule`에서 `_get_hospital_for_schedule_update` 직후:

```python
    authoritative_plan = hospital.plan.value if hasattr(hospital.plan, "value") else hospital.plan
    if authoritative_plan and body.plan != authoritative_plan:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PLAN_MISMATCH",
                "message": "요금제는 계약 기록(인수 정정)에서만 변경할 수 있습니다. 일정은 현재 계약 요금제로 저장해 주세요.",
                "contracted_plan": authoritative_plan,
            },
        )
```

`hospital.plan = Plan(body.plan)` 줄과 그 주석을 삭제한다. `hospital.plan`이 비어 있는 레거시 병원은 body.plan을 그대로 쓰되 여전히 `hospital.plan`을 쓰지 않는다(계약 정정 경로가 채운다).

`schedule/page.tsx`: `<select id="schedule-plan">` 블록을 읽기 전용으로 교체 —

```tsx
          <div>
            <p className="block text-sm font-medium text-slate-700 mb-2">요금제</p>
            <p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-800">
              {plan ? PLAN_CONTRACT_LABELS[plan] ?? plan : '계약 요금제를 불러오는 중입니다.'}
            </p>
            <p className="mt-1 text-[11px] text-slate-500">요금제는 계약 기록에서만 변경할 수 있습니다. 발행 요일과 시작일만 여기서 정합니다.</p>
          </div>
```

`plan` state의 초기값은 병원 헤더 컨텍스트(`HospitalHeaderContext`, `admin/app/hospitals/[id]/hospital-context.tsx`)의 `hospital.plan`으로 채우고, `setSelectedDays(DEFAULT_PUBLISH_DAYS_BY_PLAN[plan])`를 plan이 결정될 때 한 번 실행한다. 제출 본문은 그대로 `{ plan, publish_days, active_from }`.

- [ ] **Step 4: 통과 확인** — 해당 파이썬 테스트 파일 + `tests/test_content_calendar.py` + admin `npm test/lint/typecheck` → 초록.

- [ ] **Step 5: 커밋**

```bash
cd /Users/woojinlee/Documents/projects/reputation && git add backend/app/api/admin/content.py backend/tests 'admin/app/hospitals/[id]/schedule/page.tsx' admin/lib/schedule-page-r2.test.ts && git commit -m "fix: the contract owns the plan; schedule setup can no longer change it (H-14)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: admin 소소한 오도 제거 (M-15, M-21, M-22 일부)

**Files:**
- Modify: `backend/app/api/admin/content.py` `_serialize_item` — `post_publish_review_required` 추가
- Modify: `admin/types/index.ts`, `admin/app/hospitals/[id]/content/page.tsx:1283` (brief 편집 조건), 공개 후 확인 버튼(≈1796-1803), `admin/app/hospitals/[id]/schedule/page.tsx:70` 타입, `admin/types/index.ts:282` 부근 `AIQueryTarget`에 `in_tracking_set`
- Test: `admin/lib/publishing.test.ts` 또는 신규 `admin/lib/post-publish-review.test.ts`

- [ ] **Step 1: 백엔드 — 표본 여부 직렬화**

`_serialize_item`의 dict에 추가:

```python
        # 공개 후 확인은 표본(첫 순번 또는 공개 후 본문 편집)만 사람이 본다. 전체 글에
        # 확인 버튼을 띄우면 월 12~20번의 불필요한 클릭이 생긴다(M-21).
        "post_publish_review_required": bool(
            is_human_post_publish_review_sample(item) and getattr(item, "post_publish_reviewed_at", None) is None
        ),
```

import: `from app.services.post_publish_review_policy import is_human_post_publish_review_sample`. `ContentItemResponse`(`schemas/content.py`)에 `post_publish_review_required: bool = False` 추가.

- [ ] **Step 2: admin**

- `admin/types/index.ts` `ContentItem`에 `post_publish_review_required?: boolean`, `AIQueryTarget`에 `in_tracking_set?: boolean`.
- `content/page.tsx`: 공개 후 확인 버튼(`문제 없음 · 공개 내용 확인 완료`)을 `selected.post_publish_review_required === true`일 때만 렌더. PUBLISHED이고 확인 대상이 아니면 `getReviewState`가 `{ key: 'published', label: '공개 중', badge: 'bg-green-100 text-green-700', publishable: false }`를 돌려준다(기존 '공개 내용 확인 대기' 라벨은 `post_publish_review_required`가 true일 때만).
- `content/page.tsx:1283`: `['DRAFT', 'PUBLISHED'].includes(selected.status)` → `selected.status === 'DRAFT'` (공개 글 brief 편집은 서버가 항상 409).
- `schedule/page.tsx:70`: `first_publish_date: string | null`; 표시 시 null이면 '첫 발행일 미정'.

`admin/lib/post-publish-review.test.ts`(신규, 소스 계약):

```ts
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const page = readFileSync(new URL('../app/hospitals/[id]/content/page.tsx', import.meta.url), 'utf8')

test('the post-publish confirm button is gated on the server sample flag', () => {
  assert.match(page, /post_publish_review_required === true/)
})

test('brief editing is never offered for published items', () => {
  assert.doesNotMatch(page, /\['DRAFT', 'PUBLISHED'\]\.includes\(selected\.status\)/)
})
```

- [ ] **Step 3: 통과 확인** — backend `tests/test_admin_content*.py`, admin `npm test/lint/typecheck` → 초록.

- [ ] **Step 4: 커밋**

```bash
cd /Users/woojinlee/Documents/projects/reputation && git add backend/app/api/admin/content.py backend/app/schemas/content.py admin/types/index.ts 'admin/app/hospitals/[id]/content/page.tsx' 'admin/app/hospitals/[id]/schedule/page.tsx' admin/lib/post-publish-review.test.ts && git commit -m "fix(admin): confirm only sampled articles, hide impossible brief edits, fix nullable types (M-15, M-21, M-22)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: 전체 검증

- [ ] 5432 테스트 DB를 새로 만든다(부분 실행 잔여 행 제거): `docker exec reputation-ci-pg5432 psql -U postgres -d postgres -c "DROP DATABASE IF EXISTS reputation_test WITH (FORCE)" -c "CREATE DATABASE reputation_test"` 뒤 `alembic upgrade head`.
- [ ] Backend 전체(환경 준비 명령) → `0 failed`. ruff clean.
- [ ] `make test-frontend` → admin·site `ℹ fail 0`, lint·typecheck 0.
- [ ] `grep -rn "published_by" 'admin/app/hospitals/[id]/content/page.tsx'` → 결과 없음. `grep -n "hospital.plan = " backend/app/api/admin/content.py` → 결과 없음.
- [ ] 완료 보고: Task별 테스트 수·결과, 바뀐 파일, 새 task 라우팅 확인 로그. push하지 않는다.
