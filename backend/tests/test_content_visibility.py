"""H-01: 공개 사이트가 숨기는 글을 admin이 '공개 중'이라고 말하지 못하게 하는 단일 판정."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import app.api.public.site as site
from app.models.content import ContentStatus, ContentType
from app.services.content_visibility import VISIBILITY_BLOCKER_LABELS, assess_public_visibility
from app.services.image_engine import (
    IMAGE_POLICY_VERSION,
    image_content_hash_from_url,
    image_subject_hash,
)


def _published(**overrides):
    philosophy_id = uuid.uuid4()
    image_url = f"gs://reputation-images/content/{'a' * 64}-reviewed.png"
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
        references_list=[
            {
                "title": "질병관리청 국가건강정보포털",
                "url": (
                    "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/"
                    "gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5463"
                ),
            }
        ],
        image_url=image_url,
        image_policy_verified_at=datetime.now(timezone.utc),
        image_content_hash=image_content_hash_from_url(image_url),
        image_subject_hash=image_subject_hash(ContentType.DISEASE, "치질 원인과 치료"),
        image_policy_version=IMAGE_POLICY_VERSION,
        essence_check_summary={},
        meta_description=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base), philosophy_id


def test_fully_certified_published_item_is_visible():
    item, philosophy_id = _published()
    result = assess_public_visibility(item, philosophy_id)
    assert result.visible is True and result.blockers == ()


def test_title_edit_that_invalidates_the_image_certificate_withholds_with_a_reason():
    item, philosophy_id = _published(image_policy_verified_at=None, image_content_hash=None)
    result = assess_public_visibility(item, philosophy_id)
    assert result.visible is False
    assert result.blockers == ("IMAGE_NOT_CERTIFIED",)
    assert VISIBILITY_BLOCKER_LABELS["IMAGE_NOT_CERTIFIED"] == "대표 이미지 재인증 대기"


def test_every_blocker_has_a_korean_label_and_a_stable_order():
    item, _ = _published(
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


def test_site_and_admin_read_the_same_answer_from_one_judgment():
    """공개 표면과 admin이 같은 아이템에 같은 답을 낸다 — 두 판정이 갈라지면 H-01이 되돌아온다."""
    certified, certified_pid = _published()
    cleared, cleared_pid = _published(image_policy_verified_at=None, image_content_hash=None)
    forbidden, forbidden_pid = _published(
        title="최고의 치질 치료",
        image_subject_hash=image_subject_hash(ContentType.DISEASE, "최고의 치질 치료"),
    )

    for item, philosophy_id in (
        (certified, certified_pid),
        (cleared, cleared_pid),
        (forbidden, forbidden_pid),
    ):
        assert assess_public_visibility(item, philosophy_id).visible is (
            site._is_public_safe_content(item, philosophy_id)
        )

    assert assess_public_visibility(certified, certified_pid).visible is True
    assert assess_public_visibility(cleared, cleared_pid).visible is False
    assert "FORBIDDEN_EXPRESSION" in assess_public_visibility(forbidden, forbidden_pid).blockers
