"""H-01: 공개 사이트가 숨기는 글을 admin이 '공개 중'이라고 말하지 못하게 하는 단일 판정."""

import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import app.api.public.site as site
from app.api.admin import content as admin_content
from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.hospital import HospitalStatus
from app.services.content_visibility import VISIBILITY_BLOCKER_LABELS, assess_public_visibility
from app.services.image_engine import (
    IMAGE_POLICY_VERSION,
    image_content_hash_from_url,
    image_subject_hash,
)


def _published(**overrides):
    philosophy_id = uuid.uuid4()
    # https 경로 — admin 직렬화의 get_signed_url이 gs:// 값만 GCS로 들고 가기 때문에
    # 이 더블은 서명 호출 없이 그대로 통과한다. 인증 hash는 파일명에서 나온다.
    image_url = f"https://storage.googleapis.com/reputation-images/content/{'a' * 64}-reviewed.png"
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
        # admin 직렬화가 읽는 나머지 운영 필드 (가시성 판정에는 쓰이지 않는다).
        hospital_id=uuid.uuid4(),
        sequence_no=1,
        total_count=12,
        scheduled_date=date(2026, 6, 1),
        carried_over_from=None,
        generated_at=datetime.now(timezone.utc),
        published_by="김민지 AE",
        post_publish_notified_at=None,
        post_publish_reviewed_at=None,
        post_publish_reviewed_by=None,
        body_updated_at=None,
        query_target_id=None,
        exposure_action_id=None,
        content_brief=None,
        brief_status=None,
        brief_approved_at=None,
        brief_approved_by=None,
        image_prompt=None,
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
    """모든 검사에 걸리는 글은 라벨 표의 키 순서 그대로 사유를 낸다.

    사유 순서는 AE가 읽는 문장의 순서다 — 표와 판정이 갈라지면 라벨 없는 코드가 화면에
    그대로 노출되거나, 같은 글이 화면마다 다른 순서로 설명된다.
    """
    item, _ = _published(
        status=ContentStatus.DRAFT,
        content_type=ContentType.FAQ,
        title="   ",
        body="",
        published_at=None,
        essence_status="NEEDS_ESSENCE_REVIEW",
        faq_question=None,
        # 화이트리스트 밖 출처 + 제목에 금지 표현 — 참고자료와 금지 표현을 동시에 건다.
        references_list=[{"title": "최고의 병원 광고", "url": "https://ad-blog.example.com/promo"}],
        image_url=None,
        essence_check_summary={"ai_review": {"status": "UNAVAILABLE"}},
    )
    result = assess_public_visibility(item, uuid.uuid4())
    assert result.visible is False
    assert result.blockers == tuple(VISIBILITY_BLOCKER_LABELS)
    assert result.blocker_labels == [VISIBILITY_BLOCKER_LABELS[code] for code in result.blockers]
    assert result.blocker_labels[0] == "현재 승인된 콘텐츠 운영 기준과 다른 기준으로 생성됨"


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


def test_site_withholds_when_no_approved_philosophy_but_not_when_unasked():
    """None(승인된 기준 없음)과 UNSET(기준을 묻지 않음)은 다른 답이다."""
    item, _ = _published()
    assert site._is_public_safe_content(item, None) is False
    assert site._is_public_safe_content(item) is True


def _serialize(item, philosophy_id):
    return admin_content._serialize_item(item, full=True, public_philosophy_id=philosophy_id)


def test_admin_serializes_a_withheld_published_item_as_withheld():
    """admin 표시 경로 — 공개 페이지가 숨기는 글에 '공개 완료'가 붙으면 H-01이다."""
    item, philosophy_id = _published(image_policy_verified_at=None, image_content_hash=None)

    serialized = _serialize(item, philosophy_id)

    review = serialized["display"]["review"]
    assert review["label"] == "공개 보류"
    assert "대표 이미지 재인증 대기" in review["reason"]
    assert review["publishable"] is False
    visibility = serialized["compliance"]["public_visibility"]
    assert visibility["visible"] is False
    assert visibility["blockers"] == ["IMAGE_NOT_CERTIFIED"]
    assert visibility["blocker_labels"] == ["대표 이미지 재인증 대기"]


def test_notification_label_never_overwrites_the_withheld_label():
    """알림 문구가 사유를 덮으면 AE는 글이 공개 페이지에 없다는 사실을 볼 수 없다."""
    item, philosophy_id = _published(image_policy_verified_at=None, image_content_hash=None)
    item._publish_notification_projection = {
        "state": "PENDING",
        "label": "Slack 전달 대기",
        "problem": None,
        "next_action": "잠시 후 자동으로 전달됩니다.",
    }

    review = _serialize(item, philosophy_id)["display"]["review"]

    assert review["label"] == "공개 보류"
    assert "대표 이미지 재인증 대기" in review["reason"]
    assert review["notification_state"] == "PENDING"


class _PatchDB:
    """update_content의 행 잠금 조회만 실제 아이템으로 돌려주는 최소 더블."""

    def __init__(self, item):
        self._item = item
        self.committed = False

    async def execute(self, statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self._item)

    async def commit(self):
        self.committed = True

    async def refresh(self, item):
        pass


def _published_orm(**overrides):
    """실제 ContentItem — update_content의 isinstance 게이트를 통과해야 한다."""
    fields, philosophy_id = _published(**overrides)
    item = ContentItem()
    for key, value in vars(fields).items():
        if not key.startswith("_"):
            setattr(item, key, value)
    return item, philosophy_id


async def _patch_published(
    monkeypatch, item, philosophy_id, patch, *, status=HospitalStatus.ACTIVE
):
    hospital = SimpleNamespace(
        id=item.hospital_id,
        name="재인증의원",
        slug="recert-clinic",
        status=status,
        site_live=True,
        aeo_domain=None,
        treatments=[],
    )
    dispatched: list[str] = []
    submitted: list[uuid.UUID] = []

    async def _revalidate(slug, content_id, **kwargs):
        return True

    monkeypatch.setattr(admin_content, "ensure_site_revalidate_configured", lambda: None)
    monkeypatch.setattr(admin_content, "trigger_content_site_revalidate_safe", _revalidate)

    async def _get_content(db, content_id, hospital_id):
        return item

    async def _get_hospital(db, hospital_id):
        return hospital

    async def _philosophy(db, hospital_id):
        return None

    async def _dispatch(db, command, task):
        dispatched.append(command.operation_type)

    async def _enqueue(db, **kwargs):
        submitted.append(kwargs["content_id"])

    monkeypatch.setattr(admin_content, "_get_content", _get_content)
    monkeypatch.setattr(admin_content, "_get_hospital", _get_hospital)
    monkeypatch.setattr(admin_content, "_get_approved_philosophy", _philosophy)
    monkeypatch.setattr(admin_content, "get_public_approved_philosophy_id", _philosophy)
    monkeypatch.setattr(admin_content, "dispatch_operation", _dispatch)
    monkeypatch.setattr(admin_content.indexnow, "enqueue_content_published", _enqueue)

    await admin_content.update_content(
        hospital.id, item.id, admin_content.ContentPatch(**patch), db=_PatchDB(item)
    )
    return dispatched, submitted


async def test_title_edit_on_a_published_item_dispatches_image_recertification(monkeypatch):
    """인증을 지운 편집은 재인증을 예약하고, 아직 숨겨진 판을 색인에 제출하지 않는다."""
    item, philosophy_id = _published_orm()

    dispatched, submitted = await _patch_published(
        monkeypatch, item, philosophy_id, {"title": "치질 증상과 진료 시점"}
    )

    assert item.image_policy_verified_at is None
    assert dispatched == ["RECERTIFY_PUBLISHED_IMAGE"]
    assert submitted == []


async def test_body_edit_that_keeps_the_certificate_only_resubmits_the_index(monkeypatch):
    item, philosophy_id = _published_orm()

    dispatched, submitted = await _patch_published(
        monkeypatch, item, philosophy_id, {"body": "고쳐 쓴 본문 " * 300}
    )

    assert item.image_policy_verified_at is not None
    assert dispatched == []
    assert submitted == [item.id]


def test_visible_item_still_shows_the_notification_label_when_not_sent():
    """공개 중인 글에서는 알림 상태 표시가 그대로 살아 있어야 한다."""
    item, philosophy_id = _published()
    item._publish_notification_projection = {
        "state": "PENDING",
        "label": "Slack 전달 대기",
        "problem": None,
        "next_action": "잠시 후 자동으로 전달됩니다.",
    }

    review = _serialize(item, philosophy_id)["display"]["review"]

    assert review["label"] == "Slack 전달 대기"
    assert review["reason"] == "잠시 후 자동으로 전달됩니다."


_SENT_NOTIFICATION = {
    "state": "SENT",
    "label": "Slack 전달 완료",
    "problem": None,
    "next_action": "공개된 글에 문제가 없는지 확인해 주세요.",
}


def test_review_sample_is_the_only_published_item_asking_for_confirmation():
    """표본(첫 순번)만 확인 대기다 — 전체 공개 글에 확인을 요구하면 아무도 처리하지 않는다(M-21)."""
    item, philosophy_id = _published(sequence_no=1)
    item._publish_notification_projection = dict(_SENT_NOTIFICATION)

    serialized = _serialize(item, philosophy_id)

    assert serialized["post_publish_review_required"] is True
    assert serialized["display"]["review"]["label"] == "공개 내용 확인 대기"


def test_non_sample_published_item_is_public_not_pending_confirmation():
    item, philosophy_id = _published(sequence_no=7)
    item._publish_notification_projection = dict(_SENT_NOTIFICATION)

    serialized = _serialize(item, philosophy_id)

    assert serialized["post_publish_review_required"] is False
    assert serialized["display"]["review"]["label"] == "공개 중"


def test_reviewed_sample_no_longer_asks_for_confirmation():
    item, philosophy_id = _published(
        sequence_no=1, post_publish_reviewed_at=datetime.now(timezone.utc)
    )
    item._publish_notification_projection = dict(_SENT_NOTIFICATION)

    serialized = _serialize(item, philosophy_id)

    assert serialized["post_publish_review_required"] is False
    assert serialized["display"]["review"]["label"] == "공개 내용 확인 완료"


def test_withheld_sample_still_reads_as_withheld():
    """공개 보류가 표본 여부보다 앞선다 — 공개 페이지에 없는 글에 확인은 성립하지 않는다."""
    item, philosophy_id = _published(
        sequence_no=1, image_policy_verified_at=None, image_content_hash=None
    )
    item._publish_notification_projection = dict(_SENT_NOTIFICATION)

    serialized = _serialize(item, philosophy_id)

    assert serialized["display"]["review"]["label"] == "공개 보류"
