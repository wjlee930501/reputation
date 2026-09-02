"""월간 인용 귀속 집계 — "AI 답변이 우리 글을 실제로 읽었는가"를 셀 단위로 센다.

측정 셀(질문×AI 서비스)의 **채택 답변**만 센다. 반복 측정 중 채택되지 않은 시도를
같이 세면 인용 수가 반복 횟수만큼 부풀어 리포트 숫자가 거짓이 된다.
"""
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.monthly_sov_types import CellAttempt, ManifestCellInput
from app.services.report_attribution import (
    CitationAttributionInput,
    build_citation_attribution,
)

PLATFORM_HOST = "reputation.motionlabs.kr"
FAQ_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
LOCAL_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture(autouse=True)
def _platform_base(monkeypatch):
    monkeypatch.setattr(settings, "SITE_BASE_URL", f"https://{PLATFORM_HOST}", raising=False)


HOSPITAL = SimpleNamespace(slug="jangpyeonhan", aeo_domain=None)
CONTENTS = [
    SimpleNamespace(id=FAQ_ID, title="치질 수술 FAQ", content_type="FAQ"),
    SimpleNamespace(id=LOCAL_ID, title="강남 치질 병원 안내", content_type="LOCAL"),
]


def _cell(query_key: str, platform: str, *, record_id: uuid.UUID | None, state: str = "SUCCESS"):
    attempts = ()
    if record_id is not None:
        attempts = (
            CellAttempt(
                record_id=record_id,
                measured_at=datetime(2026, 8, 1, tzinfo=UTC),
                succeeded=True,
                is_mentioned=True,
            ),
        )
    return ManifestCellInput(
        query_key=query_key,
        query_text=f"질문 {query_key}",
        platform=platform,
        query_intent="LOCAL",
        state=state,
        query_matrix_id=None,
        query_target_id=None,
        query_variant_id=None,
        query_intent_source="FROZEN",
        attempts=attempts,
    )


def _record(record_id: uuid.UUID, urls: list[str]):
    return SimpleNamespace(id=record_id, source_urls=urls)


def _build(cells, records, *, contents=CONTENTS, hospital=HOSPITAL):
    return build_citation_attribution(
        CitationAttributionInput(
            hospital=hospital,
            cells=tuple(cells),
            records_by_id={record.id: record for record in records},
            content_items=contents,
        )
    )


def test_counts_cells_that_cited_any_owned_url():
    r1, r2, r3 = (uuid.uuid4() for _ in range(3))
    cells = [
        _cell("q1", "chatgpt", record_id=r1),
        _cell("q1", "gemini", record_id=r2),
        _cell("q2", "chatgpt", record_id=r3),
    ]
    records = [
        _record(r1, [f"https://{PLATFORM_HOST}/jangpyeonhan/contents/{FAQ_ID}"]),
        _record(r2, [f"https://jangpyeonhan.{PLATFORM_HOST}/doctor"]),
        _record(r3, ["https://blog.naver.com/other/1"]),
    ]

    summary = _build(cells, records)

    assert summary["measured_cell_count"] == 3
    assert summary["cited_cell_count"] == 2
    assert summary["cited_cell_pct"] == 66.7
    assert summary["content_cited_cell_count"] == 1
    assert summary["hub_cited_cell_count"] == 1


def test_lists_cited_items_with_counts_and_the_queries_they_were_cited_for():
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    cells = [_cell("q1", "chatgpt", record_id=r1), _cell("q2", "gemini", record_id=r2)]
    faq_url = f"https://{PLATFORM_HOST}/jangpyeonhan/contents/{FAQ_ID}"
    records = [
        _record(r1, [faq_url]),
        _record(r2, [faq_url, f"https://{PLATFORM_HOST}/jangpyeonhan/contents/{LOCAL_ID}"]),
    ]

    summary = _build(cells, records)

    assert summary["cited_content_count"] == 2
    top = summary["cited_items"][0]
    assert top["title"] == "치질 수술 FAQ"
    assert top["content_type"] == "FAQ"
    assert top["cited_cell_count"] == 2
    assert top["queries"] == [
        {"query_text": "질문 q1", "platform_label": "ChatGPT"},
        {"query_text": "질문 q2", "platform_label": "Gemini"},
    ]
    assert [row["title"] for row in summary["cited_items"]] == ["치질 수술 FAQ", "강남 치질 병원 안내"]


def test_hub_pages_are_reported_even_when_no_article_was_cited():
    record_id = uuid.uuid4()
    cells = [_cell("q1", "chatgpt", record_id=record_id)]
    records = [_record(record_id, [f"https://{PLATFORM_HOST}/jangpyeonhan"])]

    summary = _build(cells, records)

    assert summary["cited_items"] == []
    assert summary["hub_pages"] == [{
        "page_key": "home",
        "label": "병원 홈",
        "cited_cell_count": 1,
        "queries": [{"query_text": "질문 q1", "platform_label": "ChatGPT"}],
    }]
    assert summary["cited_cell_count"] == 1


def test_unmeasured_cells_are_not_counted_in_the_denominator():
    cells = [_cell("q1", "chatgpt", record_id=None, state="FAILED")]

    summary = _build(cells, [])

    assert summary["measured_cell_count"] == 0
    assert summary["cited_cell_count"] == 0
    assert summary["cited_cell_pct"] is None


def test_repeated_urls_inside_one_answer_do_not_inflate_the_cell_count():
    record_id = uuid.uuid4()
    url = f"https://{PLATFORM_HOST}/jangpyeonhan/contents/{FAQ_ID}"
    cells = [_cell("q1", "chatgpt", record_id=record_id)]
    records = [_record(record_id, [url, f"{url}/", f"http://www.{PLATFORM_HOST}/jangpyeonhan/contents/{FAQ_ID}?x=1"])]

    summary = _build(cells, records)

    assert summary["cited_items"][0]["cited_cell_count"] == 1
    assert summary["cited_items"][0]["cited_url_count"] == 1


def test_lookalike_host_is_not_counted_as_our_citation():
    record_id = uuid.uuid4()
    cells = [_cell("q1", "chatgpt", record_id=record_id)]
    records = [
        _record(record_id, [f"https://{PLATFORM_HOST}.evil.com/jangpyeonhan/contents/{FAQ_ID}"])
    ]

    summary = _build(cells, records)

    assert summary["cited_cell_count"] == 0
    assert summary["cited_items"] == []


def test_owned_url_for_an_unknown_article_still_counts_the_cell_as_cited():
    record_id = uuid.uuid4()
    cells = [_cell("q1", "chatgpt", record_id=record_id)]
    records = [_record(record_id, [f"https://{PLATFORM_HOST}/jangpyeonhan/contents/{uuid.uuid4()}"])]

    summary = _build(cells, records)

    assert summary["cited_cell_count"] == 1
    assert summary["cited_content_count"] == 0


def test_missing_record_does_not_crash_the_aggregation():
    cells = [_cell("q1", "chatgpt", record_id=uuid.uuid4())]

    summary = _build(cells, [])

    assert summary["measured_cell_count"] == 1
    assert summary["cited_cell_count"] == 0
