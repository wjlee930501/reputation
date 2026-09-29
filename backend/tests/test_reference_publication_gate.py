"""발행 직전 참고자료 게이트 — 07:45·08:00 워커, 관리자 수정·수동 발행, 운영자 문구.

계약: 모든 참고자료에 같은 URL의 신선한 통과 기록이 있어야 공개한다. 없거나 오래됐으면
워커가 다시 검증하고, 떨어진 항목은 빼고, 전부 빠지면 이 글의 주제로 채점한 수기 목록으로
채우며, 그래도 없으면 발행을 보류하고 실제 원인(참고자료 확보 실패)을 말하는 인시던트를 연다.
네트워크는 쓰지 않는다 — 가짜 fetcher만 쓴다.
"""

from __future__ import annotations

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

    def publish(content_id, *, reference_verifier):
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
        body="",
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
