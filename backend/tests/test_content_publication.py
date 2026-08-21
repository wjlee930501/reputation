import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.models.content import ContentType
from app.services import content_publication


def _item(**overrides):
    base = {
        "title": "치질 진료 전 확인할 점",
        "body": "증상과 생활 불편을 확인한 뒤 진료 방향을 설명합니다.",
        "image_url": "https://storage.googleapis.com/reputation/content.png",
        "image_policy_verified_at": datetime(2026, 8, 22, tzinfo=timezone.utc),
        "meta_description": "진료 전 확인할 내용을 정리합니다.",
        "faq_question": None,
        "faq_answer_summary": None,
        "references_list": [{"title": "질병관리청", "url": "https://kdca.go.kr/example"}],
        "content_philosophy_id": None,
        "essence_status": None,
        "essence_check_summary": None,
    }
    base.update(overrides)
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


def test_publication_policy_blocks_an_image_without_semantic_verification(monkeypatch):
    _aligned(monkeypatch)

    assessment = content_publication.assess_content_publication(
        _item(image_policy_verified_at=None), _philosophy()
    )

    assert assessment.publishable is False
    assert assessment.code == "CONTENT_IMAGE_NOT_READY"


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
    faq = _item(content_type=ContentType.FAQ, references_list=[])
    faq_assessment = content_publication.assess_content_publication(faq, _philosophy())
    assert faq_assessment.publishable is False
    assert faq_assessment.code == "MISSING_REFERENCES"
