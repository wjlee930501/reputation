"""참고자료 검증용 가짜 fetcher — 실제 네트워크 없이 문서 제목·본문을 돌려준다."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from app.services.reference_verification import FetchResult


def page_html(title: str, body: str) -> str:
    return (
        f"<html><head><title>{title}</title><script>var x = 1;</script></head>"
        f"<body><nav>메뉴</nav><main><h1>{title}</h1><p>{body}</p></main></body></html>"
    )


def document_body(topic: str) -> str:
    """빈 템플릿 판정(본문 200자 미만)에 걸리지 않을 만큼의 실제 같은 본문."""

    return (
        f"{topic}은(는) 여러 원인으로 생길 수 있습니다. {topic}의 증상과 진단, 치료 방법을 "
        "안내합니다. 증상이 지속되면 의료진과 상담하세요. " * 4
    )


class PageFetcher:
    """url → (status, final_url, html) 또는 예외. 호출과 동시 실행 수를 기록한다."""

    def __init__(
        self,
        pages: Mapping[str, tuple[int, str | None, str] | BaseException] | None = None,
        *,
        delay: float = 0.0,
    ) -> None:
        self.pages = dict(pages or {})
        self.delay = delay
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0

    def add_document(self, url: str, title: str, *, topic: str | None = None) -> None:
        self.pages[url] = (200, url, page_html(title, document_body(topic or title)))

    async def __call__(self, url: str) -> FetchResult:
        self.calls.append(url)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            page = self.pages.get(url)
            if page is None:
                return FetchResult(url=url, status=404, final_url=url, html="")
            if isinstance(page, BaseException):
                raise page
            status, final_url, html = page
            return FetchResult(url=url, status=status, final_url=final_url or url, html=html)
        finally:
            self.active -= 1
