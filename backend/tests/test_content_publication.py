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


def _reused(**overrides):
    """같은 병원의 다른 공개 글에서 빌려온 이미지를 단 글.

    주제 hash는 **원본의 값 그대로**다. 이 글의 제목으로 다시 계산해 넣으면 아무도
    검수하지 않은 합성 인증값이 되므로, 결합 대상은 marker 컬럼이 말한다.
    """
    base = {
        "image_reused_from_content_id": uuid.uuid4(),
        "image_subject_hash": content_publication.image_subject_hash(
            ContentType.FAQ, "다른 글의 제목"
        ),
    }
    base.update(overrides)
    return _item(**base)


def test_publication_policy_accepts_a_reused_certified_image(monkeypatch):
    _aligned(monkeypatch)

    assessment = content_publication.assess_content_publication(_reused(), _philosophy())

    assert assessment.publishable is True
    assert assessment.code is None
    # 운영 화면·사후 교체 스윕이 "빌린 판"을 식별할 수 있어야 한다.
    assert assessment.essence_summary["image_reused"] is True


def test_publication_policy_does_not_mark_an_own_image_as_reused(monkeypatch):
    _aligned(monkeypatch)

    assessment = content_publication.assess_content_publication(_item(), _philosophy())

    assert assessment.publishable is True
    assert "image_reused" not in assessment.essence_summary


def test_reused_image_still_requires_the_byte_binding(monkeypatch):
    """marker가 있어도 바이트 결합이 없으면 인증이 아니다 — 합성 통과를 만들지 않는다."""
    _aligned(monkeypatch)

    assessment = content_publication.assess_content_publication(
        _reused(image_content_hash=None), _philosophy()
    )

    assert assessment.publishable is False
    assert assessment.code == "CONTENT_IMAGE_NOT_VERIFIED"


def test_reused_image_with_a_retired_policy_version_is_not_current(monkeypatch):
    _aligned(monkeypatch)

    assessment = content_publication.assess_content_publication(
        _reused(image_policy_version="2000-01-01"), _philosophy()
    )

    assert assessment.publishable is False
    assert assessment.code == "CONTENT_IMAGE_NOT_VERIFIED"


def test_reused_image_survives_a_title_edit_but_an_own_image_does_not():
    """제목 편집의 의미가 두 모양에서 다르다 — 빌린 이미지는 원본에 묶여 있다."""
    own = _item()
    own.title = "제목이 바뀐 글"
    reused = _reused()
    reused.title = "제목이 바뀐 글"

    assert content_publication.image_certification_current(own) is False
    assert content_publication.image_certification_current(reused) is True


def _hospital_fallback(**overrides):
    """병원 히어로에서 온 대체 이미지를 단 글.

    주제 hash는 **비어 있다**. 이 이미지는 이 글의 주제로 검수된 적이 없고, 새로 계산해
    넣으면 아무도 검수하지 않은 합성 인증값이 된다. 결합 대상은 marker가 말한다.
    """
    base = {
        "image_fallback_source": "HOSPITAL_HERO",
        "image_subject_hash": None,
        "image_reused_from_content_id": None,
    }
    base.update(overrides)
    return _item(**base)


def test_publication_policy_accepts_a_certified_hospital_fallback_image(monkeypatch):
    _aligned(monkeypatch)

    assessment = content_publication.assess_content_publication(
        _hospital_fallback(), _philosophy()
    )

    assert assessment.publishable is True
    assert assessment.code is None
    # 사후 교체 스윕·운영 화면·08:00 요약은 "자기 주제 이미지가 아니다"를 한 열쇠로 읽는다.
    assert assessment.essence_summary["image_reused"] is True
    # 출처는 한 칸으로만 구분한다 — 읽는 쪽을 둘로 늘리지 않는다.
    assert assessment.essence_summary["image_fallback"] == "HOSPITAL_HERO"


def test_a_borrowed_article_image_is_not_marked_as_a_hospital_fallback(monkeypatch):
    _aligned(monkeypatch)

    assessment = content_publication.assess_content_publication(_reused(), _philosophy())

    assert assessment.essence_summary["image_reused"] is True
    assert "image_fallback" not in assessment.essence_summary


def test_an_own_topic_image_carries_neither_substitution_key(monkeypatch):
    _aligned(monkeypatch)

    assessment = content_publication.assess_content_publication(_item(), _philosophy())

    assert "image_reused" not in assessment.essence_summary
    assert "image_fallback" not in assessment.essence_summary


def test_hospital_fallback_image_still_requires_the_byte_binding(monkeypatch):
    """marker가 있어도 내용 hash가 URL과 묶이지 않으면 인증이 아니다."""
    _aligned(monkeypatch)

    assessment = content_publication.assess_content_publication(
        _hospital_fallback(image_content_hash=None), _philosophy()
    )

    assert assessment.publishable is False
    assert assessment.code == "CONTENT_IMAGE_NOT_VERIFIED"


def test_hospital_fallback_image_with_a_retired_policy_version_is_not_current(monkeypatch):
    _aligned(monkeypatch)

    assessment = content_publication.assess_content_publication(
        _hospital_fallback(image_policy_version="2000-01-01"), _philosophy()
    )

    assert assessment.publishable is False
    assert assessment.code == "CONTENT_IMAGE_NOT_VERIFIED"


def test_unknown_fallback_marker_does_not_grant_certification():
    """marker 문자열이 다르면 (c) 모양이 아니다 — 아무 값이나 통과시키지 않는다."""
    item = _hospital_fallback(image_fallback_source="SOMETHING_ELSE")

    assert content_publication.image_certification_current(item) is False


def test_hospital_fallback_image_survives_a_title_edit():
    """결합 대상이 글의 제목이 아니라 병원이므로 제목 편집이 인증을 깨지 않는다."""
    fallback = _hospital_fallback()
    fallback.title = "제목이 바뀐 글"

    assert content_publication.image_certification_current(fallback) is True


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


def test_authority_domain_does_not_exempt_a_reference_title(monkeypatch):
    """도메인이 공신력 있어도 제목은 모델이 지은 자유 텍스트다 — 예외를 두지 않는다.

    생성 단계가 이런 제목을 기관 표기로 바꾸지만(content_engine._sanitize_reference_titles),
    그 경로를 거치지 않은 기존 행·수동 편집이 있으므로 발행 게이트는 다시 검사한다.
    """
    _aligned(monkeypatch)
    item = _item(
        references_list=[
            {
                "title": "부작용 없는 치료 안내",
                "url": "https://www.cancer.go.kr/lay1/program/S1T211C223/cancer/view.do?cancer_seq=3797",
            }
        ]
    )

    assessment = content_publication.assess_content_publication(item, _philosophy())

    assert assessment.publishable is False
    assert assessment.code == "FORBIDDEN_EXPRESSION"
    assert "부작용 없는" in assessment.violations
    assert (
        content_publication.publication_field_values(item)["reference_titles"]
        == "부작용 없는 치료 안내"
    )


def test_generation_and_publication_share_the_reference_required_types():
    """두 집합이 갈라지면 생성은 통과하고 발행만 막히는 유형이 생긴다(NOTICE가 그랬다)."""
    from app.services.content_engine import REFERENCES_REQUIRED_TYPES

    assert content_publication._REFERENCES_REQUIRED_VALUES == frozenset(
        content_type.value for content_type in REFERENCES_REQUIRED_TYPES
    )


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
    assert "확인되지 않은 장비 주장" in assessment.message
    assert "승인된 병원 자료 또는 의료 근거" in assessment.message


def test_unconfigured_ai_review_has_permanent_cause_code(monkeypatch):
    _aligned(monkeypatch)
    item = _reviewed_item(
        {
            "status": "UNAVAILABLE",
            "schema_version": "content-review-v2",
            "blocking": True,
            "findings": [],
            "unavailable_reason": "PROVIDER_UNCONFIGURED",
        }
    )
    assessment = content_publication.assess_content_publication(item, _philosophy())
    assert assessment.publishable is False
    assert assessment.code == "CONTENT_AI_REVIEW_CONFIG_ERROR"


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
