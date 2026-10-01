"""08:00 발행기는 생성 워커가 잡은 슬롯의 참고자료를 재검증하지 않는다 — 실제 Postgres.

07:45 게이트(3차 F4)와 같은 규칙이다. 살아 있는 claim(`generation_claim_is_active`)이 있으면
잠금 없는 읽기에서 GET하지 않고, GET 사이에 claim이 생겼으면 잠근 뒤 적용하지 않는다 — 판이
오르면 워커가 공급자 비용을 치른 저장이 판 불일치로 버려진다. 만료된 claim은 종전처럼 연다.
실제 행의 claim 열·판·참고자료·검증 기록을 DB에서 다시 읽어 확인한다. 네트워크는 쓰지 않는다.
"""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus
from app.services.reference_verification import ReferenceVerifier, reference_check_record
from app.workers import tasks
from tests.reference_fetch_doubles import PageFetcher

URL = (
    "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/"
    "gnrlzHealthInfoView.do?cntnts_sn=9601"
)
TITLE = "요통이 오래갈 때 — 원인과 치료"


class _SessionProxy:
    """`with SyncSessionLocal() as db:`를 테스트 세션에 그대로 붙인다."""

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    def __enter__(self):
        return self._session

    def __exit__(self, *exc):
        return False


@pytest.fixture
def pg_session(pg_conn):
    session = Session(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def worker_db(pg_session, monkeypatch):
    monkeypatch.setattr(tasks, "SyncSessionLocal", _SessionProxy(pg_session))
    return pg_session


def _stale_item(db, *, claimed_at: datetime | None) -> ContentItem:
    hospital = Hospital(
        name="참고자료claim의원",
        slug=f"ref-claim-{uuid.uuid4().hex[:10]}",
        status=HospitalStatus.ACTIVE,
        site_live=True,
        profile_complete=True,
        site_built=True,
        schedule_set=True,
    )
    db.add(hospital)
    db.flush()
    schedule = ContentSchedule(
        hospital_id=hospital.id, plan="PLAN_12", publish_days=[1, 3], active_from=date(2026, 9, 1)
    )
    db.add(schedule)
    db.flush()
    stale_at = datetime.now(timezone.utc) - timedelta(days=3)  # 24시간 신선도 밖
    item = ContentItem(
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.DISEASE,
        sequence_no=1,
        total_count=12,
        title=TITLE,
        body="## 요통의 원인과 치료\n요통 진료 안내",
        scheduled_date=tasks.arrow.now("Asia/Seoul").date(),
        status=ContentStatus.DRAFT,
        references_list=[{"title": "요통", "url": URL}],
        reference_checks=[
            reference_check_record(
                URL,
                verdict="pass",
                reason="page_verified",
                checked_at=stale_at,
                curated=False,
                status=200,
                final_url=URL,
                page_title="요통 | 국가건강정보포털 | 질병관리청",
                text_len=800,
                verified_at=stale_at,
            )
        ],
        generation_claim_token=uuid.uuid4() if claimed_at is not None else None,
        generation_claimed_at=claimed_at,
    )
    db.add(item)
    db.flush()
    return item


def _stored(db, item_id):
    db.expire_all()
    row = db.get(ContentItem, item_id)
    return row.content_revision, row.references_list, row.reference_checks, row.status


def _dead_link_fetcher() -> PageFetcher:
    # 재검증이 참고자료를 바꾼다(404 → 제거·치유) — 적용되면 판이 오른다.
    return PageFetcher({URL: (404, URL, "")})


@pytest.fixture
def before_last_publisher(monkeypatch):
    """예정일 23시(마지막 발행기)부터는 claim 행도 GET한다(`reference_outage_alert_due`).

    claim을 존중하는 규칙을 보는 테스트는 그 전의 발행기여야 한다 — 실제 시계를 쓰면
    23시 이후 CI에서만 실패한다. 오늘 KST 10시로 고정한다.
    """
    real_now = tasks.arrow.now
    monkeypatch.setattr(
        tasks.arrow, "now", lambda *args, **kwargs: real_now(*args, **kwargs).replace(hour=10)
    )


def test_eight_does_not_refresh_a_row_a_live_worker_is_writing(worker_db, before_last_publisher):
    item = _stale_item(worker_db, claimed_at=datetime.now(timezone.utc) - timedelta(minutes=10))
    before = _stored(worker_db, item.id)
    fetcher = _dead_link_fetcher()
    verifier = ReferenceVerifier(fetcher, domain_spacing=0)

    assert tasks._prefetch_publication_references(item.id, verifier) is None
    assert tasks._auto_publish_one(item.id, reference_verifier=verifier) is None

    assert fetcher.calls == []
    assert _stored(worker_db, item.id) == before
    assert before[3] is ContentStatus.DRAFT


def test_eight_does_not_apply_a_refresh_when_a_worker_claims_during_the_get(
    worker_db, before_last_publisher
):
    item = _stale_item(worker_db, claimed_at=None)
    before = _stored(worker_db, item.id)
    fetcher = _dead_link_fetcher()
    real_fetch = fetcher.__call__

    async def claiming_fetch(url):
        # GET 사이에 생성 워커가 이 행을 잡았다(claim 열을 실제로 쓴다).
        row = worker_db.get(ContentItem, item.id)
        row.generation_claim_token = uuid.uuid4()
        row.generation_claimed_at = datetime.now(timezone.utc)
        worker_db.flush()
        return await real_fetch(url)

    assert tasks._auto_publish_one(
        item.id, reference_verifier=ReferenceVerifier(claiming_fetch, domain_spacing=0)
    ) is None

    assert fetcher.calls[0] == URL  # 재검증(과 수기 목록 치유)은 GET까지 했다
    revision, references, checks, status = _stored(worker_db, item.id)
    assert (revision, references, checks, status) == before


def test_eight_refreshes_a_row_whose_claim_expired(worker_db):
    """대조군 — 만료된 claim은 살아 있는 작업이 아니다. 같은 404가 참고자료를 바꾸고 판을 올린다."""

    item = _stale_item(worker_db, claimed_at=datetime.now(timezone.utc) - timedelta(days=2))
    revision_before, references_before, *_ = _stored(worker_db, item.id)
    fetcher = _dead_link_fetcher()

    tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0))

    assert fetcher.calls[0] == URL
    revision, references, _checks, _status = _stored(worker_db, item.id)
    assert URL not in [ref.get("url") for ref in references or []]
    assert references != references_before and revision > revision_before
