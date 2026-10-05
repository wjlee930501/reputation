"""발행 차단 자동 해결 — 실제 Postgres·실제 23:00 로더·실제 발행기 게이트로 끝까지 본다.

마포성모탑 10/5 FAQ(수련기관 오기)를 그대로 옮긴다. 예정일 전날 23:00 야간 배치의 로더가 그 글을
집고(새 스케줄 없음), 워커가 지적 문장만 승인 프로필대로 고친 뒤 독립 재검수를 받는다. 예정일
아침 발행기(`morning_content_auto_publish`)가 **실제** `assess_content_publication`으로 판정해
발행한다 — 판정을 바꿔치기하지 않는다. 대조군은 재검수가 FAIL인 경우와 재검수 없이 교정본만
남은 경우이며 둘 다 같은 발행기에서 공개되지 않는다.

공급자(교정 제안·독립 검수)만 가짜다. 이미지는 인증 필드를 실제 규칙대로 채워 두고, 참고자료는
신선한 통과 기록이 있어 GET하지 않는다.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital
from app.services import content_minimal_correction as mc
from app.services.content_ai_review import (
    ContentAiFinding,
    ContentAiFindingKind,
    ContentAiFindingSeverity,
    ContentAiReview,
    ContentAiReviewStatus,
    candidate_review_coverage,
    candidate_sha256,
)
from app.services.image_engine import IMAGE_POLICY_VERSION, image_subject_hash
from app.services.reference_verification import item_topic_fingerprint, reference_check_record
from app.workers import nightly_generation_batch, tasks
from tests.integration import test_operator_decides_digest_postgres as digest
from tests.integration.test_operator_decides_digest_postgres import _hospital
from tests.integration.test_reference_claim_last_run_postgres import (
    _kst,
    _publisher_run,
    _set_clock,
)

pg_session = digest.pg_session
gate_db = digest.gate_db
incidents = digest.incidents

SLOT = date(2032, 5, 11)
FRESH_URL = "https://health.kdca.go.kr/healthinfo/example-back-pain"
WRONG = "이현진 원장은 서울성모병원에서 정형외과 전공의 수련을 마쳤습니다."
FIXED = "이현진 원장은 가톨릭대학교 성모병원에서 정형외과 전공의 수련을 마쳤습니다."
BODY = (
    "## 허리 통증, 언제 병원에 가야 할까요\n"
    "허리 통증은 원인이 다양해 진찰과 영상 검사로 원인을 확인합니다. "
    f"{WRONG} 통증이 오래가면 생활 습관과 자세를 함께 살펴봅니다.\n\n"
    "## 내원 전 확인할 점\n"
    "증상이 시작된 시점과 통증 부위를 기록해 오시면 진료에 도움이 됩니다."
)


def _approved_philosophy(db, hospital):
    return db.execute(
        select(HospitalContentPhilosophy).where(
            HospitalContentPhilosophy.hospital_id == hospital.id,
            HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
        )
    ).scalar_one()


def _blocked_post(db, *, scheduled_date=SLOT) -> tuple[Hospital, ContentItem]:
    hospital, schedule = _hospital(db)
    hospital.director_name = "이현진"
    hospital.director_career = "가톨릭대학교 성모병원 정형외과 전공의 수련, 정형외과 전문의"
    philosophy = _approved_philosophy(db, hospital)
    title = "허리 통증, 언제 병원에 가야 할까요?"
    item = ContentItem(
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.DISEASE,
        sequence_no=1,
        total_count=12,
        title=title,
        body=BODY,
        meta_description="허리 통증의 내원 시점을 안내합니다.",
        scheduled_date=scheduled_date,
        status=ContentStatus.DRAFT,
        references_list=[{"title": "요통", "url": FRESH_URL}],
        content_philosophy_id=philosophy.id,
    )
    db.add(item)
    db.flush()
    checked = datetime.now(timezone.utc)  # 참고자료 신선도는 실제 시계로 판정한다
    item.reference_checks = [
        {
            **reference_check_record(
                FRESH_URL,
                verdict="pass",
                reason="page_verified",
                checked_at=checked,
                curated=False,
                status=200,
                final_url=FRESH_URL,
                page_title="요통 | 국가건강정보포털 | 질병관리청",
                text_len=900,
                verified_at=checked,
            ),
            "topic_fingerprint": item_topic_fingerprint(item),
        }
    ]
    digest_hex = hashlib.sha256(b"auto-resolve-image").hexdigest()
    item.image_url = f"https://storage.example/{digest_hex}-cover.png"
    item.image_content_hash = digest_hex
    item.image_subject_hash = image_subject_hash(ContentType.DISEASE, title)
    item.image_policy_version = IMAGE_POLICY_VERSION
    item.image_policy_verified_at = checked
    candidate = tasks._stored_candidate(item)
    item.essence_check_summary = {
        "blocking": True,
        "findings": ["원장 수련기관이 승인 프로필과 다릅니다."],
        "ai_review": {
            "status": "REVISE",
            "blocking": True,
            "confidence": 0.9,
            "findings": [
                {
                    "severity": "HARD",
                    "kind": "HOSPITAL_FACT",
                    "quote": WRONG,
                    "message": "원장 수련기관이 승인 프로필과 다릅니다.",
                    "target": "CANDIDATE_TEXT",
                }
            ],
            "schema_version": "content-review-v2",
            "candidate_sha256": candidate_sha256(candidate),
            "coverage": candidate_review_coverage(candidate),
        },
    }
    db.commit()
    return hospital, item


def _fake_providers(monkeypatch, *, verdict: ContentAiReviewStatus):
    calls = {"propose": 0, "review": []}

    async def propose(*, targets, **_kwargs):
        calls["propose"] += 1
        return {target.key: ("REPLACE", FIXED) for target in targets}

    async def review(**kwargs):
        content = kwargs["content"]
        calls["review"].append(candidate_sha256(content))
        findings = ()
        if verdict == ContentAiReviewStatus.REVISE:
            findings = (
                ContentAiFinding(
                    ContentAiFindingSeverity.HARD,
                    ContentAiFindingKind.MEDICAL_SAFETY,
                    "근거가 부족합니다.",
                    quote="허리 통증은 원인이 다양해 진찰과 영상 검사로 원인을 확인합니다.",
                ),
            )
        return ContentAiReview(
            status=verdict,
            confidence=0.9,
            findings=findings,
            summary="재검수",
            model="reviewer-test",
            candidate_sha256=candidate_sha256(content),
            coverage=candidate_review_coverage(content),
            provider_attempted=True,
        )

    monkeypatch.setattr(tasks, "propose_sentence_corrections", propose)
    monkeypatch.setattr(tasks, "review_generated_content", review)
    return calls


def _quiet_publication_effects(monkeypatch):
    async def recovered(*_args, **_kwargs):
        return None

    async def revalidated(*_args, **_kwargs):
        return True

    monkeypatch.setattr(tasks, "ensure_site_revalidate_configured", lambda: None)
    monkeypatch.setattr(tasks, "recover_generation_incidents", recovered)
    monkeypatch.setattr(tasks, "trigger_content_site_revalidate_safe", revalidated)


def _run_2300_sweep_worker(db, monkeypatch, hospital, item):
    """전날 23:00 — 야간 배치 로더가 내일 글을 집고, 그 글을 워커 경로로 처리한다."""

    eve = _kst(SLOT - timedelta(days=1), 23)
    _set_clock(monkeypatch, eve)
    philosophy = _approved_philosophy(db, hospital)
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_a: philosophy)
    window_start, window_end = tasks._nightly_generation_window(eve)
    assert window_start == SLOT, "23:00 창의 첫날은 내일이다"
    claimed, _truncated, _complete = nightly_generation_batch._load_nightly_generation_batch(
        db, window_start, window_end, is_eligible=tasks._generation_retry_is_eligible(db)
    )
    assert [row.id for row in claimed] == [item.id], "23:00 로더가 내일의 차단 글을 집는다"
    row = claimed[0]
    result = tasks._generate_single_content_item(db, row, hospital)
    nightly_generation_batch.release_generation_claim(db, row.id, row.generation_claim_token)
    db.commit()
    return result


def test_hard_correction_rereview_pass_then_existing_publisher_publishes(
    gate_db, incidents, monkeypatch
):
    db = gate_db
    hospital, item = _blocked_post(db)
    calls = _fake_providers(monkeypatch, verdict=ContentAiReviewStatus.PASS)
    _quiet_publication_effects(monkeypatch)

    state, code, _message = _run_2300_sweep_worker(db, monkeypatch, hospital, item)
    assert (state, code) == (tasks.GenerationItemState.SUCCEEDED, None)
    db.expire_all()
    stored = db.get(ContentItem, item.id)
    assert FIXED in stored.body and WRONG not in stored.body
    assert stored.title == "허리 통증, 언제 병원에 가야 할까요?"
    assert calls["propose"] == 1
    assert calls["review"] == [candidate_sha256(tasks._stored_candidate(stored))]
    assert stored.essence_check_summary[mc.AUTO_CORRECTION_KEY]["corrected_sha256"] == (
        candidate_sha256(tasks._stored_candidate(stored))
    )
    assert stored.status == ContentStatus.DRAFT, "선행 실행은 발행하지 않는다 — 예정일 발행기의 몫"

    _publisher_run(db, monkeypatch, _kst(SLOT, 8))

    db.expire_all()
    published = db.get(ContentItem, item.id)
    assert published.status == ContentStatus.PUBLISHED
    assert FIXED in published.body


def test_failed_rereview_leaves_the_corrected_post_unpublished(gate_db, incidents, monkeypatch):
    db = gate_db
    hospital, item = _blocked_post(db)
    calls = _fake_providers(monkeypatch, verdict=ContentAiReviewStatus.REVISE)
    _quiet_publication_effects(monkeypatch)

    state, code, _message = _run_2300_sweep_worker(db, monkeypatch, hospital, item)
    assert (state, code) == (tasks.GenerationItemState.FAILED, "CONTENT_AI_HARD_FINDING")
    assert len(calls["review"]) <= 2

    _publisher_run(db, monkeypatch, _kst(SLOT, 8))

    db.expire_all()
    stored = db.get(ContentItem, item.id)
    assert stored.status == ContentStatus.DRAFT
    assert stored.published_at is None


def test_corrected_body_without_rereview_is_blocked_by_the_publisher(
    gate_db, incidents, monkeypatch
):
    """누가 교정본만 남기고 재검수 결과를 비워도(또는 교정 전 PASS가 남아도) 발행기는 막는다."""

    db = gate_db
    hospital, item = _blocked_post(db, scheduled_date=SLOT)
    _quiet_publication_effects(monkeypatch)
    item.body = BODY.replace(WRONG, FIXED)
    summary = dict(item.essence_check_summary)
    summary.pop("ai_review")
    summary[mc.AUTO_CORRECTION_KEY] = {
        "passes": 1,
        "rereviews": 1,
        "corrected_sha256": candidate_sha256(tasks._stored_candidate(item)),
    }
    item.essence_check_summary = summary
    db.commit()

    _publisher_run(db, monkeypatch, _kst(SLOT, 8))

    db.expire_all()
    stored = db.get(ContentItem, item.id)
    assert stored.status == ContentStatus.DRAFT
    assert stored.published_at is None


def test_exhausted_correction_is_swapped_by_the_existing_pass_to_an_offered_service(
    gate_db, incidents, monkeypatch
):
    """교정 상한을 다 쓴 슬롯을 다음 스윕의 교체 pass가 실제 SQL로 집어, 키워드에만 있는
    검사(골밀도검사)가 아니라 진료 항목에 있는 질문으로 바꾼다(강심장 사례)."""

    from app.models.sov import AIQueryTarget
    from app.workers import topic_swap_fallback
    from app.workers.generation_retry_policy import AUTO_CORRECTION_EXHAUSTED_KEY

    db = gate_db
    hospital, item = _blocked_post(db)
    hospital.name = "강심장내과의원"
    hospital.specialties = ["내과"]
    hospital.treatments = ["심장초음파", "홀터검사"]
    hospital.keywords = ["마산 내과", "골밀도검사", "홀터검사"]

    def target(name: str) -> AIQueryTarget:
        row = AIQueryTarget(
            hospital_id=hospital.id,
            name=name,
            target_intent="INFORMATION",
            region_terms=[],
            decision_criteria=[],
            platforms=[],
            competitor_names=[],
            patient_language="ko",
            priority="HIGH" if "골밀도" in name else "NORMAL",
            status="ACTIVE",
        )
        db.add(row)
        db.flush()
        return row

    failed = target("심장 두근거림 원인")
    bone = target("마산 골밀도검사 가능한 병원")
    holter = target("홀터검사 받을 수 있는 병원")
    item.query_target_id = failed.id
    summary = dict(item.essence_check_summary)
    summary["generation_attempt"] = {
        "reason": "CONTENT_AI_HARD_FINDING",
        "retry_class": "SAMPLE_RECOVERABLE",
        AUTO_CORRECTION_EXHAUSTED_KEY: True,
        "next_retry_at": _kst(SLOT, 1).to("UTC").isoformat(),
    }
    summary[mc.AUTO_CORRECTION_KEY] = {"passes": 2, "rereviews": 2, "exhausted": True}
    item.essence_check_summary = summary
    db.commit()

    async def no_incident(_incident_id):
        return True

    monkeypatch.setattr(topic_swap_fallback, "_recover_incident_async", no_incident)
    philosophy = _approved_philosophy(db, hospital)
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_a: philosophy)
    one_am = _kst(SLOT, 1)
    _set_clock(monkeypatch, one_am)

    report = topic_swap_fallback.swap_exhausted_topics(
        db,
        window_start=SLOT - timedelta(days=7),
        window_end=SLOT + timedelta(days=1),
        now=one_am.to("UTC").datetime,
    )

    assert report.swapped == 1
    db.expire_all()
    swapped = db.get(ContentItem, item.id)
    assert swapped.query_target_id == holter.id != bone.id
    assert swapped.body is None and swapped.title is None
    assert len(swapped.topic_swap_history) == 1
    assert swapped.topic_swap_history[0]["reason_code"] == "CONTENT_AI_HARD_FINDING"
