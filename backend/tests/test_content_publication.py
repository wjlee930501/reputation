import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.content import ContentType
from app.services import content_publication


def _item(**overrides):
    image_hash = "a" * 64
    base = {
        "content_type": ContentType.DISEASE,
        "title": "치질 진료 전 확인할 점",
        "body": "증상과 생활 불편을 확인한 뒤 진료 방향을 설명합니다.",
        "image_url": f"gs://reputation-images/content/{image_hash}-content.png",
        "image_content_hash": image_hash,
        "image_policy_verified_at": datetime.now(timezone.utc),
        "image_policy_version": content_publication.IMAGE_POLICY_VERSION,
        "meta_description": "진료 전 확인할 내용을 정리합니다.",
        "faq_question": None,
        "faq_answer_summary": None,
        "references_list": [{"title": "질병관리청", "url": "https://kdca.go.kr/example"}],
        "content_philosophy_id": None,
        "essence_status": None,
        "essence_check_summary": None,
    }
    base.update(overrides)
    if "image_subject_hash" not in overrides:
        base["image_subject_hash"] = content_publication.image_subject_hash(
            base["content_type"], base["title"]
        )
    return SimpleNamespace(**base)


def _philosophy():
    return SimpleNamespace(id=uuid.uuid4())


def _aligned(monkeypatch):
    monkeypatch.setattr(
        content_publication,
        "screen_content_against_philosophy",
        lambda *_args: SimpleNamespace(status="ALIGNED", summary={"blocking": False}),
    )


def test_publication_policy_accepts_machine_safe_source_backed_content(monkeypatch):
    _aligned(monkeypatch)
    philosophy = _philosophy()

    assessment = content_publication.assess_content_publication(_item(), philosophy)

    assert assessment.publishable is True
    assert assessment.philosophy_id == philosophy.id
    assert assessment.code is None


def test_publication_policy_blocks_missing_reference(monkeypatch):
    _aligned(monkeypatch)

    assessment = content_publication.assess_content_publication(
        _item(references_list=[]), _philosophy()
    )

    assert assessment.publishable is False
    assert assessment.code == "MISSING_REFERENCES"
    assert assessment.essence_summary["blocking"] is True


def test_publication_policy_blocks_missing_representative_image(monkeypatch):
    _aligned(monkeypatch)

    assessment = content_publication.assess_content_publication(
        _item(image_url=None), _philosophy()
    )

    assert assessment.publishable is False
    assert assessment.code == "CONTENT_IMAGE_NOT_READY"
    assert assessment.essence_summary["blocking"] is True


def test_publication_policy_blocks_unverified_representative_image(monkeypatch):
    _aligned(monkeypatch)

    assessment = content_publication.assess_content_publication(
        _item(image_policy_verified_at=None), _philosophy()
    )

    assert assessment.publishable is False
    assert assessment.code == "CONTENT_IMAGE_NOT_VERIFIED"


@pytest.mark.parametrize(
    ("question", "answer"),
    [(None, "답변"), ("질문인가요?", None), ("물음표 없는 질문", "답변")],
)
def test_publication_policy_blocks_incomplete_faq_schema(monkeypatch, question, answer):
    _aligned(monkeypatch)

    assessment = content_publication.assess_content_publication(
        _item(
            content_type=ContentType.FAQ,
            faq_question=question,
            faq_answer_summary=answer,
        ),
        _philosophy(),
    )

    assert assessment.publishable is False
    assert assessment.code == "FAQ_FIELDS_MISSING"


def test_publication_policy_blocks_forbidden_expression_across_public_fields(monkeypatch):
    _aligned(monkeypatch)

    assessment = content_publication.assess_content_publication(
        _item(faq_answer_summary="부작용 없는 최고의 치료입니다."), _philosophy()
    )

    assert assessment.publishable is False
    assert assessment.code == "FORBIDDEN_EXPRESSION"
    assert "최고" in assessment.violations


def test_apply_publication_assessment_persists_exact_screening_result(monkeypatch):
    _aligned(monkeypatch)
    item = _item()
    assessment = content_publication.assess_content_publication(item, _philosophy())

    content_publication.apply_publication_assessment(item, assessment)

    assert item.content_philosophy_id == assessment.philosophy_id
    assert item.essence_status == "ALIGNED"
    assert item.essence_check_summary == {"blocking": False}


def test_publication_assessment_preserves_ai_review_provenance_but_not_old_blocking(monkeypatch):
    _aligned(monkeypatch)
    item = _item()
    item.essence_check_summary = {
        "blocking": True,
        "findings": ["이전 후보의 지적"],
        "automatic_remediation_attempts": 1,
        "reviewer_driven_rewrites": 1,
        "ai_review": {"status": "PASS", "confidence": 0.97},
    }
    assessment = content_publication.assess_content_publication(item, _philosophy())

    content_publication.apply_publication_assessment(item, assessment)

    assert item.essence_check_summary == {
        "blocking": False,
        "automatic_remediation_attempts": 1,
        "reviewer_driven_rewrites": 1,
        "ai_review": {"status": "PASS", "confidence": 0.97},
    }


def test_essence_writers_preserve_durable_image_certification_attempt(monkeypatch):
    _aligned(monkeypatch)
    state = {
        "fingerprint": "same-image-subject",
        "review_attempts": 2,
        "status": "PENDING_REVIEW",
    }
    item = _item(
        content_revision=4,
        essence_check_summary={"legacy_image_certification": state},
    )
    philosophy = _philosophy()

    assessment = content_publication.assess_content_publication(item, philosophy)
    content_publication.apply_publication_assessment(item, assessment)
    assert item.essence_check_summary["legacy_image_certification"] == state

    content_publication.apply_essence_revalidation(item, philosophy)
    assert item.content_revision == 5
    assert item.essence_check_summary["legacy_image_certification"] == state


def test_essence_writers_keep_the_published_image_recertification_block(monkeypatch):
    """재승인·본문 편집은 제목을 바꾸지 않는다. 재인증 차단 표시를 지우면 안 된다 (H-01)."""
    _aligned(monkeypatch)
    marker = {
        "subject_hash": "a" * 64,
        "title": "치질 증상과 진료 시점",
        "blocked": True,
        "code": "PUBLISHED_IMAGE_RECERTIFY_REJECTED",
    }
    item = _item(content_revision=4, essence_check_summary={"image_recertification": marker})
    philosophy = _philosophy()

    assessment = content_publication.assess_content_publication(item, philosophy)
    content_publication.apply_publication_assessment(item, assessment)
    assert item.essence_check_summary["image_recertification"] == marker

    content_publication.apply_essence_revalidation(item, philosophy)
    assert item.content_revision == 5
    assert item.essence_check_summary["image_recertification"] == marker


def test_publication_gate_uses_the_render_aware_check_for_the_body(monkeypatch):
    """발행 게이트는 본문을 **렌더 결과 기준**으로 검사해야 한다.

    이 테스트는 게이트의 '배선'을 고정한다. 필터 함수 단위 테스트만 있으면
    게이트가 평문 검사기로 되돌아가도 전부 초록이라 아무도 모른다
    (실제로 작업 중 한 번 조용히 되돌아갔다).
    """
    _aligned(monkeypatch)
    item = SimpleNamespace(
        title="정상 제목",
        # 렌더되면 "최고의 진료"로 보이는 우회 형태.
        body="본문입니다. 최**고**의 진료를 제공합니다.",
        meta_description=None,
        faq_question=None,
        faq_answer_summary=None,
        references_list=[{"title": "질병관리청", "url": "https://www.kdca.go.kr/x"}],
    )

    assessment = content_publication.assess_content_publication(item, _philosophy())

    assert assessment.publishable is False
    assert assessment.code == "FORBIDDEN_EXPRESSION"
    assert "최고" in assessment.violations


def test_reference_titles_are_screened_before_publication(monkeypatch):
    """참고 자료 제목도 공개 표면과 JSON-LD에 그대로 렌더된다.

    URL만 화이트리스트 검증을 거치고 제목은 모델 자유 출력이라, 검사에서 빠지면
    금지 표현이 "참고 자료" 섹션으로 공개된다.
    """
    _aligned(monkeypatch)
    item = _item(
        references_list=[
            {"title": "대장암 완치율 100% 달성 보고", "url": "https://www.kdca.go.kr/example"}
        ]
    )

    assessment = content_publication.assess_content_publication(item, _philosophy())

    assert assessment.publishable is False
    assert assessment.code == "FORBIDDEN_EXPRESSION"
    assert "완치" in assessment.violations


def test_notice_does_not_require_references_but_other_types_do(monkeypatch):
    """참고 자료 요구 유형이 생성 검증과 발행 게이트에서 같아야 한다.

    NOTICE는 순수 운영 공지라 생성 단계에서 참고 자료를 요구하지 않는다. 발행
    게이트만 유형 구분 없이 요구하면 NOTICE는 생성은 되고 발행은 매일
    MISSING_REFERENCES로 막히다가 조회 대상에서 빠져 영구 DRAFT로 사망한다.
    """
    _aligned(monkeypatch)

    notice = _item(content_type=ContentType.NOTICE, references_list=[])
    notice_assessment = content_publication.assess_content_publication(notice, _philosophy())
    assert notice_assessment.publishable is True, (
        f"NOTICE가 참고 자료 없이 차단됐다: {notice_assessment.code}"
    )

    # 의료 안내 유형은 여전히 근거를 요구한다.
    faq = _item(
        content_type=ContentType.FAQ,
        references_list=[],
        faq_question="복통은 언제 진료받아야 하나요?",
        faq_answer_summary="증상이 이어지면 진료로 원인을 확인합니다.",
    )
    faq_assessment = content_publication.assess_content_publication(faq, _philosophy())
    assert faq_assessment.publishable is False
    assert faq_assessment.code == "MISSING_REFERENCES"


def _reviewed_item(review: dict):
    item = _item(content_type=ContentType.NOTICE, references_list=[])
    review["candidate_sha256"] = content_publication.candidate_sha256(item)
    review["coverage"] = content_publication.candidate_review_coverage(item)
    item.essence_check_summary = {"ai_review": review}
    return item


def test_current_hard_ai_finding_blocks_publication(monkeypatch):
    _aligned(monkeypatch)
    item = _reviewed_item(
        {
            "status": "REVISE",
            "schema_version": "content-review-v2",
            "blocking": True,
            "findings": [
                {
                    "severity": "HARD",
                    "kind": "HOSPITAL_FACT",
                    "message": "확인되지 않은 장비 주장",
                }
            ],
        }
    )

    assessment = content_publication.assess_content_publication(item, _philosophy())

    assert assessment.publishable is False
    assert assessment.code == "CONTENT_AI_HARD_FINDING"


def test_soft_ai_finding_allows_deterministically_safe_content(monkeypatch):
    _aligned(monkeypatch)
    item = _reviewed_item(
        {
            "status": "REVISE",
            "schema_version": "content-review-v2",
            "blocking": False,
            "findings": [
                {"severity": "SOFT", "kind": "STYLE", "message": "문장이 깁니다."}
            ],
        }
    )

    assert content_publication.assess_content_publication(item, _philosophy()).publishable


def test_edit_cannot_discard_prior_hard_finding_by_hash_mismatch(monkeypatch):
    _aligned(monkeypatch)
    item = _reviewed_item(
        {
            "status": "REVISE",
            "schema_version": "content-review-v2",
            "blocking": True,
            "findings": [],
        }
    )
    item.body += " 수정"

    assessment = content_publication.assess_content_publication(item, _philosophy())

    assert assessment.publishable is False
    assert assessment.code == "CONTENT_AI_REVIEW_STALE"


def test_publication_identity_is_set_once_across_republished_editions():
    first_at = datetime(2026, 8, 31, 3, 0, tzinfo=timezone.utc)
    replacement_at = datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc)
    item = _item(
        published_at=None,
        published_by=None,
        first_published_at=None,
        first_published_by=None,
    )

    content_publication.record_publication_identity(
        item, published_at=first_at, published_by="FIRST_AE"
    )
    item.published_at = None
    item.published_by = None
    content_publication.record_publication_identity(
        item, published_at=replacement_at, published_by="REPAIR_AE"
    )

    assert item.first_published_at == first_at
    assert item.first_published_by == "FIRST_AE"
    assert item.published_at == replacement_at
    assert item.published_by == "REPAIR_AE"


def test_publication_identity_uses_known_current_date_during_rolling_deploy():
    known_at = datetime(2026, 8, 31, 3, 0, tzinfo=timezone.utc)
    item = _item(published_at=known_at, published_by="OLD_API")

    content_publication.record_publication_identity(
        item,
        published_at=datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc),
        published_by="NEW_API",
    )

    assert item.first_published_at == known_at
    assert item.first_published_by == "OLD_API"
    assert item.published_at == datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc)
    assert item.published_by == "NEW_API"


def test_publication_identity_does_not_invent_missing_legacy_first_actor():
    known_at = datetime(2026, 8, 31, 3, 0, tzinfo=timezone.utc)
    item = _item(published_at=known_at, published_by=None)

    content_publication.record_publication_identity(
        item,
        published_at=datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc),
        published_by="NEW_API",
    )

    assert item.first_published_at == known_at
    assert item.first_published_by is None
    assert item.published_by == "NEW_API"


def test_body_without_approved_essence_uses_missing_code():
    assessment = content_publication.assess_content_publication(_item(), None)
    assert assessment.code == "MISSING_APPROVED_ESSENCE"
    assert assessment.essence_status == content_publication.ESSENCE_STATUS_MISSING_APPROVED
    assert not assessment.publishable
