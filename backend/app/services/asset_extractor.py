"""자산 텍스트 추출.

- PDF: pypdf
- DOCX: python-docx
- HTML (URL fetch): httpx + html2text

각 추출은 raw_text 채우는 용도. 추출 실패는 ValueError 던지지 않고 빈 문자열 반환 후
호출자가 운영자 메시지로 안내한다.
"""
from __future__ import annotations

import io
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse

import html2text
import httpx

logger = logging.getLogger(__name__)

DEFAULT_FETCH_TIMEOUT = 12.0
MAX_HTML_BYTES = 4 * 1024 * 1024  # 4MB
MAX_RAW_TEXT_LENGTH = 60_000
MAX_REDIRECTS = 4


def extract_pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader  # noqa: WPS433
    except ImportError:
        logger.warning("pypdf not installed; PDF extraction disabled")
        return ""
    try:
        reader = PdfReader(io.BytesIO(data))
        chunks: list[str] = []
        for page in reader.pages:
            try:
                chunks.append(page.extract_text() or "")
            except Exception as page_exc:
                logger.warning("pdf page extract failed: %s", page_exc)
        text = "\n\n".join(chunks).strip()
        return text[:MAX_RAW_TEXT_LENGTH]
    except Exception as exc:
        logger.warning("pdf extract failed: %s", exc)
        return ""


def extract_docx_text(data: bytes) -> str:
    try:
        from docx import Document  # python-docx 패키지명: docx
    except ImportError:
        logger.warning("python-docx not installed; DOCX extraction disabled")
        return ""
    try:
        doc = Document(io.BytesIO(data))
        chunks = [para.text for para in doc.paragraphs if para.text and para.text.strip()]
        text = "\n\n".join(chunks).strip()
        return text[:MAX_RAW_TEXT_LENGTH]
    except Exception as exc:
        logger.warning("docx extract failed: %s", exc)
        return ""


async def fetch_url_text(url: str) -> tuple[str, str | None]:
    """URL을 가져와 마크다운 형태 텍스트 + 추출 실패 사유를 반환.

    실패 시 (빈 문자열, 사유) 반환. 성공 시 (text, None).
    """
    validation_error = _validate_fetch_url(url)
    if validation_error:
        return "", validation_error
    try:
        current_url = url
        async with httpx.AsyncClient(timeout=DEFAULT_FETCH_TIMEOUT, follow_redirects=False) as client:
            response: httpx.Response | None = None
            for _ in range(MAX_REDIRECTS + 1):
                response = await client.get(current_url, headers=_browser_headers())
                if response.status_code not in {301, 302, 303, 307, 308}:
                    break
                location = response.headers.get("location")
                if not location:
                    break
                next_url = urljoin(current_url, location)
                validation_error = _validate_fetch_url(next_url)
                if validation_error:
                    return "", f"리다이렉트 대상 차단: {validation_error}"
                current_url = next_url
            if response is None:
                return "", "URL 접근 실패."
            if response.status_code in {301, 302, 303, 307, 308}:
                return "", "리다이렉트가 너무 많습니다."
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "html" not in content_type and "text" not in content_type:
                return "", f"HTML이 아닌 콘텐츠({content_type})는 자동 추출 불가."
            content = response.content[:MAX_HTML_BYTES]
            text = _html_to_markdown(content.decode(response.encoding or "utf-8", errors="ignore"))
            return text[:MAX_RAW_TEXT_LENGTH], None
    except httpx.HTTPStatusError as exc:
        return "", f"HTTP {exc.response.status_code} — URL 접근 실패."
    except Exception as exc:
        logger.warning("URL fetch failed for %s: %s", url, exc)
        return "", f"URL 접근 중 오류 — {exc}"


def _html_to_markdown(html: str) -> str:
    converter = html2text.HTML2Text()
    converter.ignore_images = True
    converter.ignore_links = False
    converter.body_width = 0  # 줄바꿈 비활성
    return converter.handle(html).strip()


def _browser_headers() -> dict[str, str]:
    return {
        # 일반 데스크탑 브라우저 UA — 일부 사이트가 봇 차단함
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
    }


def _validate_fetch_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return "지원하지 않는 URL scheme입니다 (http/https만 허용)."
    if not parsed.hostname:
        return "호스트가 없는 URL입니다."
    if parsed.username or parsed.password:
        return "인증 정보를 포함한 URL은 허용하지 않습니다."
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror:
        return "호스트 DNS 조회에 실패했습니다."
    for result in addresses:
        ip = ipaddress.ip_address(result[4][0])
        if _is_blocked_ip(ip):
            return "사설망/로컬망 주소는 크롤링할 수 없습니다."
    return None


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any([
        ip.is_private,
        ip.is_loopback,
        ip.is_link_local,
        ip.is_multicast,
        ip.is_reserved,
        ip.is_unspecified,
    ])


def detect_extractor_for(mime_type: str | None, filename: str) -> str:
    """확장자/mime로 추출기 종류 판별. PHOTO 카테고리는 'IMAGE' 반환 — 추출 안 함."""
    name = (filename or "").lower()
    mt = (mime_type or "").lower()
    if mt.startswith("image/") or any(name.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
        return "IMAGE"
    if mt == "application/pdf" or name.endswith(".pdf"):
        return "PDF"
    if (
        mt == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        or name.endswith(".docx")
    ):
        return "DOCX"
    return "UNKNOWN"
