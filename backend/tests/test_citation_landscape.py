from types import SimpleNamespace

from app.services.citation_landscape import (
    citation_domain,
    format_domain_list,
    summarize_citation_landscape,
)


def _record(urls, *, mentioned, competitors=(), platform="chatgpt", status="SUCCESS"):
    return SimpleNamespace(
        source_urls=list(urls),
        is_mentioned=mentioned,
        competitor_mentions=[{"name": name, "is_mentioned": hit} for name, hit in competitors],
        ai_platform=platform,
        measurement_status=status,
    )


def test_citation_domain_normalizes_mobile_and_www_hosts():
    assert citation_domain("https://m.blog.naver.com/clinic/1") == "blog.naver.com"
    assert citation_domain("https://www.modoodoc.com/hospitals/1?x=1") == "modoodoc.com"
    assert citation_domain("https://m.example") == "m.example"
    assert citation_domain("not a url") is None
    assert citation_domain(None) is None


def test_landscape_counts_answers_not_url_occurrences_and_splits_competitor_answers():
    records = [
        _record(
            ["https://modoodoc.com/a", "https://modoodoc.com/b", "https://clinic.example/x"],
            mentioned=True,
        ),
        _record(["https://modoodoc.com/c", "https://news.example/1"], mentioned=False, competitors=[("A", True)]),
        _record(["https://news.example/2"], mentioned=False, competitors=[("A", True)], platform="gemini"),
        _record(["https://kdca.go.kr/1"], mentioned=False, competitors=[("A", False)]),
        _record([], mentioned=False),
        _record(["https://ignored.example"], mentioned=None, status="FAILED"),
    ]

    summary = summarize_citation_landscape(
        records, is_owned=lambda url: url.startswith("https://clinic.example")
    )

    assert summary["unit"] == "answer"
    assert summary["answers_total"] == 5
    assert summary["answers_with_sources"] == 4
    by_domain = {row["domain"]: row for row in summary["domains"]}
    assert by_domain["modoodoc.com"]["answers"] == 2
    assert by_domain["modoodoc.com"]["hospital_mentioned"] == 1
    assert by_domain["modoodoc.com"]["competitor_only"] == 1
    assert by_domain["clinic.example"]["owned"] is True
    assert by_domain["news.example"]["platforms"] == {"chatgpt": 1, "gemini": 1}
    assert by_domain["kdca.go.kr"]["competitor_only"] == 0
    assert [row["domain"] for row in summary["competitor_only_domains"]] == [
        "news.example",
        "modoodoc.com",
    ]
    assert format_domain_list(summary["competitor_only_domains"], key="competitor_only") == (
        "news.example(2), modoodoc.com(1)"
    )


def test_owned_domains_never_appear_as_off_site_candidates():
    records = [
        _record(["https://clinic.example/a"], mentioned=False, competitors=[("A", True)]),
    ]
    summary = summarize_citation_landscape(records, is_owned=lambda url: True)
    assert summary["competitor_only_domains"] == []
    assert summarize_citation_landscape([], top=5)["domains"] == []
