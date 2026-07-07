"""콘텐츠 발행-AI 언급 상관 집계 + 리포트 신규 섹션 렌더 회귀."""
from datetime import datetime
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services.report_engine import (
    TEMPLATE_DIR,
    build_content_attribution_summary,
)


def _rec(query_id, *, mentioned, target_id=None, query_text=None, status="SUCCESS", raw="ok"):
    """SoV 레코드 스텁 — build_content_attribution_summary가 읽는 속성만."""
    return SimpleNamespace(
        query_id=query_id,
        ai_query_target_id=target_id,
        is_mentioned=mentioned,
        measurement_status=status,
        raw_response=raw,
        query=SimpleNamespace(query_text=query_text) if query_text else None,
        ai_query_target=None,
    )


def _content(content_type, title, *, target_id=None):
    return SimpleNamespace(content_type=content_type, title=title, query_target_id=target_id)


# ── 유형별 편수 ───────────────────────────────────────────────
def test_content_type_counts_covers_all_seven_types():
    contents = [
        _content("FAQ", "q1"),
        _content("FAQ", "q2"),
        _content("DISEASE", "d1"),
        _content("LOCAL", "l1"),
    ]
    summary = build_content_attribution_summary(
        published_contents=contents,
        prev_published_contents=[],
        this_records=[],
        prev_records=[],
        sov_pct=10.0,
        prev_sov_pct=None,
        change_pct=None,
    )
    counts = summary["content_type_counts"]
    assert counts == {
        "FAQ": 2, "DISEASE": 1, "TREATMENT": 0, "COLUMN": 0,
        "HEALTH": 0, "LOCAL": 1, "NOTICE": 0,
    }
    assert summary["published_count"] == 4


# ── 신규 언급 판정 ────────────────────────────────────────────
def test_new_mention_prev_not_mentioned_this_mentioned():
    # 쿼리 A: 전월 미언급(False) → 이번 달 언급(True) = 신규
    this_records = [_rec("A", mentioned=True, query_text="강남 치질 수술")]
    prev_records = [_rec("A", mentioned=False, query_text="강남 치질 수술")]
    summary = build_content_attribution_summary(
        published_contents=[],
        prev_published_contents=[],
        this_records=this_records,
        prev_records=prev_records,
        sov_pct=50.0,
        prev_sov_pct=0.0,
        change_pct=50.0,
    )
    assert summary["new_mention_count"] == 1
    assert summary["new_mention_queries"][0]["query_text"] == "강남 치질 수술"


def test_new_mention_prev_unmeasured_counts_as_new():
    # 전월 측정 자체가 없던 쿼리도 이번 달 언급 시작이면 신규로 인정.
    this_records = [_rec("A", mentioned=True, query_text="탈장 수술")]
    summary = build_content_attribution_summary(
        published_contents=[],
        prev_published_contents=[],
        this_records=this_records,
        prev_records=[],
        sov_pct=100.0,
        prev_sov_pct=None,
        change_pct=None,
    )
    assert summary["new_mention_count"] == 1


def test_prev_failed_only_counts_as_new():
    # 전월 레코드가 있으나 전부 FAILED(측정 실패) → 전월 미측정 취급 → 신규.
    this_records = [_rec("A", mentioned=True, query_text="맹장 수술")]
    prev_records = [_rec("A", mentioned=False, status="FAILED", raw="", query_text="맹장 수술")]
    summary = build_content_attribution_summary(
        published_contents=[],
        prev_published_contents=[],
        this_records=this_records,
        prev_records=prev_records,
        sov_pct=100.0,
        prev_sov_pct=None,
        change_pct=None,
    )
    assert summary["new_mention_count"] == 1


def test_already_mentioned_last_month_is_not_new():
    this_records = [_rec("A", mentioned=True, query_text="치핵")]
    prev_records = [_rec("A", mentioned=True, query_text="치핵")]
    summary = build_content_attribution_summary(
        published_contents=[],
        prev_published_contents=[],
        this_records=this_records,
        prev_records=prev_records,
        sov_pct=100.0,
        prev_sov_pct=100.0,
        change_pct=0.0,
    )
    assert summary["new_mention_count"] == 0


def test_not_mentioned_this_month_is_not_new():
    this_records = [_rec("A", mentioned=False, query_text="치핵")]
    summary = build_content_attribution_summary(
        published_contents=[],
        prev_published_contents=[],
        this_records=this_records,
        prev_records=[],
        sov_pct=0.0,
        prev_sov_pct=None,
        change_pct=None,
    )
    assert summary["new_mention_count"] == 0


def test_new_mention_capped_at_five():
    this_records = [_rec(f"Q{i}", mentioned=True, query_text=f"쿼리{i}") for i in range(8)]
    summary = build_content_attribution_summary(
        published_contents=[],
        prev_published_contents=[],
        this_records=this_records,
        prev_records=[],
        sov_pct=100.0,
        prev_sov_pct=None,
        change_pct=None,
    )
    assert summary["new_mention_count"] == 5
    assert len(summary["new_mention_queries"]) == 5


# ── 연관 콘텐츠 연결 ──────────────────────────────────────────
def test_related_content_via_query_target_link():
    # exposure_content_linker가 설정한 query_target_id 링크를 재사용.
    this_records = [_rec("A", mentioned=True, target_id="T1", query_text="강남 치질")]
    contents = [
        _content("FAQ", "치질 자가진단 FAQ", target_id="T1"),
        _content("DISEASE", "무관한 글", target_id="T9"),
    ]
    summary = build_content_attribution_summary(
        published_contents=contents,
        prev_published_contents=[],
        this_records=this_records,
        prev_records=[],
        sov_pct=100.0,
        prev_sov_pct=None,
        change_pct=None,
    )
    assert summary["new_mention_queries"][0]["related_contents"] == ["치질 자가진단 FAQ"]


def test_related_content_keyword_fallback_when_no_link():
    this_records = [_rec("A", mentioned=True, query_text="탈장 수술 회복")]
    contents = [_content("TREATMENT", "탈장 수술 후 회복 안내")]
    summary = build_content_attribution_summary(
        published_contents=contents,
        prev_published_contents=[],
        this_records=this_records,
        prev_records=[],
        sov_pct=100.0,
        prev_sov_pct=None,
        change_pct=None,
    )
    assert summary["new_mention_queries"][0]["related_contents"] == ["탈장 수술 후 회복 안내"]


def test_related_content_empty_when_nothing_matches():
    this_records = [_rec("A", mentioned=True, query_text="갑상선 초음파")]
    contents = [_content("FAQ", "치질 FAQ")]
    summary = build_content_attribution_summary(
        published_contents=contents,
        prev_published_contents=[],
        this_records=this_records,
        prev_records=[],
        sov_pct=100.0,
        prev_sov_pct=None,
        change_pct=None,
    )
    assert summary["new_mention_queries"][0]["related_contents"] == []


# ── 템플릿 렌더 ───────────────────────────────────────────────
def _render(attribution, **overrides):
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html",)),
    )
    template = env.get_template("report.html")
    hospital = SimpleNamespace(
        name="장편한외과의원", region=["강남"], specialties=["대장항문외과"], plan="PLAN_16"
    )
    ctx = dict(
        hospital=hospital,
        report_type="MONTHLY",
        period_label="2026-07",
        period_start=datetime(2026, 7, 1),
        period_end=datetime(2026, 7, 31),
        sov_pct=42.0,
        sov_measured=True,
        published_count=3,
        repeat_count=5,
        attribution=attribution,
        generated_at=datetime(2026, 7, 31),
    )
    ctx.update(overrides)
    return template.render(**ctx)


def _sample_attribution(**over):
    base = dict(
        content_type_counts={"FAQ": 2, "DISEASE": 1, "TREATMENT": 0, "COLUMN": 0,
                             "HEALTH": 0, "LOCAL": 0, "NOTICE": 0},
        prev_content_type_counts={"FAQ": 1, "DISEASE": 0, "TREATMENT": 0, "COLUMN": 0,
                                  "HEALTH": 0, "LOCAL": 0, "NOTICE": 0},
        published_count=3,
        prev_published_count=1,
        new_mention_queries=[{"query_text": "강남 치질 수술", "related_contents": ["치질 FAQ"]}],
        new_mention_count=1,
        sov_pct=42.0,
        prev_sov_pct=30.0,
        change_pct=12.0,
    )
    base.update(over)
    return base


def test_report_renders_attribution_section():
    html = _render(_sample_attribution())
    assert "콘텐츠 발행과 AI 언급 변화" in html
    assert "강남 치질 수술" in html
    assert "치질 FAQ" in html
    # 상관 표현 존재, 인과 단정 금지 (섹션 문구 한정 — head의 CSS는 제외).
    section = html[html.index("콘텐츠 발행과 AI 언급 변화"):]
    assert "언급이 시작되었습니다" in section
    assert "덕분에" not in section
    # 의료광고 금지표현이 신규 섹션 문구에 없어야 한다.
    for banned in ["1등", "최고", "완치", "100%", "유일", "성공률"]:
        assert banned not in section


def test_report_renders_empty_new_mentions_branch():
    html = _render(_sample_attribution(new_mention_queries=[], new_mention_count=0))
    assert "새로 언급이 시작된 쿼리는 확인되지 않았습니다" in html


def test_report_attribution_coheres_with_no_sov_data():
    # 측정 데이터 없음(sov None)과 정합 — %.1f 포매팅으로 터지지 않아야 한다.
    attribution = _sample_attribution(sov_pct=None, prev_sov_pct=None, change_pct=None)
    html = _render(attribution, sov_pct=None, sov_measured=False)
    assert "측정 데이터 없음" in html
    assert "0.0%" not in html


def test_report_without_attribution_omits_section():
    html = _render(None)
    assert "콘텐츠 발행과 AI 언급 변화" not in html
