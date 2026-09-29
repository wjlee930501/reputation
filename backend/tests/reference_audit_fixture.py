"""2026-09-29 참고자료 전수 점검 fixture를 실제 검증 파이프라인에 태우는 공용 도구.

fixture는 공개 참고자료 365건 중 판정이 확정된 111행(정상 48·빈 페이지 37·주제 불일치 22·
죽은 링크 4)과 그 URL의 실제 HTML(스크립트·스타일·주석만 제거, gzip)이다. 사람 확인
필요 254행은 판정 대상이 아니다. 네트워크는 쓰지 않는다 — 가짜 fetcher가 fixture를 돌려준다.

같은 디렉터리의 2차 점검(김실장, 같은 날 실제 GET) fixture:
- `exclusions.json` — 제외 목록(`REFERENCE_URL_EXCLUSIONS`) 5개 주소의 실제 응답.
- `catalog_seed.json` — 수기 목록에 더한 15개 문서의 실제 응답.
"""

from __future__ import annotations

import gzip
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.reference_verification import (
    FetchResult,
    ReferenceVerifier,
    article_topic_terms,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "reference_audit_20260929"
EXPECT_PASS = frozenset({"정상"})
EXPECT_REMOVED = frozenset({"빈 페이지", "주제 불일치", "죽은 링크"})


@lru_cache(maxsize=1)
def load_manifest() -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def load_review_rows(name: str) -> tuple[dict[str, Any], ...]:
    """2차 점검 fixture(`exclusions`·`catalog_seed`)의 행."""

    return tuple(json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))["rows"])


@lru_cache(maxsize=None)
def fixture_html(relative: str) -> str:
    return gzip.decompress((FIXTURE_DIR / relative).read_bytes()).decode("utf-8")


class AuditFixtureFetcher:
    """fixture의 상태·최종 URL·HTML을 그대로 돌려주는 가짜 fetcher."""

    def __init__(self, rows: Any = None) -> None:
        rows = load_manifest()["rows"] if rows is None else rows
        self.by_url = {row["url"]: row for row in rows}
        self.calls: list[str] = []

    async def __call__(self, url: str) -> FetchResult:
        self.calls.append(url)
        row = self.by_url[url]
        return FetchResult(
            url=url,
            status=row["status"],
            final_url=row["final_url"],
            html=fixture_html(row["html"]),
        )


def audit_row_topic(row: dict[str, Any]) -> list[str]:
    """점검 데이터에 남은 글 주제: 제목과 진료 항목(측정 질의의 treatment)."""

    return article_topic_terms(title=row["article_title"], extra=[row.get("treatment")])


async def verify_audit_row(row: dict[str, Any]) -> dict[str, Any]:
    """한 행 = 한 글의 참고자료 하나. 라벨은 공개됐던 그대로 넘긴다(판정에 쓰이면 안 된다)."""

    verifier = ReferenceVerifier(AuditFixtureFetcher(), domain_spacing=0)
    outcome = await verifier.verify(
        [{"title": row["ref_label"], "url": row["url"]}],
        topic_terms=audit_row_topic(row),
    )
    assert len(outcome.checks) == 1
    return outcome.checks[0]


async def verify_review_row(row: dict[str, Any], topic_terms: list[str]) -> tuple[dict[str, Any], list[str]]:
    """2차 점검 fixture 한 행을 실제 검증 파이프라인에 태운다 → (판정 기록, GET한 주소)."""

    fetcher = AuditFixtureFetcher([row])
    verifier = ReferenceVerifier(fetcher, domain_spacing=0)
    outcome = await verifier.verify([{"title": "", "url": row["url"]}], topic_terms=topic_terms)
    assert len(outcome.checks) == 1
    return outcome.checks[0], list(fetcher.calls)


def review_row_topic(row: dict[str, Any]) -> list[str]:
    return article_topic_terms(title=row["article_title"], extra=[row.get("topic")])
