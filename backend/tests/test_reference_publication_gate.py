"""발행 직전 참고자료 게이트 — 07:45·08:00 워커, 관리자 수정·수동 발행, 운영자 문구.

계약: 모든 참고자료에 같은 URL의 신선한 통과 기록이 있어야 공개한다. 없거나 오래됐으면
워커가 다시 검증하고, 떨어진 항목은 빼고, 전부 빠지면 이 글의 주제로 채점한 수기 목록으로
채우며, 그래도 없으면 발행을 보류하고 실제 원인(참고자료 확보 실패)을 말하는 인시던트를 연다.
네트워크는 쓰지 않는다 — 가짜 fetcher만 쓴다.
"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import arrow
import httpx
import pytest
from fastapi import HTTPException

from app.api.admin import content as content_api
from app.services.audit_log import reset_request_actor, set_request_actor
from app.services.content_engine import MissingCitableReferencesError
from app.services.content_row_state import REFERENCE_RETRY_ROW_REASON, content_row_state
from app.services.content_visibility import PublicVisibility
from app.services.reference_publication import (
    publication_references_current,
    refresh_publication_references,
    verify_publication_references,
)
from app.services.reference_verification import (
    REFERENCE_CHECK_MAX_AGE,
    ReferenceVerifier,
    item_topic_fingerprint,
    override_reference_fetcher,
    reference_check_record,
)
from app.workers import tasks
from app.workers.generation_incident_control import (
    REFERENCE_REJECTION_OPERATOR_ACTION,
    _generation_operator_copy,
    generation_operator_action,
    generation_safe_cause,
)
from app.workers.generation_run_control import (
    GENERATION_REFERENCE_REJECTION_MESSAGE,
    classify_generation_failure,
)
from tests.reference_fetch_doubles import PageFetcher, document_body, page_html
from tests.test_content_compliance_faq import (
    _content_item,
    _hospital,
    _NoExecuteDB,
    _PatchDB,
    _wire,
)
from tests.test_tasks_nightly import (
    _approved_philosophy,
    _arm_external_effect_tripwires,
    _AutoPublishDB,
    _publication_hospital,
    _publication_item,
)

KDCA_VIEW = (
    "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/"
    "gnrlzHealthInfoView.do?cntnts_sn={}"
)
CURATED_HEMORRHOID_KDCA = KDCA_VIEW.format(5818)
CURATED_HEMORRHOID_AMC = (
    "https://www.amc.seoul.kr/asan/healthinfo/disease/diseaseDetail.do?contentId=31772"
)
GUESSED = KDCA_VIEW.format(2480)
HEMORRHOID_TITLE = "치질 수술 후 회복 기간과 통증 관리"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pass(url: str, *, age: timedelta = timedelta(hours=1), verified_age: timedelta | None = None):
    checked = _now() - age
    return reference_check_record(
        url,
        verdict="pass",
        reason="page_verified",
        checked_at=checked,
        curated=False,
        status=200,
        final_url=url,
        page_title="치핵 | 국가건강정보포털 | 질병관리청",
        text_len=900,
        verified_at=_now() - (verified_age if verified_age is not None else age),
    )


def _stamp(item):
    """저장된 통과 기록을 지금 글의 주제에 묶는다(생성·PATCH가 남기는 기록과 같은 모양)."""
    for check in getattr(item, "reference_checks", None) or []:
        check["topic_fingerprint"] = item_topic_fingerprint(item)
    return item


def _hemorrhoid_fetcher(**pages) -> PageFetcher:
    fetcher = PageFetcher(pages)
    for url in (CURATED_HEMORRHOID_KDCA, CURATED_HEMORRHOID_AMC):
        if url not in fetcher.pages:
            fetcher.add_document(url, "치핵 | 국가건강정보포털 | 질병관리청", topic="치핵")
    return fetcher


def _publish_setup(monkeypatch, *, references, checks, title=HEMORRHOID_TITLE):
    hospital = _publication_hospital()
    item = _publication_item(hospital, body="진료 기준과 내원 시점을 안내합니다.", title=title)
    item.references_list = references
    item.reference_checks = checks
    item.content_revision = 3
    _stamp(item)
    db = _AutoPublishDB(item, hospital)
    effects = _arm_external_effect_tripwires(monkeypatch)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(
        tasks, "get_current_approved_philosophy_sync", lambda *_args: _approved_philosophy()
    )
    monkeypatch.setattr(tasks, "ensure_site_revalidate_configured", lambda: None)
    monkeypatch.setattr(tasks.arrow, "now", lambda *_a, **_k: arrow.get(2026, 6, 10, 8, 0, tzinfo="Asia/Seoul"))
    return item, db, effects


# ── 08:00 발행기 ────────────────────────────────────────────────────────────


def test_fresh_passing_check_for_the_same_url_publishes_without_a_get(monkeypatch):
    url = KDCA_VIEW.format(9001)
    item, _db, _effects = _publish_setup(
        monkeypatch, references=[{"title": "치핵", "url": url}], checks=[_pass(url)]
    )
    fetcher = _hemorrhoid_fetcher()

    payload = tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(fetcher))

    assert payload is not None and payload.get("kind") != "blocked"
    assert item.status is tasks.ContentStatus.PUBLISHED
    assert fetcher.calls == []


def test_stale_check_is_reverified_before_publishing(monkeypatch):
    url = KDCA_VIEW.format(9002)
    stale = _pass(url, age=REFERENCE_CHECK_MAX_AGE + timedelta(hours=2))
    item, _db, _effects = _publish_setup(
        monkeypatch, references=[{"title": "치핵", "url": url}], checks=[stale]
    )
    fetcher = _hemorrhoid_fetcher()
    fetcher.add_document(url, "치핵 | 국가건강정보포털 | 질병관리청", topic="치핵")

    tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0))

    assert fetcher.calls == [url]
    assert item.status is tasks.ContentStatus.PUBLISHED
    assert publication_references_current(item)
    assert item.reference_checks[0]["checked_at"] > stale["checked_at"]


def test_check_for_a_different_url_does_not_count(monkeypatch):
    """PATCH·치유로 주소가 바뀌었는데 옛 주소의 통과 기록이 남아 있으면 다시 검증한다."""
    url = KDCA_VIEW.format(9003)
    item, _db, _effects = _publish_setup(
        monkeypatch,
        references=[{"title": "치핵", "url": url}],
        checks=[_pass(KDCA_VIEW.format(9004))],
    )
    fetcher = _hemorrhoid_fetcher()  # url은 fetcher에 없다 → 404

    payload = tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0))

    assert url in fetcher.calls
    # 죽은 링크를 빼고 이 글의 주제로 수기 목록에서 채워 발행한다.
    assert payload.get("kind") != "blocked"
    assert url not in [ref["url"] for ref in item.references_list]


def test_domain_outage_reuses_the_previous_pass_instead_of_holding(monkeypatch):
    url = KDCA_VIEW.format(9005)
    previous = _pass(url, age=REFERENCE_CHECK_MAX_AGE + timedelta(hours=6))
    item, _db, _effects = _publish_setup(
        monkeypatch, references=[{"title": "치핵", "url": url}], checks=[previous]
    )
    fetcher = PageFetcher({url: httpx.ConnectError("kdca down")})

    tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0))

    assert item.status is tasks.ContentStatus.PUBLISHED
    assert [ref["url"] for ref in item.references_list] == [url]
    assert item.reference_checks[0]["reason"] == "reused_previous_pass"


def test_nothing_left_is_healed_from_the_topic_matched_curated_list(monkeypatch):
    item, _db, _effects = _publish_setup(
        monkeypatch, references=[{"title": "치핵 자료", "url": GUESSED}], checks=None
    )
    fetcher = _hemorrhoid_fetcher()
    fetcher.pages[GUESSED] = (
        200,
        "https://health.kdca.go.kr/healthinfo/biz/health/main/mainPage/main.do",
        page_html("| 국가건강정보포털 | 질병관리청", "메뉴 " * 100),
    )

    tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0))

    healed = [ref["url"] for ref in item.references_list]
    assert healed and set(healed) <= {CURATED_HEMORRHOID_KDCA, CURATED_HEMORRHOID_AMC}
    assert item.status is tasks.ContentStatus.PUBLISHED
    reasons = {check["url"]: check["reason"] for check in item.reference_checks}
    assert reasons[GUESSED] == "soft_404_redirect"
    assert item.content_revision == 4  # 참고자료가 바뀐 판


def test_heal_never_borrows_a_curated_document_from_another_topic(monkeypatch):
    """고혈압 글이 죽은 링크를 잃었다고 치핵 문서를 받지 않는다(주제 채점)."""
    item, _db, _effects = _publish_setup(
        monkeypatch,
        references=[{"title": "고혈압", "url": GUESSED}],
        checks=None,
        title="창원 고혈압 진료 병원 — 혈압 관리 방향",
    )
    fetcher = _hemorrhoid_fetcher()
    fetcher.add_document(
        "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/"
        "gnrlzHealthInfoView.do?cntnts_sn=6765",
        "고혈압 | 국가건강정보포털 | 질병관리청",
        topic="고혈압",
    )

    tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0))

    urls = [ref["url"] for ref in item.references_list]
    assert urls and all("cntnts_sn=6765" in url for url in urls)


def test_still_nothing_holds_publication_with_the_real_cause(monkeypatch):
    item, db, effects = _publish_setup(
        monkeypatch,
        references=[{"title": "경산 내과 진료비", "url": GUESSED}],
        checks=None,
        title="경산 내과 진료비, 무엇에 따라 달라지나요",
    )
    fetcher = PageFetcher({GUESSED: (404, GUESSED, "")})

    payload = tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0))

    assert payload["kind"] == "blocked"
    assert payload["code"] == "MISSING_REFERENCES"
    assert "공신력 있는 참고 자료를 확보하지 못했습니다" in payload["message"]
    assert item.status is tasks.ContentStatus.DRAFT
    assert item.references_list == []
    assert item.reference_checks[0]["reason"] == "dead_link"
    assert effects == {"revalidate": [], "indexnow": []}
    # 운영자 인시던트·요약에 실리는 원인과 조치도 같은 원인을 말한다.
    assert "참고 자료를 확보하지 못해" in generation_safe_cause("MISSING_REFERENCES")
    assert "실제 문서 확인" in generation_operator_action("MISSING_REFERENCES")


def test_run_budget_exhaustion_defers_without_blocking(monkeypatch):
    url = KDCA_VIEW.format(9006)
    item, db, _effects = _publish_setup(
        monkeypatch, references=[{"title": "치핵", "url": url}], checks=None
    )
    fetcher = _hemorrhoid_fetcher()

    payload = tasks._auto_publish_one(
        item.id, reference_verifier=ReferenceVerifier(fetcher, max_fetches=0)
    )

    assert payload is None
    assert fetcher.calls == []
    assert item.status is tasks.ContentStatus.DRAFT
    assert [ref["url"] for ref in item.references_list] == [url]
    assert not [log for log in db.added if getattr(log, "action", None) == "auto_publish_blocked"]


def test_publisher_never_publishes_when_the_gate_is_not_current(monkeypatch):
    """재검증 뒤에도 신선한 통과 기록이 없으면(경합 등) 공개하지 않고 다음 시간대로 미룬다."""
    url = KDCA_VIEW.format(9007)
    item, _db, _effects = _publish_setup(
        monkeypatch, references=[{"title": "치핵", "url": url}], checks=None
    )
    # 재검증 결과가 없는데(경합·다른 작업이 기록을 바꿈) 게이트가 current가 아닌 상태.
    monkeypatch.setattr(tasks, "_prefetch_publication_references", lambda *_a, **_k: None)

    assert tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(PageFetcher())) is None
    assert item.status is tasks.ContentStatus.DRAFT


def test_morning_publisher_shares_one_verifier_across_the_run(monkeypatch):
    seen: list[object] = []

    def publish(content_id, *, reference_verifier, today_kst=None):
        seen.append(reference_verifier)
        return None

    ids = [uuid.uuid4(), uuid.uuid4()]

    class _DB:
        def execute(self, _stmt):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: ids))

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(tasks, "_auto_publish_one", publish)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_a, **_k: None)

    tasks.morning_content_auto_publish.run()

    assert len(seen) == 2 and seen[0] is seen[1]
    assert isinstance(seen[0], ReferenceVerifier)


# ── 07:45 게이트 ────────────────────────────────────────────────────────────


class _GateDB:
    def __init__(self, item):
        self.item = item
        self.commits = 0
        self.added = []
        self.locks = 0

    def execute(self, stmt):
        if getattr(stmt, "_for_update_arg", None) is not None:
            self.locks += 1
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [self.item]),
            scalar_one_or_none=lambda: self.item,
        )

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1


def _gate_setup(monkeypatch, item):
    incidents: list[dict] = []

    async def capture_incident(**kwargs):
        incidents.append(kwargs)

    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", lambda *_a: _approved_philosophy())
    monkeypatch.setattr(
        tasks, "ensure_publication_block_run", lambda *_a, **_k: SimpleNamespace(id=uuid.uuid4())
    )
    monkeypatch.setattr(tasks, "_record_gate_blocker_decision", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks, "open_generation_incident", capture_incident)
    return incidents


def test_seven_forty_five_reverifies_stale_references_so_eight_does_not_burst(monkeypatch):
    url = KDCA_VIEW.format(9101)
    hospital = _publication_hospital()
    item = _publication_item(hospital, body="진료 기준을 안내합니다.", title=HEMORRHOID_TITLE)
    item.references_list = [{"title": "치핵", "url": url}]
    item.reference_checks = [_pass(url, age=timedelta(hours=33))]
    _stamp(item)
    db = _GateDB(item)
    _gate_setup(monkeypatch, item)
    fetcher = _hemorrhoid_fetcher()
    fetcher.add_document(url, "치핵 | 국가건강정보포털 | 질병관리청", topic="치핵")

    with override_reference_fetcher(fetcher):
        tasks._page_morning_stored_publication_gates(
            db, now_kst=arrow.get(2026, 6, 10, 7, 45, tzinfo="Asia/Seoul")
        )

    assert fetcher.calls == [url]
    assert db.locks == 1
    assert publication_references_current(item)


def _stale_claimable_item():
    url = KDCA_VIEW.format(9101)
    hospital = _publication_hospital()
    item = _publication_item(hospital, body="진료 기준을 안내합니다.", title=HEMORRHOID_TITLE)
    item.references_list = [{"title": "치핵", "url": url}]
    item.reference_checks = [_pass(url, age=timedelta(hours=33))]
    item.content_revision = 5
    _stamp(item)
    fetcher = _hemorrhoid_fetcher()
    fetcher.add_document(url, "치핵 | 국가건강정보포털 | 질병관리청", topic="치핵")
    return item, url, fetcher


def _claim(item, at):
    item.generation_claim_token = uuid.uuid4()
    item.generation_claimed_at = at


def test_seven_forty_five_does_not_refresh_a_slot_a_live_worker_is_writing(monkeypatch):
    """3차 F4 — 살아 있는 claim이 있는 슬롯은 참고자료 재검증(GET·잠금·판 올림)부터 건너뛴다.

    재검증이 판을 올리면 워커가 공급자 비용을 치른 저장이 판 불일치로 버려진다.
    """

    item, _url, fetcher = _stale_claimable_item()
    observed = arrow.get(2026, 6, 10, 7, 45, tzinfo="Asia/Seoul")
    _claim(item, observed.datetime - timedelta(minutes=10))
    before = (copy.deepcopy(item.references_list), copy.deepcopy(item.reference_checks))
    db = _GateDB(item)
    incidents = _gate_setup(monkeypatch, item)

    with override_reference_fetcher(fetcher):
        paged = tasks._page_morning_stored_publication_gates(db, now_kst=observed)

    assert fetcher.calls == [] and db.locks == 0
    assert (paged, incidents) == (0, [])
    assert item.content_revision == 5
    assert (item.references_list, item.reference_checks) == before


def test_seven_forty_five_does_not_apply_a_refresh_when_a_worker_claims_during_the_get(
    monkeypatch,
):
    item, url, fetcher = _stale_claimable_item()
    fetcher.pages[url] = (404, url, "")  # 재검증이 참고자료를 바꾼다(빼고 치유) — 판이 오를 일이다
    before = copy.deepcopy(item.references_list)
    observed = arrow.get(2026, 6, 10, 7, 45, tzinfo="Asia/Seoul")
    real_fetch = fetcher.__call__

    async def claiming_fetch(fetched_url):
        _claim(item, observed.datetime)  # GET 사이에 07시 스윕의 워커가 잡았다
        return await real_fetch(fetched_url)

    db = _GateDB(item)
    incidents = _gate_setup(monkeypatch, item)

    with override_reference_fetcher(claiming_fetch):
        paged = tasks._page_morning_stored_publication_gates(db, now_kst=observed)

    assert fetcher.calls[0] == url and db.locks == 1
    assert (paged, incidents) == (0, [])
    assert (item.content_revision, item.references_list) == (5, before)


def test_seven_forty_five_refreshes_a_slot_whose_claim_expired(monkeypatch):
    """대조군 — 만료된 claim은 살아 있는 작업이 아니다(종전처럼 재검증한다)."""

    item, url, fetcher = _stale_claimable_item()
    observed = arrow.get(2026, 6, 10, 7, 45, tzinfo="Asia/Seoul")
    _claim(item, observed.datetime - timedelta(days=2))
    db = _GateDB(item)
    _gate_setup(monkeypatch, item)

    with override_reference_fetcher(fetcher):
        tasks._page_morning_stored_publication_gates(db, now_kst=observed)

    assert fetcher.calls == [url] and db.locks == 1
    assert publication_references_current(item)


def test_seven_forty_five_does_not_page_a_slot_a_worker_claims_after_the_refresh(monkeypatch):
    """재검증을 적용한 뒤, 판정 전에 생성 워커가 잡은 슬롯 — 보류·인시던트를 만들지 않는다.

    대조군(`..._holds_and_pages_the_real_cause_when_nothing_survives`)과 같은 글이 claim만 없으면
    MISSING_REFERENCES로 보류된다. 판정 직후의 claim 재확인이 없으면 여기서도 보류된다.
    """

    hospital = _publication_hospital()
    item = _publication_item(hospital, body="진료비 기준을 안내합니다.", title="경산 내과 진료비 안내")
    item.references_list = [{"title": "진료비", "url": GUESSED}]
    item.reference_checks = None
    db = _GateDB(item)
    incidents = _gate_setup(monkeypatch, item)
    observed = arrow.get(2026, 6, 10, 7, 45, tzinfo="Asia/Seoul")
    claimed_after_apply: list[bool] = []

    def philosophy_after_a_claim(*_args):
        # 적용·commit 뒤 판정 직전 — 07시 스윕의 워커가 이 슬롯을 잡았다.
        claimed_after_apply.append(item.references_list == [])
        _claim(item, observed.datetime)
        return _approved_philosophy()

    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", philosophy_after_a_claim)

    with override_reference_fetcher(PageFetcher({GUESSED: (404, GUESSED, "")})):
        paged = tasks._page_morning_stored_publication_gates(db, now_kst=observed)

    assert claimed_after_apply == [True]  # 재검증은 적용됐다(claim 전이다)
    assert (paged, incidents) == (0, [])


def test_seven_forty_five_holds_and_pages_the_real_cause_when_nothing_survives(monkeypatch):
    hospital = _publication_hospital()
    item = _publication_item(hospital, body="진료비 기준을 안내합니다.", title="경산 내과 진료비 안내")
    item.references_list = [{"title": "진료비", "url": GUESSED}]
    item.reference_checks = None
    db = _GateDB(item)
    incidents = _gate_setup(monkeypatch, item)

    with override_reference_fetcher(PageFetcher({GUESSED: (404, GUESSED, "")})):
        paged = tasks._page_morning_stored_publication_gates(
            db, now_kst=arrow.get(2026, 6, 10, 7, 45, tzinfo="Asia/Seoul")
        )

    assert paged == 1
    assert incidents[0]["code"] == "MISSING_REFERENCES"
    assert "공신력 있는 참고 자료를 확보하지 못했습니다" in incidents[0]["message"]
    assert item.references_list == []


# ── 생성 단계의 참고자료 거절 문구 (점검 §4: 가격·지역 게이트 문구로 보였다) ─────────


def test_generation_reference_rejection_is_labelled_as_a_reference_failure():
    error = MissingCitableReferencesError(
        "GEO hard-fail: references is empty for FAQ — 검증을 통과한 학회/KDCA 출처 없음.",
        {},
    )

    code, message = classify_generation_failure(error)

    assert code == "GENERATION_REJECTED"
    assert message == GENERATION_REFERENCE_REJECTION_MESSAGE
    assert "가격" not in message
    _impact, action = _generation_operator_copy(code, message)
    assert action == REFERENCE_REJECTION_OPERATOR_ACTION
    assert "가격·지역·검색 구조" not in action
    # 다른 생성 거절은 종전 문구 그대로다.
    assert "가격·지역·검색 구조" in _generation_operator_copy(code, "다른 거절")[1]


def test_admin_row_shows_the_reference_retry_instead_of_nightly_generation():
    item = SimpleNamespace(
        status="DRAFT",
        title=None,
        body=None,
        scheduled_date=datetime(2099, 9, 30).date(),
        essence_check_summary={
            "generation_attempt": {
                "reason": "GENERATION_REJECTED",
                "message": GENERATION_REFERENCE_REJECTION_MESSAGE,
            }
        },
    )
    visibility = PublicVisibility(visible=False, blockers=())

    state = content_row_state(
        item, visibility, compliance_blockers=(), blocked_link=None, today=datetime(2099, 9, 29).date()
    )

    assert state.reason == REFERENCE_RETRY_ROW_REASON
    assert state.reason != "발행 전날 23:00 자동 생성"


# ── 관리자 PATCH·수동 발행 ────────────────────────────────────────────────────


async def test_admin_patch_rejects_a_reference_that_fails_verification(monkeypatch):
    hospital = _hospital()
    item = _content_item(hospital_id=hospital.id, title=HEMORRHOID_TITLE, references_list=[])
    _wire(monkeypatch, item, hospital)
    fetcher = _hemorrhoid_fetcher()
    fetcher.pages[GUESSED] = (200, GUESSED, page_html("| 국가건강정보포털 | 질병관리청", "메뉴 " * 100))

    with override_reference_fetcher(fetcher), pytest.raises(HTTPException) as raised:
        await content_api.update_content(
            hospital.id,
            item.id,
            content_api.ContentPatch(
                references=[
                    {"title": "치핵", "url": CURATED_HEMORRHOID_KDCA},
                    {"title": "치질 수술 안내", "url": GUESSED},
                ]
            ),
            db=_PatchDB(hospital),
        )

    assert raised.value.status_code == 400
    detail = raised.value.detail
    assert GUESSED in detail["message"]
    assert "제목·본문이 빈 페이지" in detail["message"]
    assert [entry["url"] for entry in detail["failed_references"]] == [GUESSED]
    assert item.references_list == []  # 아무것도 저장하지 않는다


async def test_admin_patch_stores_the_checks_of_accepted_references(monkeypatch):
    hospital = _hospital()
    item = _content_item(hospital_id=hospital.id, title=HEMORRHOID_TITLE, references_list=[])
    item.content_revision = 1
    _wire(monkeypatch, item, hospital)
    outside = KDCA_VIEW.format(9201)
    fetcher = _hemorrhoid_fetcher()
    fetcher.add_document(outside, "치핵 | 국가건강정보포털 | 질병관리청", topic="치핵")

    with override_reference_fetcher(fetcher):
        await content_api.update_content(
            hospital.id,
            item.id,
            content_api.ContentPatch(
                references=[
                    {"title": "치핵", "url": CURATED_HEMORRHOID_KDCA},
                    {"title": "질병관리청 치핵", "url": outside},
                ]
            ),
            db=_PatchDB(hospital),
        )

    assert [ref["url"] for ref in item.references_list] == [CURATED_HEMORRHOID_KDCA, outside]
    assert publication_references_current(item)
    reasons = {check["url"]: check["reason"] for check in item.reference_checks}
    assert reasons == {CURATED_HEMORRHOID_KDCA: "curated_verified", outside: "page_verified"}


async def test_admin_patch_offline_rejects_an_unverifiable_outside_url(monkeypatch):
    """운영 외 환경 기본값: 실제로 열어 볼 수 없는 목록 밖 주소는 저장하지 않는다."""
    hospital = _hospital()
    item = _content_item(hospital_id=hospital.id, title=HEMORRHOID_TITLE, references_list=[])
    _wire(monkeypatch, item, hospital)

    with pytest.raises(HTTPException) as raised:
        await content_api.update_content(
            hospital.id,
            item.id,
            content_api.ContentPatch(references=[{"title": "치핵", "url": KDCA_VIEW.format(9202)}]),
            db=_PatchDB(hospital),
        )

    assert raised.value.status_code == 400
    assert raised.value.detail["failed_references"][0]["reason"] == "not_verified"


async def test_admin_manual_publish_runs_the_same_reference_gate(monkeypatch):
    hospital = _hospital()
    item = _content_item(
        hospital_id=hospital.id,
        title="경산 내과 진료비 안내",
        references_list=[{"title": "진료비", "url": GUESSED}],
        faq_question="경산 내과 진료비는 무엇에 따라 달라지나요?",
        faq_answer_summary="검사 범위와 보험 적용에 따라 달라집니다.",
    )
    item.reference_checks = None
    _wire(monkeypatch, item, hospital)
    token = set_request_actor("ae@example.com")
    try:
        with override_reference_fetcher(PageFetcher({GUESSED: (404, GUESSED, "")})):
            with pytest.raises(HTTPException) as raised:
                await content_api.publish_content(
                    hospital.id, item.id, content_api.PublishBody(), db=_NoExecuteDB()
                )
    finally:
        reset_request_actor(token)

    assert raised.value.status_code == 400, raised.value.detail
    assert raised.value.detail["missing"] == "references", raised.value.detail
    assert item.references_list == []
    assert item.reference_checks[0]["reason"] == "dead_link"


async def test_refresh_reports_already_current_without_touching_the_row():
    url = KDCA_VIEW.format(9301)
    item = SimpleNamespace(
        title=HEMORRHOID_TITLE,
        body="",
        content_brief=None,
        faq_question=None,
        content_type="FAQ",
        references_list=[{"title": "치핵", "url": url}],
        reference_checks=[_pass(url)],
    )
    _stamp(item)
    fetcher = PageFetcher()

    refresh = await refresh_publication_references(item, ReferenceVerifier(fetcher))

    assert refresh.already_current and not refresh.references_changed
    assert fetcher.calls == []


def test_page_html_double_is_long_enough_for_the_empty_template_rule():
    assert len(document_body("치핵")) > 200


# ── Pass 2: 기관 사이트 일시 장애는 미룸(제거·치유·보류 없음) ──────────────────


def test_publish_time_outage_without_a_prior_pass_defers_instead_of_holding(monkeypatch):
    """직전 통과가 없는 목록 밖 URL의 기관 사이트가 내려가 있으면 빼지 않고 미룬다."""
    url = KDCA_VIEW.format(9401)
    item, db, effects = _publish_setup(
        monkeypatch, references=[{"title": "치핵", "url": url}], checks=None
    )
    fetcher = PageFetcher({url: httpx.ConnectError("kdca down")})

    payload = tasks._auto_publish_one(
        item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0)
    )

    assert payload["kind"] == "reference_deferred"
    assert payload["unreachable_urls"] == [url]
    assert item.status is tasks.ContentStatus.DRAFT
    assert item.references_list == [{"title": "치핵", "url": url}]  # 빼지도 채우지도 않는다
    assert item.content_revision == 3
    assert item.reference_checks[0]["verdict"] == "deferred"
    assert item.reference_checks[0]["reason"] == "site_unreachable"
    assert not [log for log in db.added if getattr(log, "action", None) == "auto_publish_blocked"]
    assert effects == {"revalidate": [], "indexnow": []}


def test_publish_time_definitive_failure_still_removes_and_heals(monkeypatch):
    url = KDCA_VIEW.format(9402)
    item, _db, _effects = _publish_setup(
        monkeypatch, references=[{"title": "치핵", "url": url}], checks=None
    )
    fetcher = _hemorrhoid_fetcher()  # url은 없다 → 404(확정)

    tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0))

    assert url not in [ref["url"] for ref in item.references_list]
    assert item.status is tasks.ContentStatus.PUBLISHED


def _deferred_outcome(hospital_id, scheduled_date):
    return {
        "kind": "reference_deferred",
        "hospital_id": hospital_id,
        "hospital_name": "기관장애의원",
        "title": "치질 수술 후 회복",
        "scheduled_date": scheduled_date,
        "unreachable_urls": [KDCA_VIEW.format(9403)],
    }


@pytest.mark.parametrize(
    ("now_kst", "expected"),
    [
        (arrow.get(2026, 6, 10, 8, 0, tzinfo="Asia/Seoul"), False),
        (arrow.get(2026, 6, 10, 22, 0, tzinfo="Asia/Seoul"), False),
        (arrow.get(2026, 6, 10, 23, 0, tzinfo="Asia/Seoul"), True),
        (arrow.get(2026, 6, 11, 8, 0, tzinfo="Asia/Seoul"), True),
    ],
    ids=["first-run", "same-day", "last-run-of-the-day", "catch-up-day"],
)
def test_outage_deferral_is_reported_from_the_last_publisher_run_with_its_real_cause(
    monkeypatch, now_kst, expected
):
    from datetime import date

    from app.services.notification_copy import blocker_copy

    content_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    digests: list[dict] = []

    class _DB:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, _stmt):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [content_id]))

        def commit(self):
            pass

    monkeypatch.setattr(tasks, "SyncSessionLocal", _DB)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks.arrow, "now", lambda *_a, **_k: now_kst)
    monkeypatch.setattr(
        tasks,
        "_auto_publish_one",
        lambda _id, **_kw: _deferred_outcome(hospital_id, date(2026, 6, 10)),
    )
    monkeypatch.setattr(
        tasks,
        "enqueue_generation_blocked_digest_sync",
        lambda db, day, batch, outcomes, reused_outcomes=(): digests.extend(outcomes),
    )
    incidents: list = []
    monkeypatch.setattr(tasks, "open_generation_incident", lambda **kw: incidents.append(kw))

    tasks.morning_content_auto_publish.run()

    assert incidents == []  # 인시던트·재생성 run을 만들지 않는다
    if not expected:
        assert digests == []
        return
    [entry] = digests
    assert entry["code"] == "REFERENCE_SITE_UNREACHABLE"
    assert "기관 사이트에 접속하지 못해" in entry["cause"]
    assert "확보하지 못" not in entry["cause"]
    copy = blocker_copy(entry["code"])
    assert "기관 사이트" in copy.title and "확보" not in copy.title


# ── 08:00 발행기도 살아 있는 claim의 참고자료를 재검증하지 않는다(PR #183 후속) ─────


def _stale_publish_setup(monkeypatch):
    url = KDCA_VIEW.format(9601)
    stale = _pass(url, age=REFERENCE_CHECK_MAX_AGE + timedelta(hours=2))
    item, db, effects = _publish_setup(
        monkeypatch, references=[{"title": "치핵", "url": url}], checks=[stale]
    )
    fetcher = _hemorrhoid_fetcher()
    fetcher.add_document(url, "치핵 | 국가건강정보포털 | 질병관리청", topic="치핵")
    return item, url, fetcher


def _assert_untouched(item, before):
    assert item.content_revision == 3
    assert (item.references_list, item.reference_checks) == before
    assert item.status is tasks.ContentStatus.DRAFT


def test_eight_does_not_refresh_a_slot_a_live_worker_is_writing(monkeypatch):
    """잠금 없는 읽기에서 살아 있는 claim을 보면 GET하지 않고, 잠근 뒤에도 적용·발행·보류하지 않는다."""

    item, _url, fetcher = _stale_publish_setup(monkeypatch)
    _claim(item, _now() - timedelta(minutes=10))
    before = (copy.deepcopy(item.references_list), copy.deepcopy(item.reference_checks))
    verifier = ReferenceVerifier(fetcher, domain_spacing=0)

    assert tasks._prefetch_publication_references(item.id, verifier) is None
    payload = tasks._auto_publish_one(item.id, reference_verifier=verifier)

    assert payload is None and fetcher.calls == []
    _assert_untouched(item, before)


def test_eight_does_not_apply_a_refresh_when_a_worker_claims_during_the_get(monkeypatch):
    item, url, fetcher = _stale_publish_setup(monkeypatch)
    fetcher.pages[url] = (404, url, "")  # 재검증이 참고자료를 바꾼다(빼고 치유) — 판이 오를 일이다
    before = (copy.deepcopy(item.references_list), copy.deepcopy(item.reference_checks))

    def worker_claims():
        _claim(item, _now())  # GET 사이에 생성 워커가 잡았다

    payload = tasks._auto_publish_one(
        item.id,
        reference_verifier=ReferenceVerifier(
            _MutatingFetcher(worker_claims, fetcher.pages), domain_spacing=0
        ),
    )

    assert payload is None
    _assert_untouched(item, before)


def test_eight_refreshes_a_slot_whose_claim_expired(monkeypatch):
    """대조군 — 만료된 claim은 살아 있는 작업이 아니다(종전처럼 재검증하고 발행한다)."""

    item, url, fetcher = _stale_publish_setup(monkeypatch)
    _claim(item, _now() - timedelta(days=2))

    tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0))

    assert fetcher.calls == [url]
    assert item.status is tasks.ContentStatus.PUBLISHED


def test_eight_judges_a_live_claimed_slot_whose_references_are_settled_as_before(monkeypatch):
    """확인이 끝난 참고자료(신선한 통과)는 재검증할 일이 없다 — claim과 무관하게 종전 판정이다."""

    url = KDCA_VIEW.format(9602)
    item, _db, _effects = _publish_setup(
        monkeypatch, references=[{"title": "치핵", "url": url}], checks=[_pass(url)]
    )
    _claim(item, _now() - timedelta(minutes=10))
    fetcher = _hemorrhoid_fetcher()

    payload = tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(fetcher))

    assert fetcher.calls == [] and item.content_revision == 3
    assert payload is not None and item.status is tasks.ContentStatus.PUBLISHED


# ── Pass 2: GET은 잠금 밖, 적용은 잠근 뒤 비교해서 ─────────────────────────────


class _MutatingFetcher(PageFetcher):
    """GET 도중 다른 요청이 행을 바꾼 것처럼 흉내 낸다."""

    def __init__(self, mutate, pages=None):
        super().__init__(pages)
        self._mutate = mutate
        self.locks_seen: list[int] = []

    async def __call__(self, url):
        self._mutate()
        self._mutate = lambda: None  # 동시 편집은 한 번이다
        return await super().__call__(url)


def test_publisher_fetches_before_locking_and_never_overwrites_a_concurrent_edit(monkeypatch):
    url = KDCA_VIEW.format(9501)
    item, db, _effects = _publish_setup(
        monkeypatch, references=[{"title": "치핵", "url": url}], checks=None
    )
    for_update: list[object] = []
    original_execute = db.execute

    def tracking_execute(stmt):
        if getattr(stmt, "_for_update_arg", None) is not None:
            for_update.append(stmt)
        return original_execute(stmt)

    db.execute = tracking_execute
    locks_at_fetch: list[int] = []
    concurrent = [{"title": "치핵(사람이 고친 주소)", "url": CURATED_HEMORRHOID_KDCA}]

    def concurrent_patch():
        locks_at_fetch.append(len(for_update))
        item.references_list = concurrent
        item.content_revision = 4

    fetcher = _MutatingFetcher(concurrent_patch, {url: (404, url, "")})

    payload = tasks._auto_publish_one(
        item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0)
    )

    assert locks_at_fetch == [0]  # GET하는 동안 잡은 행 잠금이 없다
    assert payload is None
    assert item.references_list == concurrent  # 동시 편집을 덮어쓰지 않는다
    assert item.reference_checks is None
    assert item.status is tasks.ContentStatus.DRAFT


def test_seven_forty_five_fetches_before_locking_and_skips_a_changed_row(monkeypatch):
    url = KDCA_VIEW.format(9502)
    hospital = _publication_hospital()
    item = _publication_item(hospital, body="진료 기준을 안내합니다.", title=HEMORRHOID_TITLE)
    item.references_list = [{"title": "치핵", "url": url}]
    item.reference_checks = None
    db = _GateDB(item)
    incidents = _gate_setup(monkeypatch, item)
    locks_at_fetch: list[int] = []

    def concurrent_retitle():
        locks_at_fetch.append(db.locks)
        item.title = "치질 수술 뒤 식사 관리"

    fetcher = _MutatingFetcher(concurrent_retitle, {url: (404, url, "")})

    with override_reference_fetcher(fetcher):
        tasks._page_morning_stored_publication_gates(
            db, now_kst=arrow.get(2026, 6, 10, 7, 45, tzinfo="Asia/Seoul")
        )

    assert locks_at_fetch == [0]
    assert item.references_list == [{"title": "치핵", "url": url}]
    assert item.reference_checks is None
    assert incidents == []  # 08:00 발행기가 바뀐 행으로 다시 본다


async def test_apply_refuses_a_refresh_whose_row_changed():
    from app.services.reference_publication import apply_publication_reference_refresh

    url = KDCA_VIEW.format(9503)
    item = SimpleNamespace(
        title=HEMORRHOID_TITLE,
        body="치질 수술 뒤 회복 기간을 안내합니다.",
        content_brief=None,
        faq_question=None,
        content_type="FAQ",
        content_revision=2,
        references_list=[{"title": "치핵", "url": url}],
        reference_checks=None,
    )
    refresh = await refresh_publication_references(
        item, ReferenceVerifier(PageFetcher({url: (404, url, "")}), domain_spacing=0)
    )
    assert refresh.references_changed

    for change in (
        {"content_revision": 3},
        {"references_list": [{"title": "다른 주소", "url": CURATED_HEMORRHOID_KDCA}]},
        {"title": "고혈압 관리"},
        {"content_brief": {"target_keyword": "고혈압"}},
    ):
        changed = SimpleNamespace(**{**vars(item), **change})
        before = dict(vars(changed))
        assert not apply_publication_reference_refresh(changed, refresh)
        assert vars(changed) == before

    assert apply_publication_reference_refresh(item, refresh)
    assert url not in [ref["url"] for ref in item.references_list]


# ── Pass 2: 글 주제가 바뀌면 같은 URL도 다시 판정한다 ──────────────────────────


async def test_title_only_patch_makes_the_stored_passes_stale_without_a_get(monkeypatch):
    hospital = _hospital()
    url = KDCA_VIEW.format(9601)
    item = _content_item(
        hospital_id=hospital.id,
        title=HEMORRHOID_TITLE,
        references_list=[{"title": "치핵", "url": url}],
    )
    item.content_revision = 1
    item.reference_checks = [_pass(url)]
    _stamp(item)
    assert publication_references_current(item)
    _wire(monkeypatch, item, hospital)
    fetcher = PageFetcher()

    with override_reference_fetcher(fetcher):
        await content_api.update_content(
            hospital.id,
            item.id,
            content_api.ContentPatch(title="대장내시경 전 장정결 방법"),
            db=_PatchDB(hospital),
        )

    assert fetcher.calls == []
    assert not publication_references_current(item)  # 다음 게이트가 새 주제로 다시 판정한다


async def test_brief_change_makes_the_stored_passes_stale():
    url = KDCA_VIEW.format(9602)
    item = SimpleNamespace(
        title=HEMORRHOID_TITLE,
        body="",
        content_brief={"target_keyword": "치질 수술"},
        faq_question=None,
        references_list=[{"title": "치핵", "url": url}],
        reference_checks=[_pass(url)],
    )
    _stamp(item)
    assert publication_references_current(item)

    item.content_brief = {"target_keyword": "고혈압"}

    assert not publication_references_current(item)


async def test_patch_verifies_before_locking_and_refuses_when_the_row_changed(monkeypatch):
    hospital = _hospital()
    item = _content_item(hospital_id=hospital.id, title=HEMORRHOID_TITLE, references_list=[])
    item.content_revision = 1
    _wire(monkeypatch, item, hospital)
    locks: list[str] = []

    async def record_lock(_db, _hospital_id):
        locks.append("hospital")

    monkeypatch.setattr(content_api, "acquire_hospital_advisory_lock", record_lock)
    outside = KDCA_VIEW.format(9603)
    locks_at_fetch: list[int] = []

    def concurrent_edit():
        locks_at_fetch.append(len(locks))
        item.content_revision = 2

    fetcher = _MutatingFetcher(concurrent_edit)
    fetcher.add_document(outside, "치핵 | 국가건강정보포털 | 질병관리청", topic="치핵")

    with override_reference_fetcher(fetcher), pytest.raises(HTTPException) as raised:
        await content_api.update_content(
            hospital.id,
            item.id,
            content_api.ContentPatch(references=[{"title": "치핵", "url": outside}]),
            db=_PatchDB(hospital),
        )

    assert locks_at_fetch == [0]
    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "CONTENT_CHANGED_DURING_REFERENCE_CHECK"
    assert item.references_list == []


async def test_patch_reports_a_site_outage_as_temporary(monkeypatch):
    hospital = _hospital()
    item = _content_item(hospital_id=hospital.id, title=HEMORRHOID_TITLE, references_list=[])
    _wire(monkeypatch, item, hospital)
    outside = KDCA_VIEW.format(9604)

    with override_reference_fetcher(PageFetcher({outside: httpx.ConnectError("down")})):
        with pytest.raises(HTTPException) as raised:
            await content_api.update_content(
                hospital.id,
                item.id,
                content_api.ContentPatch(references=[{"title": "치핵", "url": outside}]),
                db=_PatchDB(hospital),
            )

    assert raised.value.status_code == 400
    assert raised.value.detail["failed_references"][0]["reason"] == "site_unreachable"
    assert "일시 장애" in raised.value.detail["message"]


async def test_manual_publish_fetches_before_taking_the_hospital_lock(monkeypatch):
    hospital = _hospital()
    item = _content_item(
        hospital_id=hospital.id,
        title="경산 내과 진료비 안내",
        references_list=[{"title": "진료비", "url": GUESSED}],
        faq_question="경산 내과 진료비는 무엇에 따라 달라지나요?",
        faq_answer_summary="검사 범위와 보험 적용에 따라 달라집니다.",
    )
    item.reference_checks = None
    _wire(monkeypatch, item, hospital)
    locks: list[str] = []

    async def record_lock(_db, _hospital_id):
        locks.append("hospital")

    monkeypatch.setattr(content_api, "acquire_hospital_advisory_lock", record_lock)
    locks_at_fetch: list[int] = []
    fetcher = _MutatingFetcher(lambda: locks_at_fetch.append(len(locks)), {GUESSED: (404, GUESSED, "")})
    token = set_request_actor("ae@example.com")
    try:
        with override_reference_fetcher(fetcher), pytest.raises(HTTPException):
            await content_api.publish_content(
                hospital.id, item.id, content_api.PublishBody(), db=_NoExecuteDB()
            )
    finally:
        reset_request_actor(token)

    assert locks_at_fetch == [0]
    assert locks == ["hospital"]


async def test_manual_publish_outage_is_a_retry_later_not_a_missing_reference(monkeypatch):
    hospital = _hospital()
    outside = KDCA_VIEW.format(9605)
    item = _content_item(
        hospital_id=hospital.id,
        title=HEMORRHOID_TITLE,
        references_list=[{"title": "치핵", "url": outside}],
    )
    item.reference_checks = None
    _wire(monkeypatch, item, hospital)
    token = set_request_actor("ae@example.com")
    try:
        with override_reference_fetcher(PageFetcher({outside: httpx.ConnectTimeout("slow")})):
            with pytest.raises(HTTPException) as raised:
                await content_api.publish_content(
                    hospital.id, item.id, content_api.PublishBody(), db=_NoExecuteDB()
                )
    finally:
        reset_request_actor(token)

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "REFERENCES_NOT_VERIFIED"
    assert outside in raised.value.detail["message"]
    assert item.references_list == [{"title": "치핵", "url": outside}]


# ── Pass 2: restore는 검증만 하고, 통과하지 못하면 거절한다 ────────────────────


class _CommitDB:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


async def test_restore_gate_refuses_without_touching_the_published_references():
    guessed_url = GUESSED
    references = [{"title": "치질 수술 안내", "url": guessed_url}]
    item = SimpleNamespace(
        title=HEMORRHOID_TITLE,
        body="",
        content_brief=None,
        faq_question=None,
        content_type="FAQ",
        content_revision=5,
        references_list=[dict(ref) for ref in references],
        reference_checks=None,
    )
    fetcher = _hemorrhoid_fetcher()  # 추측 주소 → 404, 치유 후보(수기 목록)는 열린다
    verification = await verify_publication_references(
        item, ReferenceVerifier(fetcher, domain_spacing=0)
    )
    db = _CommitDB()

    with pytest.raises(HTTPException) as raised:
        await content_api._require_restorable_references(db, item, verification)

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "REFERENCES_NOT_VERIFIED"
    assert guessed_url in raised.value.detail["message"]
    assert "PATCH" in raised.value.detail["message"]
    assert item.references_list == references  # 빼지도, 채우지도, 바꾸지도 않는다
    assert item.content_revision == 5
    assert item.reference_checks[0]["reason"] == "dead_link"  # 검증 기록만 남는다
    assert db.commits == 1
    assert fetcher.calls == [guessed_url]  # 수기 목록 치유를 시도하지 않는다


async def test_restore_gate_passes_a_currently_verified_post():
    url = KDCA_VIEW.format(9701)
    item = SimpleNamespace(
        title=HEMORRHOID_TITLE,
        body="",
        content_brief=None,
        faq_question=None,
        content_type="FAQ",
        content_revision=5,
        references_list=[{"title": "치핵", "url": url}],
        reference_checks=None,
    )
    fetcher = PageFetcher()
    fetcher.add_document(url, "치핵 | 국가건강정보포털 | 질병관리청", topic="치핵")
    verification = await verify_publication_references(
        item, ReferenceVerifier(fetcher, domain_spacing=0)
    )

    await content_api._require_restorable_references(_CommitDB(), item, verification)

    assert publication_references_current(item)
    assert item.references_list == [{"title": "치핵", "url": url}]


async def test_restore_gate_refuses_malformed_entries_and_site_outages():
    url = KDCA_VIEW.format(9702)
    item = SimpleNamespace(
        title=HEMORRHOID_TITLE,
        body="",
        content_brief=None,
        faq_question=None,
        content_type="FAQ",
        content_revision=5,
        references_list=[{"title": "치핵", "url": url}, {"title": "주소 없음"}],
        reference_checks=None,
    )
    verification = await verify_publication_references(
        item, ReferenceVerifier(PageFetcher({url: httpx.ConnectError("down")}), domain_spacing=0)
    )

    with pytest.raises(HTTPException) as raised:
        await content_api._require_restorable_references(_CommitDB(), item, verification)

    labels = [entry["reason_label"] for entry in raised.value.detail["failed_references"]]
    assert "기관 사이트에 접속하지 못함(일시 장애)" in labels
    assert "참고 자료 항목 형식 오류" in labels
    assert item.references_list == [{"title": "치핵", "url": url}, {"title": "주소 없음"}]


# ── Pass 2: 생성 단계 장애는 수기 목록 치유가 먼저다 ─────────────────────────────


async def test_generation_outage_heals_from_the_curated_list_instead_of_rejecting():
    from app.services import content_engine

    guessed = KDCA_VIEW.format(9801)
    result = {
        "title": HEMORRHOID_TITLE,
        "body": "## 치질 수술 뒤 회복\n본문",
        "faq_question": None,
        "references": [{"title": "치질 수술 안내", "url": guessed}],
    }
    fetcher = PageFetcher({guessed: httpx.ConnectError("kdca down")})

    with override_reference_fetcher(fetcher):
        await content_engine._verify_generated_references(result, None, required=True)

    urls = [ref["url"] for ref in result["references"]]
    assert urls and guessed not in urls
    assert set(urls) <= {CURATED_HEMORRHOID_KDCA, CURATED_HEMORRHOID_AMC}


# ── 리뷰 B1: GET 사이에 공개된 글의 참고자료를 다시 쓰지 않는다 ────────────────────


def test_seven_forty_five_never_rewrites_a_row_published_during_the_get(monkeypatch):
    """07:45 GET 도중 운영자가 수동 발행했다 — 발행은 판·참고자료·주제를 바꾸지 않는다."""

    url = KDCA_VIEW.format(9901)
    hospital = _publication_hospital()
    item = _publication_item(hospital, body="진료 기준을 안내합니다.", title=HEMORRHOID_TITLE)
    original = [{"title": "치핵", "url": url}]
    item.references_list = [dict(ref) for ref in original]
    item.reference_checks = None
    item.content_revision = 3
    db = _GateDB(item)
    incidents = _gate_setup(monkeypatch, item)

    def concurrent_publish():
        item.status = tasks.ContentStatus.PUBLISHED

    # 404 → 제거 후 수기 목록 치유가 될 결과다. 적용되면 공개 글의 참고자료가 바뀐다.
    fetcher = _MutatingFetcher(concurrent_publish, {url: (404, url, "")})
    with override_reference_fetcher(fetcher):
        tasks._page_morning_stored_publication_gates(
            db, now_kst=arrow.get(2026, 6, 10, 7, 45, tzinfo="Asia/Seoul")
        )

    assert url in fetcher.calls  # 잠금 밖에서 다시 검증했다(제거·치유 결과가 만들어졌다)
    assert item.status is tasks.ContentStatus.PUBLISHED
    assert item.references_list == original
    assert item.content_revision == 3
    assert item.reference_checks is None
    assert incidents == []


def test_seven_forty_five_writes_nothing_to_a_row_that_is_not_publishable_at_lock(monkeypatch):
    """잠근 행이 발행 전 상태가 아니면 검증 기록도, 발행 판정·인시던트도 남기지 않는다."""

    url = KDCA_VIEW.format(9902)
    hospital = _publication_hospital()
    item = _publication_item(hospital, body="진료 기준을 안내합니다.", title=HEMORRHOID_TITLE)
    item.status = tasks.ContentStatus.PUBLISHED
    item.references_list = [{"title": "치핵", "url": url}]
    item.reference_checks = None
    item.content_revision = 3
    db = _GateDB(item)
    incidents = _gate_setup(monkeypatch, item)
    fetcher = PageFetcher()
    fetcher.add_document(url, "치핵 | 국가건강정보포털 | 질병관리청", topic="치핵")  # 통과할 문서

    with override_reference_fetcher(fetcher):
        tasks._page_morning_stored_publication_gates(
            db, now_kst=arrow.get(2026, 6, 10, 7, 45, tzinfo="Asia/Seoul")
        )

    assert item.reference_checks is None
    assert item.references_list == [{"title": "치핵", "url": url}]
    assert item.content_revision == 3
    assert incidents == []


async def test_apply_refuses_a_refresh_once_the_row_left_the_publishable_states():
    from app.services.reference_publication import apply_publication_reference_refresh

    dead = KDCA_VIEW.format(9903)
    live = KDCA_VIEW.format(9904)
    fetcher = PageFetcher({dead: (404, dead, "")})
    fetcher.add_document(live, "치핵 | 국가건강정보포털 | 질병관리청", topic="치핵")

    def row(url, status):
        return SimpleNamespace(
            title=HEMORRHOID_TITLE,
            body="치질 수술 뒤 회복 기간을 안내합니다.",
            content_brief=None,
            faq_question=None,
            content_type="FAQ",
            content_revision=2,
            status=status,
            references_list=[{"title": "치핵", "url": url}],
            reference_checks=None,
        )

    verifier = ReferenceVerifier(fetcher, domain_spacing=0)
    for url, changes_references in ((dead, True), (live, False)):
        item = row(url, tasks.ContentStatus.DRAFT)
        refresh = await refresh_publication_references(item, verifier)
        assert refresh.references_changed is changes_references
        # GET 사이에 공개됐다(발행은 판·참고자료·주제를 바꾸지 않는다) — 검증 기록조차 쓰지 않는다.
        item.status = tasks.ContentStatus.PUBLISHED
        before = dict(vars(item))
        assert not apply_publication_reference_refresh(item, refresh)
        assert vars(item) == before

    # 처음부터 공개된 행으로 만든 결과(스냅샷이 같아도)는 참고자료를 바꾸지 않는다.
    published = row(dead, tasks.ContentStatus.PUBLISHED)
    refresh = await refresh_publication_references(published, verifier)
    before = dict(vars(published))
    assert not apply_publication_reference_refresh(published, refresh)
    assert vars(published) == before


# ── 리뷰 B2: 아직 생성되지 않은 슬롯의 참고자료·판을 건드리지 않는다 ────────────────


@pytest.mark.parametrize(
    "references",
    [[], [{"title": "치핵", "url": KDCA_VIEW.format(9905)}]],
    ids=["empty", "stale"],
)
def test_publisher_leaves_an_ungenerated_slot_untouched(monkeypatch, references):
    """생성은 brief를 저장한 뒤 그 판을 잡고 공급자를 부른다 — 판이 오르면 결과가 버려진다."""

    item, _db, _effects = _publish_setup(monkeypatch, references=list(references), checks=None)
    item.title = None
    item.body = None
    item.content_brief = {"target_keyword": "치핵", "query_target": {"name": "치핵 수술 병원"}}
    fetcher = _hemorrhoid_fetcher()
    refreshed: list[object] = []
    real_refresh = tasks.refresh_publication_references

    async def spy_refresh(row, verifier, **kwargs):
        refreshed.append(row)
        return await real_refresh(row, verifier, **kwargs)

    monkeypatch.setattr(tasks, "refresh_publication_references", spy_refresh)
    verifier = ReferenceVerifier(fetcher, domain_spacing=0)

    assert tasks._prefetch_publication_references(item.id, verifier) is None
    payload = tasks._auto_publish_one(item.id, reference_verifier=verifier)

    assert refreshed == []  # 재검증 자체를 하지 않는다
    assert fetcher.calls == []
    assert item.content_revision == 3
    assert item.references_list == references
    assert item.reference_checks is None
    assert payload is not None and payload["code"] == "CONTENT_NOT_GENERATED"
    assert item.status is tasks.ContentStatus.DRAFT


@pytest.mark.parametrize(
    "references",
    [[], [{"title": "치핵", "url": KDCA_VIEW.format(9905)}]],
    ids=["empty", "stale"],
)
async def test_manual_publish_leaves_an_ungenerated_slot_untouched(monkeypatch, references):
    hospital = _hospital()
    item = _content_item(
        hospital_id=hospital.id,
        title="",
        body="",
        references_list=[dict(ref) for ref in references],
        content_brief={"target_keyword": "치핵", "query_target": {"name": "치핵 수술 병원"}},
    )
    item.content_revision = 3
    item.reference_checks = None
    _wire(monkeypatch, item, hospital)
    fetcher = _hemorrhoid_fetcher()
    refreshed: list[object] = []
    real_refresh = content_api.refresh_publication_references

    async def spy_refresh(row, verifier, **kwargs):
        refreshed.append(row)
        return await real_refresh(row, verifier, **kwargs)

    monkeypatch.setattr(content_api, "refresh_publication_references", spy_refresh)
    token = set_request_actor("ae@example.com")
    try:
        with override_reference_fetcher(fetcher), pytest.raises(HTTPException) as raised:
            await content_api.publish_content(
                hospital.id, item.id, content_api.PublishBody(), db=_NoExecuteDB()
            )
    finally:
        reset_request_actor(token)

    # 참고자료 게이트(409)가 아니라 종전 그대로 "아직 생성되지 않음"(400)이다.
    assert raised.value.status_code == 400
    assert raised.value.detail == "Content not generated yet"
    assert refreshed == []
    assert fetcher.calls == []
    assert item.references_list == references
    assert item.content_revision == 3
    assert item.reference_checks is None


async def test_refresh_never_heals_a_row_without_generated_text():
    item = SimpleNamespace(
        title=None,
        body=None,
        content_brief={"target_keyword": "치핵"},
        faq_question=None,
        content_type="FAQ",
        content_revision=3,
        references_list=[],
        reference_checks=None,
    )
    fetcher = _hemorrhoid_fetcher()

    refresh = await refresh_publication_references(item, ReferenceVerifier(fetcher, domain_spacing=0))

    assert fetcher.calls == []
    assert not refresh.references_changed
    assert not refresh.healed
    assert refresh.references == []


# ── 공개된 글의 참고자료는 어떤 자동 경로에서도 바뀌지 않는다 ─────────────────────
#
# 생성 write-back은 공개 행에 쓰지 않는다(`GENERATION_WRITE_BACK_STATUSES`, PG 고정:
# tests/integration/test_generation_write_back_guard.py). 나머지 자동 경로를 여기서 고정한다.


def _published_references():
    # 404 문서 — 재검증 결과가 적용되면 빼고 수기 목록으로 채우는(판이 오르는) 주소다.
    return [{"title": "치질 수술 안내", "url": GUESSED}]


def _assert_unchanged(item, references, revision):
    assert item.references_list == references
    assert item.content_revision == revision


def test_published_references_never_change_on_the_worker_paths(monkeypatch):
    # 07:45 게이트
    hospital = _publication_hospital()
    item = _publication_item(hospital, body="진료 기준을 안내합니다.", title=HEMORRHOID_TITLE)
    item.status = tasks.ContentStatus.PUBLISHED
    item.references_list = _published_references()
    item.reference_checks = None
    item.content_revision = 7
    _gate_setup(monkeypatch, item)
    with override_reference_fetcher(_hemorrhoid_fetcher()):
        tasks._page_morning_stored_publication_gates(
            _GateDB(item), now_kst=arrow.get(2026, 6, 10, 7, 45, tzinfo="Asia/Seoul")
        )
    _assert_unchanged(item, _published_references(), 7)

    # 08:00 발행기(예정일 당일)와 catch-up(지난 예정일)
    for scheduled in (arrow.get(2026, 6, 10).date(), arrow.get(2026, 6, 7).date()):
        item, _db, _effects = _publish_setup(
            monkeypatch, references=_published_references(), checks=None
        )
        item.status = tasks.ContentStatus.PUBLISHED
        item.scheduled_date = scheduled
        fetcher = _hemorrhoid_fetcher()
        assert tasks._auto_publish_one(
            item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0)
        ) is None
        assert fetcher.calls == []
        _assert_unchanged(item, _published_references(), 3)
        assert item.reference_checks is None


async def test_published_references_never_change_on_apply_restore_or_manual_publish(monkeypatch):
    from app.services.reference_publication import apply_publication_reference_refresh

    def published(status):
        return SimpleNamespace(
            title=HEMORRHOID_TITLE,
            body="치질 수술 뒤 회복 기간을 안내합니다.",
            content_brief=None,
            faq_question=None,
            content_type="FAQ",
            content_revision=7,
            status=status,
            references_list=_published_references(),
            reference_checks=None,
        )

    # 재검증 결과 적용
    item = published(tasks.ContentStatus.PUBLISHED)
    refresh = await refresh_publication_references(
        item, ReferenceVerifier(_hemorrhoid_fetcher(), domain_spacing=0)
    )
    assert refresh.references_changed
    assert not apply_publication_reference_refresh(item, refresh)
    _assert_unchanged(item, _published_references(), 7)

    # restore(비공개 보존 글) — 검증 기록만 남기고 거절한다
    for status in (tasks.ContentStatus.WITHHELD, tasks.ContentStatus.PUBLISHED):
        item = published(status)
        verification = await verify_publication_references(
            item, ReferenceVerifier(_hemorrhoid_fetcher(), domain_spacing=0)
        )
        with pytest.raises(HTTPException):
            await content_api._require_restorable_references(_CommitDB(), item, verification)
        _assert_unchanged(item, _published_references(), 7)

    # 수동 발행 — 이미 공개된 글은 400이고 잠금 전 재검증 결과를 쓰지 않는다
    hospital = _hospital()
    item = _content_item(
        hospital_id=hospital.id,
        title=HEMORRHOID_TITLE,
        status=content_api.ContentStatus.PUBLISHED,
        references_list=_published_references(),
    )
    item.content_revision = 7
    item.reference_checks = None
    _wire(monkeypatch, item, hospital)
    token = set_request_actor("ae@example.com")
    try:
        with override_reference_fetcher(_hemorrhoid_fetcher()), pytest.raises(HTTPException) as raised:
            await content_api.publish_content(
                hospital.id, item.id, content_api.PublishBody(), db=_NoExecuteDB()
            )
    finally:
        reset_request_actor(token)
    assert raised.value.status_code == 400
    _assert_unchanged(item, _published_references(), 7)
    assert item.reference_checks is None


# ── 수동 발행: 잠금 전에 통과였어도 잠근 행으로 다시 본다 ──────────────────────────


async def test_manual_publish_rechecks_the_gate_on_the_locked_row(monkeypatch):
    """잠금 전에는 신선한 통과(재검증 없음)였는데 잠금을 기다리는 사이 참고자료가 바뀌었다."""

    hospital = _hospital()
    url = KDCA_VIEW.format(9906)
    item = _content_item(
        hospital_id=hospital.id,
        title=HEMORRHOID_TITLE,
        references_list=[{"title": "치핵", "url": url}],
    )
    item.content_revision = 1
    item.reference_checks = [_pass(url)]
    _stamp(item)
    assert publication_references_current(item)
    _wire(monkeypatch, item, hospital)
    monkeypatch.setattr(
        content_api,
        "assess_content_publication",
        lambda _item, philosophy: SimpleNamespace(
            publishable=True,
            code=None,
            message=None,
            violations=(),
            essence_status=content_api.ESSENCE_STATUS_ALIGNED,
            essence_summary={"ok": True},
            philosophy_id=None,
        ),
    )

    class ConcurrentEditDB(_NoExecuteDB):
        async def refresh(self, row):
            # 잠금을 기다리는 동안 다른 요청이 검증 기록 없는 주소로 바꿨다.
            row.references_list = [{"title": "치핵", "url": KDCA_VIEW.format(9907)}]
            row.content_revision = 2

    fetcher = PageFetcher()
    token = set_request_actor("ae@example.com")
    try:
        with override_reference_fetcher(fetcher), pytest.raises(HTTPException) as raised:
            await content_api.publish_content(
                hospital.id, item.id, content_api.PublishBody(), db=ConcurrentEditDB()
            )
    finally:
        reset_request_actor(token)

    assert fetcher.calls == []  # 잠금 전에는 통과였으니 GET하지 않았다
    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "REFERENCES_NOT_VERIFIED"
    assert item.status != content_api.ContentStatus.PUBLISHED


# ── 공개 글 PATCH: 필수 글의 참고자료를 비울 수 없다 ───────────────────────────────


@pytest.mark.parametrize(
    ("content_type", "query_target_id"),
    [("FAQ", None), ("NOTICE", uuid.uuid4())],
    ids=["medical_type", "notice_with_query_link"],
)
async def test_patch_cannot_empty_the_references_of_a_published_post(
    monkeypatch, content_type, query_target_id
):
    hospital = _hospital()
    references = [{"title": "치핵", "url": CURATED_HEMORRHOID_KDCA}]
    item = _content_item(
        hospital_id=hospital.id,
        title=HEMORRHOID_TITLE,
        content_type=content_type,
        query_target_id=query_target_id,
        status=content_api.ContentStatus.PUBLISHED,
        references_list=[dict(ref) for ref in references],
    )
    item.content_revision = 4
    item.reference_checks = None
    _wire(monkeypatch, item, hospital)
    monkeypatch.setattr(content_api, "ensure_site_revalidate_configured", lambda: None)

    with override_reference_fetcher(PageFetcher()), pytest.raises(HTTPException) as raised:
        await content_api.update_content(
            hospital.id,
            item.id,
            content_api.ContentPatch(references=[]),
            db=_PatchDB(hospital),
        )

    assert raised.value.status_code == 400
    assert "비울 수 없습니다" in raised.value.detail["message"]


# ── 08:00 발행기: GET 사이에 바뀐 행은 장애 알림 대상도 아니다 ──────────────────────


def test_publisher_does_not_report_an_outage_for_a_row_changed_during_the_get(monkeypatch):
    url = KDCA_VIEW.format(9908)
    item, _db, _effects = _publish_setup(
        monkeypatch, references=[{"title": "치핵", "url": url}], checks=None
    )
    concurrent = [{"title": "치핵(사람이 고친 주소)", "url": CURATED_HEMORRHOID_KDCA}]

    def concurrent_patch():
        item.references_list = concurrent
        item.content_revision = 4

    fetcher = _MutatingFetcher(concurrent_patch, {url: httpx.ConnectError("down")})

    payload = tasks._auto_publish_one(
        item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0)
    )

    # 옛 주소의 접속 장애를 08:00 요약에 '기관 사이트 접속 불가'로 올리지 않는다 — 그 주소는
    # 이제 이 글에 없다. 다음 시간대 발행기가 바뀐 행으로 다시 본다.
    assert payload is None
    assert item.references_list == concurrent
    assert item.reference_checks is None


# ── 깨진 포트 주소 하나가 07:45 루프 전체를 멈추지 않는다 ──────────────────────────


class _ManyGateDB(_GateDB):
    def __init__(self, items):
        super().__init__(items[0])
        self.items = {item.id: item for item in items}
        self.ordered = list(items)

    def execute(self, stmt):
        if getattr(stmt, "_for_update_arg", None) is not None:
            self.locks += 1
            row = self.items[stmt.whereclause.right.value]
            return SimpleNamespace(scalar_one_or_none=lambda: row)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: list(self.ordered)))


def test_seven_forty_five_rejects_a_malformed_port_url_and_keeps_going(monkeypatch):
    malformed = (
        "https://health.kdca.go.kr:bad/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/"
        "gnrlzHealthInfoView.do?cntnts_sn=5818"
    )
    hospital = _publication_hospital()
    broken = _publication_item(hospital, body="진료 기준을 안내합니다.", title=HEMORRHOID_TITLE)
    broken.references_list = [{"title": "치핵", "url": malformed}]
    broken.reference_checks = None
    healthy_url = KDCA_VIEW.format(9909)
    healthy = _publication_item(hospital, body="진료 기준을 안내합니다.", title=HEMORRHOID_TITLE)
    healthy.references_list = [{"title": "치핵", "url": healthy_url}]
    healthy.reference_checks = None
    _gate_setup(monkeypatch, broken)
    fetcher = _hemorrhoid_fetcher()
    fetcher.add_document(healthy_url, "치핵 | 국가건강정보포털 | 질병관리청", topic="치핵")

    with override_reference_fetcher(fetcher):
        tasks._page_morning_stored_publication_gates(
            _ManyGateDB([broken, healthy]),
            now_kst=arrow.get(2026, 6, 10, 7, 45, tzinfo="Asia/Seoul"),
        )

    assert malformed not in fetcher.calls
    assert malformed not in [ref["url"] for ref in broken.references_list]
    assert [c["reason"] for c in broken.reference_checks if c["url"] == malformed] == [
        "not_citable"
    ]
    # 다음 글도 이어서 검증했다.
    assert healthy_url in fetcher.calls
    assert publication_references_current(healthy)


# ── 생성은 검증 기록을 저장한다 — 07:45·08:00이 같은 URL을 다시 열지 않는다 ─────────


def _generated_with_checks(item, url):
    """생성 엔진이 돌려주는 모양 — 참고자료와 그 실제 문서 검증 기록(글 주제에 묶음)."""

    result = {
        "title": HEMORRHOID_TITLE,
        "body": "## 치질 수술 뒤 회복\n수술 뒤 통증과 배변 관리를 의료진과 확인합니다.",
        "meta_description": "치질 수술 뒤 회복 기간을 정리했습니다.",
        "references": [{"title": "치핵", "url": url}],
        "faq_question": "치질 수술 뒤 회복은 얼마나 걸리나요?",
        "faq_answer_summary": "수술 방법과 상태에 따라 달라집니다.",
    }
    record = _pass(url)
    record["topic_fingerprint"] = item_topic_fingerprint(
        SimpleNamespace(
            title=result["title"],
            body=result["body"],
            faq_question=result["faq_question"],
            content_brief=getattr(item, "content_brief", None),
        )
    )
    result["reference_checks"] = [record]
    return result


def test_first_generation_stores_the_reference_checks_for_the_publication_gates(monkeypatch):
    from app.services.reference_publication import publication_references_settled
    from tests.test_topic_swap_fallback import _approved_philosophy as _swap_philosophy
    from tests.test_topic_swap_fallback import (
        _generate_once,
        _kst,
        _patch_generation,
        _swapped_slot,
    )

    philosophy = _swap_philosophy()
    item = _swapped_slot(philosophy)  # 본문 없는 슬롯 — 배치의 첫 생성 경로
    _patch_generation(monkeypatch, philosophy, item, fail=False)
    url = KDCA_VIEW.format(9910)
    stored: list[dict] = []

    async def writer(*, hospital, item, existing_titles, philosophy, approved_brief):
        result = _generated_with_checks(item, url)
        stored.extend(result["reference_checks"])
        return result, SimpleNamespace(status=None, summary={})

    monkeypatch.setattr(tasks, "_generate_with_auto_review", writer)

    state, code, _message = _generate_once(monkeypatch, item, _kst(2026, 9, 16, 7, 0, 4))

    assert (state, code) == (tasks.GenerationItemState.SUCCEEDED, None)
    assert item.reference_checks == stored and stored
    assert publication_references_settled(item)  # 07:45·08:00은 GET 없이 지나간다


def test_regeneration_stores_the_reference_checks_for_the_publication_gates(monkeypatch):
    from app.services.reference_publication import publication_references_settled
    from tests.test_tasks_nightly import _sweep_regeneration_harness, _uncertain_only_item

    philosophy = SimpleNamespace(id=uuid.uuid4())
    item = _uncertain_only_item(philosophy)
    item.scheduled_date = arrow.get(2026, 9, 13).date()
    item.generation_claim_token = None
    item.content_revision = 1
    hospital = SimpleNamespace(id=item.hospital_id, name="검증기록의원", slug="checks")
    db, _writer_calls, _gate_calls = _sweep_regeneration_harness(monkeypatch, philosophy, item)
    url = KDCA_VIEW.format(9911)
    stored: list[dict] = []

    async def writer(**kwargs):
        result = _generated_with_checks(kwargs["item"], url)
        stored.extend(result["reference_checks"])
        return result, SimpleNamespace(status="ALIGNED", summary={"blocking": False})

    monkeypatch.setattr(tasks, "_generate_with_auto_review", writer)
    # 저장 본문 수리 경로로 들어가게 한다(재검수 없이 곧바로 작가 세션).
    item.essence_check_summary["ai_review"]["findings"][0]["severity"] = "HARD"
    item.essence_check_summary["ai_review"]["findings"][0]["kind"] = "STYLE"

    state, code, _message = tasks._generate_single_content_item(db, item, hospital)

    assert stored, "작가 세션까지 가야 이 테스트가 의미가 있다"
    assert (state, code) == (tasks.GenerationItemState.SUCCEEDED, None)
    assert item.reference_checks == stored
    assert publication_references_settled(item)
