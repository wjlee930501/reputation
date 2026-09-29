"""생성 claim의 활성 판정과 종료 해제 — SQL 없이 확인할 수 있는 계약.

- `generation_claim_is_active`는 `load_claimed_generation_item`과 같이 naive
  `generation_claimed_at`을 UTC로 읽는다. naive 값과 aware 시각을 그대로 비교하면
  TypeError가 나고, KST로 읽으면 살아 있는 claim이 9시간 늙어 만료로 보인다.
- 워커의 자기 토큰 해제는 복구 필터(`_needs_generation_recovery`)에 매이지 않는다.
  토큰 없는 해제만 종전처럼 복구 필터로 좁힌다. 실제 UPDATE 술어는
  `tests/integration/test_generation_claim_release_postgres.py`가 본다.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy.dialects import postgresql

from app.workers.nightly_generation_batch import (
    NIGHTLY_GENERATION_CLAIM_TTL_HOURS,
    generation_claim_is_active,
    release_unfinished_claims,
)

KST = ZoneInfo("Asia/Seoul")
# 07:45 게이트가 넘기는 aware 시각(= 2026-09-15 22:45 UTC).
GATE_AT = datetime(2026, 9, 16, 7, 45, tzinfo=KST)
TTL = timedelta(hours=NIGHTLY_GENERATION_CLAIM_TTL_HOURS)


def _row(claimed_at):
    return SimpleNamespace(generation_claim_token=uuid.uuid4(), generation_claimed_at=claimed_at)


def _naive_utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


def test_a_naive_live_claim_is_read_as_utc_against_an_aware_now():
    claimed = _naive_utc(GATE_AT - timedelta(minutes=35))

    assert generation_claim_is_active(_row(claimed), now=GATE_AT) is True


def test_a_naive_expired_claim_is_read_as_utc_against_an_aware_now():
    claimed = _naive_utc(GATE_AT - TTL - timedelta(minutes=1))

    assert generation_claim_is_active(_row(claimed), now=GATE_AT) is False


def test_the_ttl_boundary_is_inclusive_for_naive_claims():
    """경계는 `claim_generation_lease`와 같다 — 정확히 TTL 전의 claim은 아직 살아 있다."""

    at_boundary = _naive_utc(GATE_AT - TTL)
    just_past = at_boundary - timedelta(microseconds=1)

    assert generation_claim_is_active(_row(at_boundary), now=GATE_AT) is True
    assert generation_claim_is_active(_row(just_past), now=GATE_AT) is False


def test_naive_as_utc_is_not_naive_as_kst():
    """같은 naive 값(35분 전 UTC)을 KST로 읽으면 9시간 35분 전이 돼 만료로 보인다."""

    claimed = _naive_utc(GATE_AT - timedelta(minutes=35))

    assert generation_claim_is_active(_row(claimed), now=GATE_AT) is True
    assert generation_claim_is_active(_row(claimed.replace(tzinfo=KST)), now=GATE_AT) is False


def test_aware_claims_keep_their_meaning():
    assert generation_claim_is_active(_row(GATE_AT - timedelta(minutes=35)), now=GATE_AT)
    assert not generation_claim_is_active(_row(GATE_AT - TTL - timedelta(seconds=1)), now=GATE_AT)
    assert not generation_claim_is_active(
        SimpleNamespace(generation_claim_token=None, generation_claimed_at=GATE_AT), now=GATE_AT
    )


class _CaptureDB:
    def __init__(self):
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(rowcount=0)


def _where_sql(**kwargs) -> str:
    db = _CaptureDB()
    release_unfinished_claims(db, [uuid.uuid4()], **kwargs)
    (statement,) = db.statements
    return str(statement.whereclause.compile(dialect=postgresql.dialect()))


def test_the_owner_token_release_does_not_depend_on_the_recovery_filter():
    """끝난 워커는 행이 어떤 이유로 막혀 있든 자기 claim을 푼다 — 토큰이 소유를 증명한다."""

    where = _where_sql(expected_claim_token=uuid.uuid4())

    assert "content_items.generation_claim_token = " in where
    assert "content_items.body IS NULL" not in where
    assert "jsonb_typeof" not in where


def test_the_tokenless_release_keeps_the_recovery_filter():
    where = _where_sql()

    assert "content_items.body IS NULL" in where
    assert "jsonb_typeof" in where
    assert "generation_claim_token" not in where
