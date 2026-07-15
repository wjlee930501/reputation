from types import SimpleNamespace
import uuid

from app.services import content_publication


def _item(**overrides):
    base = {
        "title": "치질 진료 전 확인할 점",
        "body": "증상과 생활 불편을 확인한 뒤 진료 방향을 설명합니다.",
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
