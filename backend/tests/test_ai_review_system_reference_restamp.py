"""시스템이 참고자료를 고쳐도(발행 시 치유·제거) 유효한 검수 PASS는 발행 가능해야 한다.

사람의 PATCH 편집만 PASS를 낡게 만든다. 본문 편집도 그대로 낡은 PASS다.
"""

import uuid
from types import SimpleNamespace

from app.services import content_publication
from app.services.content_ai_review import candidate_sha256
from app.services.reference_publication import (
    PublicationReferenceRefresh,
    apply_publication_reference_refresh,
)
from tests.test_ai_review_hash_binding import _aligned, _item, _review

HEALED = [{"title": "대한의사협회", "url": "https://www.kma.org/healed"}]


def _refresh(item, references):
    # 스냅샷 대조는 이 테스트의 관심사가 아니다 — 항상 일치한다고 본다.
    return PublicationReferenceRefresh(
        snapshot=SimpleNamespace(),
        references=references,
        checks=[],
        references_changed=True,
        deferred=False,
        site_unreachable_urls=(),
    )


def _apply(monkeypatch, item, references):
    from app.services import reference_publication

    monkeypatch.setattr(reference_publication, "reference_snapshot_matches", lambda *_: True)
    assert apply_publication_reference_refresh(item, _refresh(item, references))


def _assess(item):
    return content_publication.assess_content_publication(item, SimpleNamespace(id=uuid.uuid4()))


def test_system_reference_heal_keeps_pass_publishable(monkeypatch):
    _aligned(monkeypatch)
    item = _item()
    item.essence_check_summary = {"ai_review": _review(item)}

    _apply(monkeypatch, item, HEALED)

    assert item.references_list == HEALED
    assert item.essence_check_summary["ai_review"]["candidate_sha256"] == candidate_sha256(item)
    assert _assess(item).code is None


def test_system_heal_does_not_rescue_an_already_stale_review(monkeypatch):
    _aligned(monkeypatch)
    item = _item()
    item.essence_check_summary = {"ai_review": _review(item)}
    item.body = "사람이 고친 본문"

    _apply(monkeypatch, item, HEALED)

    assert _assess(item).code == "CONTENT_AI_REVIEW_STALE"


def test_human_reference_edit_is_still_stale(monkeypatch):
    _aligned(monkeypatch)
    item = _item()
    item.essence_check_summary = {"ai_review": _review(item)}
    item.references_list = HEALED  # PATCH 경로는 재서명하지 않는다

    assert _assess(item).code == "CONTENT_AI_REVIEW_STALE"


def test_body_edit_is_still_stale(monkeypatch):
    _aligned(monkeypatch)
    item = _item()
    item.essence_check_summary = {"ai_review": _review(item)}
    item.body = "고친 본문"

    assert _assess(item).code == "CONTENT_AI_REVIEW_STALE"
