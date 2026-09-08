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


def test_noise_hash_is_a_canonical_set_not_a_list():
    a = uuid.uuid4()
    assert compute_evidence_noise_hash([a, a, str(a).upper()]) == compute_evidence_noise_hash([a])


def test_is_noise_note_reads_only_an_explicit_true_flag():
    assert is_noise_note(SimpleNamespace(note_metadata={"is_noise": True})) is True
    assert is_noise_note(SimpleNamespace(note_metadata={"is_noise": False})) is False
    assert is_noise_note(SimpleNamespace(note_metadata={})) is False
    assert is_noise_note(SimpleNamespace(note_metadata=None)) is False
