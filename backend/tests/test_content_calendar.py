"""콘텐츠 캘린더 유형 배치 회귀 — 몰림 방지 + 결정론.

버그: dict 순서대로 extend하던 이전 로직은 PLAN_16의 첫 4개 슬롯이 전부 FAQ로
연속 배정됐다. 수정 후에는 유형이 고르게 흩뿌려지고, 같은 입력에는 항상 같은
배치를 반환해야 한다(테스트 가능한 결정론).
"""
import os

os.environ.setdefault("ADMIN_SECRET_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///tmp/reputation-test.db")
os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///tmp/reputation-test.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

import uuid  # noqa: E402
from itertools import groupby  # noqa: E402

import arrow  # noqa: E402

from app.models.content import PLAN_DISTRIBUTION  # noqa: E402
from app.services.content_calendar import _interleave_types, generate_monthly_slots  # noqa: E402


def _max_consecutive_run(sequence: list) -> int:
    return max(len(list(group)) for _, group in groupby(sequence))


def test_interleave_types_avoids_long_runs_for_plan_16():
    sequence = _interleave_types(PLAN_DISTRIBUTION["PLAN_16"], seed="hospital-a:2026-07")

    assert len(sequence) == 16
    # 버그 재현 방지: 이전 로직은 FAQ가 4연속으로 몰렸다.
    assert _max_consecutive_run(sequence) <= 2
    # 카운트 보존 — 재배치일 뿐 유형별 편수는 그대로여야 한다.
    for ctype, count in PLAN_DISTRIBUTION["PLAN_16"].items():
        assert sequence.count(ctype) == count


def test_interleave_types_is_deterministic_for_same_seed():
    seq1 = _interleave_types(PLAN_DISTRIBUTION["PLAN_16"], seed="hospital-a:2026-07")
    seq2 = _interleave_types(PLAN_DISTRIBUTION["PLAN_16"], seed="hospital-a:2026-07")

    assert seq1 == seq2


def test_interleave_types_differs_across_seeds():
    seq_a = _interleave_types(PLAN_DISTRIBUTION["PLAN_16"], seed="hospital-a:2026-07")
    seq_b = _interleave_types(PLAN_DISTRIBUTION["PLAN_16"], seed="hospital-b:2026-07")

    # 회전량이 시드에 따라 달라져 병원마다 시작 유형이 반복되지 않는다.
    assert seq_a != seq_b


def test_generate_monthly_slots_avoids_long_type_runs_without_hospital_id():
    """hospital_id를 넘기지 않는 기존 호출부(admin API, worker, demo_seed)도
    plan+연월 시드로 자동으로 몰림이 해소되어야 한다."""
    slots = generate_monthly_slots(
        "PLAN_16", [0, 1, 2, 3, 4, 5, 6], arrow.get("2026-07-01").floor("month")
    )
    types = [ctype for _, ctype, _, _ in slots]

    assert len(types) == 16
    assert _max_consecutive_run(types) <= 2


def test_generate_monthly_slots_same_hospital_and_month_is_deterministic():
    hospital_id = uuid.uuid4()
    month = arrow.get("2026-07-01").floor("month")

    slots1 = generate_monthly_slots(
        "PLAN_12", [0, 2, 4], month, hospital_id=hospital_id
    )
    slots2 = generate_monthly_slots(
        "PLAN_12", [0, 2, 4], month, hospital_id=hospital_id
    )

    assert slots1 == slots2


def test_generate_monthly_slots_preserves_dates_and_sequence_numbers():
    slots = generate_monthly_slots(
        "PLAN_8", [0, 1, 2, 3, 4, 5, 6], arrow.get("2026-07-01").floor("month")
    )

    assert [seq for _, _, seq, _ in slots] == list(range(1, 9))
    assert all(total == 8 for _, _, _, total in slots)
    dates = [d for d, _, _, _ in slots]
    assert dates == sorted(dates)
