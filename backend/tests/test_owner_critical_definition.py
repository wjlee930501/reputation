"""2026-10-15 대표 확정 Critical 정의 — "이건 의학 논문이 아니야".

Critical은 둘뿐이다: (1) 환자유인·불법 의료광고(완치·효과 장담·보장, 최저가, 할인·무료·이벤트 유인),
(2) 병원으로서 무책임한 허위정보(위험한 행동 권고·위험한 거짓 진술). 누락(119·주의·금기 안내 없음),
병원 사실 불일치, 보장이 아닌 과장, 사소한 사실 오류는 Critical이 아니고 발행을 막지 않는다 — 진술 문장은
지우고, 지울 문장이 없으면 기록만 남긴다. 양쪽을 모두 고정한다.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import settings
from app.services import content_minimal_correction as mc
from app.services.content_ai_review import (
    ContentAiFindingSeverity,
    ContentAiReviewStatus,
    _build_review,
    candidate_review_coverage,
    candidate_sha256,
)
from app.services.content_publication import _blocking_ai_review_state
from app.services.finding_criticality import (
    CRITICAL_RULE,
    CRITICAL_RULE_KEY,
    Criticality,
    classify_finding,
    finding_blocks,
    finding_is_record_only,
    flag_findings_are_critical,
    review_has_critical_finding,
    review_still_blocks,
)
from app.workers import incident_backlog
from app.workers import post_publish_ai_review as sweep
from tests.test_post_publish_critical_findings import (  # noqa: F401 - pytest fixture
    _DB,
    KEY,
    NOW,
    _flag,
    _item,
    _lock_to,
    _Session,
    calls,
)

C, N = Criticality.CRITICAL, Criticality.NONCRITICAL

SAFE = "허리 통증은 원인이 다양해 진찰과 영상 검사로 원인을 확인합니다."
STAFF = "본원에는 내과 전문의 6명과 영상의학과 전문의 1명이 상주합니다."
CHEST_REST = "흉통이 있어도 집에서 쉬면 됩니다."
WEAK_ER = "가슴 통증이 심하면 필요하면 응급실 방문을 고려하세요."
SUGAR = "공복혈당 100mg/dL은 정상 범위의 예입니다."
BODY = "## 안내\n" + " ".join([SAFE] * 60) + f"\n{STAFF}\n{CHEST_REST}\n{WEAK_ER}\n{SUGAR}\n"
CONTENT = {
    "title": "가슴 통증, 언제 병원에 가야 할까요?",
    "body": BODY,
    "meta_description": "가슴 통증 내원 시점 안내",
    "faq_question": None,
    "faq_answer_summary": None,
}


def _v4(severity, kind, message, quote="", critical=False):
    """확정 정의로 판정한 검수 지적(v4)."""

    return {
        "severity": severity,
        "kind": kind,
        "message": message,
        "quote": quote,
        "critical": critical,
        CRITICAL_RULE_KEY: CRITICAL_RULE,
    }


def _v3(severity, kind, message, quote="", critical=True):
    """옛 넓은 정의(응급 안내 누락·병원 사실까지 Critical)로 판정한 지적 — 규칙 표지가 없다."""

    return {"severity": severity, "kind": kind, "message": message, "quote": quote, "critical": critical}


# ── 1. 정의의 양쪽(v4 — 검수 모델이 확정 정의로 판정) ─────────────────────────────────────


@pytest.mark.parametrize(
    ("finding", "expected"),
    [
        # Critical: 위험한 권고·거짓 진술(모델 true), 유인 어휘(모델 false여도)
        (_v4("HARD", "MEDICAL_SAFETY", "흉통에도 집에서 쉬라고 권합니다", CHEST_REST, True), C),
        (_v4("HARD", "MEDICAL_SAFETY", "약 용량이 틀렸습니다", SAFE, True), C),
        (_v4("HARD", "HOSPITAL_FACT", "효과를 보장한다는 문장", SAFE), C),
        (_v4("HARD", "STYLE", "첫 방문 할인 문구", SAFE), C),
        (_v4("HARD", "STYLE", "지역 최저가 문구", SAFE), C),
        (_v4("HARD", "MEDICAL_SAFETY", "한 번이면 완치된다는 문장", SAFE), C),
        # 판정이 없으면(null) fail closed
        (_v4("HARD", "MEDICAL_SAFETY", "판정 없음", SAFE, None), C),
        # 비-Critical: 누락·병원 사실·사소한 오류·약한 응급 권고
        (_v4("HARD", "MEDICAL_SAFETY", "119·응급실 안내가 본문 어디에도 없습니다"), N),
        (_v4("HARD", "HOSPITAL_FACT", "의료진 수는 프로필에 근거가 없습니다", STAFF), N),
        (_v4("HARD", "MEDICAL_SAFETY", "공복혈당 100은 경계값입니다", SUGAR), N),
        (_v4("HARD", "MEDICAL_SAFETY", "응급 권고가 약합니다", WEAK_ER), N),
    ],
)
def test_v4_reviewer_verdict_follows_the_owner_definition(finding, expected) -> None:
    assert classify_finding(finding, CONTENT) is expected


# ── 2. v3 판정의 결정적 재등급(돈을 쓰지 않는다) ─────────────────────────────────────────

# 10/15 03:10 신기한속내과연합의원 공개 글 7편의 실제 지적 문구(운영 DB, v3 — 앞부분).
SINGIHAN_V3 = {
    "6ce8780c 대장내시경": [
        _v3("HARD", "HOSPITAL_FACT", "의료진 수(내과 전문의 6명, 영상의학과 전문의 1명)는 hospital_profile에 "
            "근거가 없습니다. 프로필에는 대표원장 2인만 있으므로", critical=False),
        _v3("HARD", "HOSPITAL_FACT", "예약제 운영은 프로필에 근거가 없습니다. 또 평일 점심시간(13:00~14:00)이 "
            "빠져 있어 진료시간을 오해할 수 있습니다.", critical=False),
    ],
    "86136d2d 헬리코박터": [
        _v3("HARD", "MEDICAL_SAFETY", "흑색변·빈혈 등 출혈 의심 증상을 증상 목록에만 넣고, 즉시 진료가 필요하다는 "
            "안내가 본문 어디에도 없습니다. 검은 변, 피를 토함, 심한 복통"),
        _v3("UNCERTAIN", "MEDICAL_SAFETY", "제균 치료 시 약물 알레르기(특히 페니실린) 병력 고지와 치료 중 음주 주의 "
            "같은 주의 대상 안내가 없습니다. 한 문장 보강을 권합니다.", critical=False),
    ],
    "6dc9b861 진료비": [
        _v3("HARD", "HOSPITAL_FACT", "hospital_profile과 approved_essence에는 수면(진정)내시경 시행 근거가 "
            "없습니다. 이 글은 병원 내시경에서 수면내시경을"),
        _v3("HARD", "HOSPITAL_FACT", "승인된 병원 사실에 없는 수면내시경 운영을 전제로 합니다. 진정 후 "
            "주의사항(운전 금지 등)과 위험 정보도 충분히 다루지 않았습니다."),
    ],
    "cbdafc4e CT": [
        _v3("HARD", "MEDICAL_SAFETY", "조영제 후 호흡곤란·전신 두드러기·얼굴 부종·의식 저하 등은 아나필락시스일 수 "
            "있습니다. 귀가 후에도 생길 수 있으므로 의료진에게 알리는 것에 더해, 증상이 심하거나 호흡곤란이 "
            "있으면 즉시 119 또는 가까운 응급실을 안내하세요."),
        _v3("UNCERTAIN", "MEDICAL_SAFETY", "신장 질환이나 심부전 등으로 수분 제한이 있는 환자에게는 맞지 않을 수 "
            "있습니다. 개인 상태에 따라 의료진 안내를 따르라는 단서가 필요합니다."),
    ],
    "1b811c20 검진 결과": [
        _v3("HARD", "MEDICAL_SAFETY", "선종성 용종의 추적 주기를 일반적으로 1년이라고 안내하는 것은 부정확합니다. "
            "저위험 선종은 보통 수 년(예: 3년) 간격이고"),
        _v3("HARD", "MEDICAL_SAFETY", "공복혈당 100mg/dL은 공복혈당장애 기준 경계값이어서 '정상'으로 예시하면 "
            "오해를 줄 수 있습니다."),
        _v3("HARD", "MEDICAL_SAFETY", "심근경색 의심은 응급 상황입니다. '필요하면 응급실 방문'은 대응을 지연시킬 수 "
            "있으니 즉시 119 또는 응급실로 가야 한다고 분명히 쓰세요."),
    ],
    "48e66b6a 복부초음파": [
        _v3("HARD", "MEDICAL_SAFETY", "황달이나 심한 복통은 응급 상황일 수 있습니다. 그런데 글에는 응급실 방문이나 "
            "119 안내가 어디에도 없습니다. 복부초음파 예약을 기다리지 말고 즉시 진료나 응급실이 필요한 경고 "
            "증상을 추가하세요."),
    ],
}
# 같은 실행의 정기 건강검진 글(fca90112): 결과 보장 어휘가 지적 문구에 있다 — 안전망 어휘라 Critical로 남는다.
GUARANTEE_V3 = _v3(
    "HARD", "HOSPITAL_FACT", "'정확한 진단부터 치료까지 책임진다'는 승인된 근거가 없습니다. 진단 정확성과 치료 "
    "책임을 단정하는 표현이라 결과 보장으로 읽힐 수 있습니다."
)
PROGNOSIS_V3 = _v3(
    "HARD", "MEDICAL_SAFETY", "모든 질환에 해당하는 것처럼 예후를 일반화했습니다. 질환별로 다르고 개인차가 있음을 "
    "밝혀 '일부 질환에서는 조기 발견이 치료 선택지를 넓힐 수"
)


@pytest.mark.parametrize("post", sorted(SINGIHAN_V3))
def test_singihan_v3_findings_are_no_longer_critical(post) -> None:
    findings = SINGIHAN_V3[post]
    for finding in findings:
        assert classify_finding(finding) is N, finding["message"]
    assert flag_findings_are_critical(findings) is False


def test_v3_exaggeration_is_not_critical_but_a_guarantee_word_still_is() -> None:
    assert classify_finding(PROGNOSIS_V3) is N
    assert classify_finding(GUARANTEE_V3) is C
    assert flag_findings_are_critical([PROGNOSIS_V3, GUARANTEE_V3]) is True


@pytest.mark.parametrize(
    "message",
    [
        "흉통이 있어도 집에서 쉬면 된다고 안내합니다.",
        "혈변은 걱정할 필요 없다고 단정합니다.",
        "흑색변을 단순 소화불량으로 안내했습니다.",
        "고혈압약을 임의로 중단해도 된다고 씁니다.",
        "아세트아미노펜 하루 용량을 8g으로 잘못 적었습니다.",
        "위험한 민간요법을 권합니다.",
    ],
)
def test_v3_stated_dangerous_recommendations_stay_critical(message) -> None:
    assert classify_finding(_v3("HARD", "MEDICAL_SAFETY", message)) is C


def test_v3_reviewer_false_stays_noncritical_and_null_stays_critical() -> None:
    assert classify_finding(_v3("HARD", "MEDICAL_SAFETY", "혈변은 걱정할 필요 없다고 씁니다", critical=False)) is N
    assert classify_finding(_v3("HARD", "HOSPITAL_FACT", "판정 없음", critical=None)) is C


# ── 3. 누락은 막지 않는다 — 기록만 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "finding",
    [
        _v4("HARD", "MEDICAL_SAFETY", "119·응급실 안내가 본문 어디에도 없습니다"),
        _v4("UNCERTAIN", "MEDICAL_SAFETY", "항응고제 복용자 주의 안내가 없습니다", SAFE),
        _v4("HARD", "MEDICAL_SAFETY", "조영제 알레르기 문진을 추가하세요", SAFE),
        _v4("HARD", "HOSPITAL_FACT", "개원 연도를 확인할 수 없습니다"),
        # 응급 안내가 든 문장은 지우지 않는다 — 비-Critical 지적은 기록만 남는다.
        _v4("HARD", "MEDICAL_SAFETY", "응급 권고가 약합니다", WEAK_ER),
        # v3로 판정된 누락도 같다.
        SINGIHAN_V3["86136d2d 헬리코박터"][0],
    ],
)
def test_omissions_and_undeletable_noncritical_findings_are_record_only(finding) -> None:
    assert finding_is_record_only(finding, CONTENT)
    assert not finding_blocks(finding, CONTENT)


def test_noncritical_statements_still_block_until_deleted_and_critical_always_blocks() -> None:
    staff = _v4("HARD", "HOSPITAL_FACT", "의료진 수는 프로필에 근거가 없습니다", STAFF)
    danger = _v4("HARD", "MEDICAL_SAFETY", "흉통에도 집에서 쉬라고 권합니다", CHEST_REST, True)
    omission_but_critical = _v4("HARD", "MEDICAL_SAFETY", "흉통에 쉬라고 하고 119 안내가 없습니다", "", True)

    assert finding_blocks(staff, CONTENT) and not finding_is_record_only(staff, CONTENT)
    assert finding_blocks(danger, CONTENT)
    assert finding_blocks(omission_but_critical, CONTENT)


def _stored_review(content, *findings):
    return {
        "status": "REVISE",
        "blocking": True,
        "findings": list(findings),
        "schema_version": "content-review-v3",
        "candidate_sha256": candidate_sha256(content),
        "coverage": candidate_review_coverage(content),
    }


def test_stored_v3_omission_block_no_longer_holds_the_publish_gate() -> None:
    item = dict(CONTENT)
    omission = SINGIHAN_V3["48e66b6a 복부초음파"][0]
    review = _stored_review(item, omission)

    class _Item:
        status = None
        essence_check_summary = {"ai_review": review}

    for name, value in item.items():
        setattr(_Item, name, value)
    assert not review_still_blocks(review, _Item)
    assert _blocking_ai_review_state(_Item) is None

    danger = _v3("HARD", "MEDICAL_SAFETY", "흉통이 있어도 집에서 쉬면 된다고 안내합니다", CHEST_REST)
    _Item.essence_check_summary = {"ai_review": _stored_review(item, danger)}
    assert _blocking_ai_review_state(_Item)[0] == "CURRENT"
    assert review_has_critical_finding(_Item.essence_check_summary["ai_review"], _Item)


# ── 4. 새 검수 출력(v4): 누락은 SOFT 기록, 진술 문장은 차단 ────────────────────────────────


def _raw(severity, kind, message, quote="", critical=False):
    return {"severity": severity, "kind": kind, "message": message, "quote": quote, "critical": critical}


def test_new_review_records_omissions_and_passes() -> None:
    review = _build_review(
        {
            "decision": "REVISE",
            "confidence": 0.9,
            "findings": [
                _raw("HARD", "MEDICAL_SAFETY", "흉통에 대한 119·응급실 안내가 본문 어디에도 없습니다."),
                _raw("SOFT", "MEDICAL_SAFETY", "조영제 알레르기 주의 안내를 추가하세요."),
            ],
            "summary": "누락뿐",
        },
        reviewed_content=CONTENT,
    )

    assert review.status == ContentAiReviewStatus.PASS
    assert review.blocking_findings == ()
    payload = review.payload()
    assert payload["blocking"] is False and payload["schema_version"] == "content-review-v4"
    assert all(f["severity"] == "SOFT" and f["record_only"] == "OMISSION" for f in payload["findings"])
    assert all(f[CRITICAL_RULE_KEY] == CRITICAL_RULE for f in payload["findings"])
    assert payload["findings"][0]["original_severity"] == "HARD"


def test_new_review_keeps_statement_and_critical_findings_blocking() -> None:
    review = _build_review(
        {
            "decision": "REVISE",
            "confidence": 0.9,
            "findings": [
                _raw("HARD", "HOSPITAL_FACT", "의료진 수는 프로필에 근거가 없습니다.", STAFF),
                _raw("HARD", "MEDICAL_SAFETY", "흉통에도 집에서 쉬라고 권합니다.", CHEST_REST, True),
            ],
            "summary": "차단",
        },
        reviewed_content=CONTENT,
    )

    assert review.status == ContentAiReviewStatus.REVISE
    assert [f.severity for f in review.blocking_findings] == [ContentAiFindingSeverity.HARD] * 2
    payload = review.payload()
    assert review_has_critical_finding(payload, CONTENT)
    assert classify_finding(payload["findings"][0], CONTENT) is N


# ── 5. 응급 템플릿 삽입은 기본 꺼짐 ──────────────────────────────────────────────────


def test_emergency_template_insertion_is_off_by_default(monkeypatch) -> None:
    assert settings.CONTENT_EMERGENCY_TEMPLATE_INSERTION_ENABLED is False
    finding = _v4("HARD", "MEDICAL_SAFETY", "흉통에 쉬라고 하고 119·응급 안내가 없습니다", CHEST_REST, True)
    review = _stored_review(CONTENT, finding)

    plan = mc.plan_corrections(CONTENT, review)
    assert plan.insertions == ()
    assert [target.sentence for target in plan.targets] == [CHEST_REST]

    monkeypatch.setattr(settings, "CONTENT_EMERGENCY_TEMPLATE_INSERTION_ENABLED", True)
    assert mc.plan_corrections(CONTENT, review).insertions != ()


# ── 6. 공개 글: 열린 사고·미전달 알림 의도를 새 정의로 다시 본다 ─────────────────────────


def test_resolver_quietly_closes_v3_flags_that_are_no_longer_critical() -> None:
    item = _flag(_item(), *SINGIHAN_V3["1b811c20 검진 결과"])
    incident = type("I", (), {"source_id": str(item.id)})()

    assert incident_backlog._post_publish_flag_settled(_Session(item), incident, NOW) == (
        "flag_not_critical"
    )

    still = _flag(_item(), PROGNOSIS_V3, GUARANTEE_V3)
    assert incident_backlog._post_publish_flag_settled(_Session(still), incident, NOW) is None


def test_pending_alert_intent_is_closed_without_slack_when_no_longer_critical(
    monkeypatch, calls  # noqa: F811 - pytest fixture
):
    item = _flag(_item(), *SINGIHAN_V3["cbdafc4e CT"])
    summary = dict(item.essence_check_summary)
    summary[KEY] = {
        "requested_at": NOW.isoformat(),
        "snapshot": {"title": item.title, "findings": ["조영제"]},
        "enqueued_at": None,
        "critical_alert_sent_at": None,
        "closed_at": None,
    }
    item.essence_check_summary = summary
    _lock_to(monkeypatch, item)

    assert sweep._deliver_critical_alert(_DB(), item.id, asyncio.run, now=NOW) == "CLOSED"
    assert calls["alerts"] == [] and calls["critical"] == []
    assert item.essence_check_summary[KEY]["closed_reason"] == "not_critical"


# ── 7. Codex 1차 지적: 재등급이 유인 광고·인용된 위험 권고·옛 지적을 놓치지 않는다 ──────────────


def test_v3_inducement_beyond_the_four_words_stays_critical() -> None:
    assert classify_finding(_v3("HARD", "HOSPITAL_FACT", "첫 진료 무료 이벤트 문구입니다", SAFE)) is C
    assert classify_finding(_v3("HARD", "STYLE", "특가 프로모션 안내", SAFE)) is C
    # 모델이 false라 했어도 v3에서는 그대로다(v3 false는 옛 넓은 정의에서도 비-Critical).
    assert classify_finding(_v3("HARD", "HOSPITAL_FACT", "무료 이벤트", SAFE, critical=False)) is N


def test_v3_dangerous_statement_is_caught_from_the_quoted_sentence() -> None:
    finding = _v3("HARD", "MEDICAL_SAFETY", "의학적으로 부적절한 안내입니다", CHEST_REST)
    assert classify_finding(finding, CONTENT) is C
    assert classify_finding(_v3("HARD", "MEDICAL_SAFETY", "부적절합니다", "혈변은 걱정할 필요가 없습니다.")) is C
    assert classify_finding(_v3("HARD", "MEDICAL_SAFETY", "부적절", "약은 증상이 나아지면 끊어도 됩니다.")) is C


def test_legacy_findings_without_the_key_are_regraded_on_published_flags_only() -> None:
    legacy_danger = {"severity": "HARD", "kind": "MEDICAL_SAFETY", "message": "혈변은 걱정할 필요 없다고 씁니다"}
    legacy_omission = {"severity": "HARD", "kind": "MEDICAL_SAFETY", "message": "119 안내가 없습니다"}
    legacy_fact = {"severity": "HARD", "kind": "HOSPITAL_FACT", "message": "개원 연도 근거 없음", "quote": STAFF}
    synthetic = {
        "severity": "UNCERTAIN",
        "kind": "MEDICAL_SAFETY",
        "message": "독립 AI 검수의 확신이 충분하지 않아 자동 재검수가 필요합니다.",
    }

    # 공개 글 표시: 돈 없이 확정 정의로 다시 매긴다.
    assert flag_findings_are_critical([legacy_danger]) is True
    assert flag_findings_are_critical([legacy_omission, legacy_fact], CONTENT) is False
    # 발행 전 경로: 등급 없는 옛 차단은 fail closed(종전 그대로 — v4 검수를 한 번 다시 산다).
    assert classify_finding(legacy_omission) is C and finding_blocks(legacy_omission, CONTENT)
    assert classify_finding(synthetic) is C and finding_blocks(synthetic, CONTENT)


def test_heading_or_table_line_quotes_are_record_only() -> None:
    content = dict(CONTENT, body=CONTENT["body"] + "## 본원 의료진 6명이 진료합니다\n| 검사 | 본원 단독 장비 |\n")
    heading = _v4("HARD", "HOSPITAL_FACT", "근거 없음", "본원 의료진 6명이 진료합니다")
    table = _v4("HARD", "HOSPITAL_FACT", "근거 없음", "본원 단독 장비")

    assert finding_is_record_only(heading, content) and finding_is_record_only(table, content)


def test_record_only_uncertain_flags_do_not_wait_for_a_paid_senior_rereview() -> None:
    omission = _v4("UNCERTAIN", "MEDICAL_SAFETY", "항응고제 주의 안내가 없습니다")
    statement = _v4("UNCERTAIN", "HOSPITAL_FACT", "근거 부족", STAFF)

    assert sweep.uncertain_only({"structured_findings": [omission]}) is False
    assert sweep.uncertain_only({"structured_findings": [omission, statement]}) is True


# ── 8. Codex 2차 지적 ────────────────────────────────────────────────────────────


def test_v3_effect_guarantee_wording_stays_critical() -> None:
    assert classify_finding(_v3("HARD", "MEDICAL_SAFETY", "근거 없는 표현", "치료 효과를 장담합니다.")) is C


def test_legacy_uncertain_dangerous_statement_alerts_too() -> None:
    legacy = {"severity": "UNCERTAIN", "kind": "MEDICAL_SAFETY", "message": "부적절",
              "quote": "혈변은 걱정할 필요 없습니다."}
    assert flag_findings_are_critical([legacy]) is True


# 운영 공개 글(10/15 미리보기)의 옛 지적 — '…해도 된다고 오해할 수 있다'는 약한·모호한 안내이고, '임의로 중단하지
# 말라는 문구도 필요'는 누락이다. 확정 정의로 Critical이 아니다(새 알림을 만들지 않는다).
PROD_LEGACY_NONCRITICAL = [
    ("491c9d06", "대소변 장애나 급격한 근력 약화는 응급 평가가 필요한 위험 신호인데, '2~3주 지속 시 병원 확인' 안내와 함께 "
     "있어 기다려도 된다는 오해를 줄 수 있습니다. 이런 증상은 기다리지 말고 즉시 응급실이나 대면 진료를 받아야 한다고 "
     "명시하세요.", "하지만 근력 약화나 대소변 장애 같은 심각한 신경 증상이 나타나면 빠른 수술적 감압이 필요할 수 있습니다."),
    ("2e6a2dd6", "발열이나 반복되는 구토 동반, 체중 감소, 심한 복통, 혈변·검은 변 같은 즉시 진료가 필요한 신호에 대한 안내가 "
     "없습니다. 환자가 대면 진료를 미뤄도 된다고 오해할 수 있습니다.", ""),
    ("331bb4cc", "응급·즉시 대면 진료 신호가 본문 어디에도 없습니다. 환자가 응급 증상도 2~3주 지켜봐도 된다고 오해할 수 "
     "있습니다.", "두통이 반복되거나 어지럼증이 지속될 때 신경외과 진료를 고려하게 됩니다."),
    ("5d8534e7", "이 문장은 근력 약화 같은 신경학적 위험 증상도 2~3주 지켜봐도 되는 것처럼 읽힙니다.",
     "이러한 증상이 2~3주 이상 이어지거나 점점 악화되는 양상이라면 참고 견디기보다 병원에서 상태를 확인하는 것이 안전합니다."),
    ("e9963cc5", "약 복용 조정이 필요하지 않다고 단정했습니다. 아스피린·항응고제 복용자는 사전에 처방의와 상의해야 한다고 "
     "안내해야 합니다. 임의로 중단하지 말라는 문구도 필요합니다.",
     "검사 전 목 부위를 드러낼 수 있도록 편안한 옷차림이 도움이 되며, 검사 당일 금식이나 약 복용 조정은 필요하지 않습니다."),
    ("189c9b7e", "흑색변은 소화관 출혈을 시사할 수 있어 빠른 대면 진료가 필요한 신호입니다. 환자가 2주를 기다려도 된다고 "
     "오해할 수 있습니다.", "2주 이상 반복되거나 흑색변 같은 동반 신호가 있어 점막을 직접 확인해야 하는 상황인지 살핍니다."),
]


@pytest.mark.parametrize(("post", "message", "quote"), PROD_LEGACY_NONCRITICAL)
def test_prod_legacy_misreading_and_omission_flags_raise_no_new_alert(post, message, quote) -> None:
    legacy = {"severity": "HARD", "kind": "MEDICAL_SAFETY", "message": message, "quote": quote}
    assert flag_findings_are_critical([legacy]) is False, post
