"""process_source_asset() 결과를 통한 evidence note 분류 검증.

규칙 기반 분류기(_classify_excerpt)이므로 LLM 호출 없이 동작 — 실제 AE 입력
샘플로 노트 타입이 의도대로 매핑되는지 회귀 테스트한다.
"""
from types import SimpleNamespace

import pytest

from app.models.essence import EvidenceNoteType
from app.services.essence_engine import process_source_asset


def _make_asset(raw_text: str, operator_note: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(raw_text=raw_text, operator_note=operator_note)


def test_process_source_asset_raises_when_raw_text_empty():
    asset = _make_asset(raw_text="   ")
    with pytest.raises(ValueError):
        process_source_asset(asset)


def test_process_source_asset_categorizes_doctor_philosophy():
    asset = _make_asset(
        raw_text=(
            "원장이 가장 중요하게 생각하는 진료 원칙은 환자가 충분히 이해할 때까지 설명하는 것입니다. "
            "동네 환자들의 일상 회복을 돕는 차분한 진료를 약속합니다."
        )
    )

    notes = process_source_asset(asset)

    note_types = {n.note_type for n in notes}
    assert EvidenceNoteType.DOCTOR_PHILOSOPHY in note_types
    # 환자 약속 또는 톤 시그널 중 하나는 포함되어야 한다.
    assert {EvidenceNoteType.PATIENT_PROMISE, EvidenceNoteType.TONE_SIGNAL} & note_types


def test_process_source_asset_flags_forbidden_expression_as_risk():
    asset = _make_asset(raw_text="저희 병원은 강남에서 1등 성공률을 자랑합니다.")
    notes = process_source_asset(asset)

    risk_notes = [n for n in notes if n.note_type == EvidenceNoteType.RISK_SIGNAL]
    assert risk_notes, "의료광고 금지 표현이 RISK_SIGNAL 로 분류되어야 한다"
    assert any("violations" in (n.note_metadata or {}) for n in risk_notes)


def test_process_source_asset_classifies_treatment_signal():
    asset = _make_asset(
        raw_text="대장 내시경 검사를 통해 용종을 조기에 발견하고 제거하는 시술을 합니다."
    )
    notes = process_source_asset(asset)

    treatment_notes = [n for n in notes if n.note_type == EvidenceNoteType.TREATMENT_SIGNAL]
    assert treatment_notes, "진료/시술 단어가 포함되면 TREATMENT_SIGNAL로 분류되어야 한다"
    # 진료 라벨 추출 메타데이터 동봉 확인.
    assert any((n.note_metadata or {}).get("treatment") for n in treatment_notes)


def test_process_source_asset_classifies_local_context_when_region_term_present():
    asset = _make_asset(
        raw_text="강남구 역삼동 인근 환자분들이 자주 방문하는 지역 친화 병원입니다."
    )
    notes = process_source_asset(asset)

    local_notes = [n for n in notes if n.note_type == EvidenceNoteType.LOCAL_CONTEXT]
    assert local_notes, "지역명/구·동 단어가 포함된 문장은 LOCAL_CONTEXT로 분류되어야 한다"


def test_process_source_asset_inserts_key_message_when_absent():
    """KEY_MESSAGE note가 없는 입력에는 첫 번째 근거 기반 KEY_MESSAGE가 자동 삽입된다."""
    asset = _make_asset(raw_text="대장 내시경 시술과 항문외과 수술을 합니다.")
    notes = process_source_asset(asset)

    note_types = [n.note_type for n in notes]
    assert EvidenceNoteType.KEY_MESSAGE in note_types
    key_message_index = note_types.index(EvidenceNoteType.KEY_MESSAGE)
    # 자동 삽입된 KEY_MESSAGE는 가장 앞에 위치해야 한다.
    assert key_message_index == 0
    assert notes[0].note_metadata.get("derived_from_first_evidence") is True


def test_process_source_asset_caps_at_twenty_payloads():
    sentences = " ".join(
        f"환자분들께 {i}번째로 친절히 설명드리는 진료 원칙을 지킵니다." for i in range(1, 30)
    )
    notes = process_source_asset(_make_asset(raw_text=sentences))

    assert len(notes) <= 21  # 20개 + KEY_MESSAGE 자동 삽입 1개
