"""AI 답변이 근거로 읽은 출처 도메인의 지형.

왜 필요한가. 측정은 답변마다 인용 URL을 이미 저장한다(`SovRecord.source_urls`). 지금까지는
그 URL을 "병원 소유 / 그 밖" 둘로만 나눴다. 사이트 밖에서 무엇을 보강할지는 그 밖이 어디인지에
달려 있다 — 경쟁 병원이 언급된 답변에서 AI가 읽은 도메인(디렉터리·언론·학회·공공기관 등)이
다음 작업의 후보다. 2026-11 점검에서 네이버 블로그는 OAI-SearchBot과 Google-Extended를 막고
있었으므로, ChatGPT·Gemini가 실제로 무엇을 읽는지는 측정 기록으로 봐야 한다.

집계 단위는 **답변**이다(URL 출현 수가 아니다). 한 답변이 같은 도메인을 세 번 인용해도 1이다.
월간 보고서의 출처 집계와 같은 단위다. 이 함수는 관측 집계일 뿐 인과를 말하지 않는다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from app.services.content_citations import normalize_cited_url

DEFAULT_TOP_DOMAINS = 10

# 같은 서비스의 모바일·데스크톱 호스트를 한 도메인으로 센다. 서비스가 다른 하위 도메인
# (blog.naver.com과 cafe.naver.com)은 성격이 달라 그대로 둔다.
_MOBILE_PREFIXES = ("m.",)


def citation_domain(url: Any) -> str | None:
    """인용 URL → 집계용 도메인. `www.`·`m.`을 떼고 소문자로 만든다. 못 읽으면 None."""
    normalized = normalize_cited_url(url)
    if normalized is None:
        return None
    host = normalized.host
    if any(ch.isspace() for ch in host) or "." not in host:
        return None
    for prefix in _MOBILE_PREFIXES:
        if host.startswith(prefix) and host.count(".") >= 2:
            host = host[len(prefix) :]
    return host or None


def _record_urls(record: Any) -> list[str]:
    return [
        value.strip()
        for value in (getattr(record, "source_urls", None) or [])
        if isinstance(value, str) and value.strip()
    ]


def _competitor_mentioned(record: Any) -> bool:
    for competitor in getattr(record, "competitor_mentions", None) or []:
        if isinstance(competitor, dict) and competitor.get("is_mentioned") is True:
            return True
    return False


def _is_success(record: Any) -> bool:
    status = getattr(record, "measurement_status", None)
    return status in (None, "SUCCESS")


def summarize_citation_landscape(
    records: Iterable[Any],
    *,
    is_owned: Callable[[str], bool] | None = None,
    top: int = DEFAULT_TOP_DOMAINS,
) -> dict[str, Any]:
    """성공한 측정 답변의 인용 도메인을 답변 단위로 센다.

    반환 구조:
      answers_total / answers_with_sources — 성공 답변 수 / 출처가 하나라도 있는 답변 수
      domains — 도메인별 {domain, answers, hospital_mentioned, competitor_only, owned, platforms}
        hospital_mentioned: 병원이 언급된 답변 중 이 도메인을 인용한 수
        competitor_only: 병원은 없고 경쟁 병원이 언급된 답변 중 이 도메인을 인용한 수
      competitor_only_domains — competitor_only가 큰 순서의 소유하지 않은 도메인(사이트 밖 후보)
    """
    owned_check = is_owned or (lambda _url: False)
    answers_total = 0
    answers_with_sources = 0
    per_domain: dict[str, dict[str, Any]] = {}
    platforms: dict[str, dict[str, int]] = defaultdict(dict)

    for record in records:
        if not _is_success(record):
            continue
        answers_total += 1
        urls = _record_urls(record)
        if not urls:
            continue
        answers_with_sources += 1
        mentioned = getattr(record, "is_mentioned", None) is True
        competitor_only = (getattr(record, "is_mentioned", None) is False) and _competitor_mentioned(
            record
        )
        platform = str(getattr(record, "ai_platform", "") or "unknown")
        seen: dict[str, bool] = {}
        for url in urls:
            domain = citation_domain(url)
            if domain is None:
                continue
            seen[domain] = seen.get(domain, False) or bool(owned_check(url))
        for domain, owned in seen.items():
            row = per_domain.setdefault(
                domain,
                {
                    "domain": domain,
                    "answers": 0,
                    "hospital_mentioned": 0,
                    "competitor_only": 0,
                    "owned": False,
                },
            )
            row["answers"] += 1
            row["owned"] = row["owned"] or owned
            if mentioned:
                row["hospital_mentioned"] += 1
            if competitor_only:
                row["competitor_only"] += 1
            platforms[domain][platform] = platforms[domain].get(platform, 0) + 1

    def _with_platforms(row: dict[str, Any]) -> dict[str, Any]:
        return {**row, "platforms": dict(sorted(platforms[row["domain"]].items()))}

    ranked = sorted(per_domain.values(), key=lambda row: (-row["answers"], row["domain"]))
    competitor_ranked = sorted(
        (row for row in per_domain.values() if row["competitor_only"] > 0 and not row["owned"]),
        key=lambda row: (-row["competitor_only"], -row["answers"], row["domain"]),
    )
    limit = max(int(top), 0)
    return {
        "unit": "answer",
        "answers_total": answers_total,
        "answers_with_sources": answers_with_sources,
        "domains": [_with_platforms(row) for row in ranked[:limit]],
        "competitor_only_domains": [_with_platforms(row) for row in competitor_ranked[:limit]],
    }


def format_domain_list(rows: Sequence[dict[str, Any]], *, key: str, limit: int = 3) -> str:
    """운영자 문구용 짧은 목록: `modoodoc.com(4), news.example(2)`."""
    parts = [f"{row['domain']}({row[key]})" for row in rows[:limit] if row.get(key)]
    return ", ".join(parts)
