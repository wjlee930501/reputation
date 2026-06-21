"""자산 텍스트 추출.

- PDF: pypdf
- DOCX: python-docx
- HTML (URL fetch): httpx + html2text

각 추출은 raw_text 채우는 용도. 추출 실패는 ValueError 던지지 않고 빈 문자열 반환 후
호출자가 운영자 메시지로 안내한다.

네이버 블로그 데스크탑 URL은 iframe frameset 셸만 돌려주므로 본문이 비어 있다.
모바일/PostView 형태(`m.blog.naver.com/{blogId}/{logNo}`)는 서버 렌더된 본문을
직접 돌려주므로, fetch 전에 네이버 호스트를 모바일 form으로 정규화한다.
"""
from __future__ import annotations

from dataclasses import dataclass
import asyncio
import io
import ipaddress
import logging
import re
import socket
from urllib.parse import parse_qs, urljoin, urlparse

import html2text
import httpx

logger = logging.getLogger(__name__)

DEFAULT_FETCH_TIMEOUT = 12.0
MAX_HTML_BYTES = 4 * 1024 * 1024  # 4MB
MAX_RAW_TEXT_LENGTH = 60_000
MAX_REDIRECTS = 4

# fetch 품질 게이트 — 셸/프레임셋만 받아오면 본문이 비었으므로 거부 판단에 쓴다.
MIN_FETCH_TEXT_LENGTH = 200
SHELL_MARKER_PATTERN = re.compile(r"mainFrame|frameset|PostView\.naver|//내용", re.IGNORECASE)

# 네이버 블로그 호스트 — 데스크탑/PostView/축약 form 모두 모바일 본문 URL로 정규화한다.
_NAVER_BLOG_HOSTS = frozenset({"blog.naver.com", "m.blog.naver.com"})
_NAVER_ID_LOG_RE = re.compile(r"^/([A-Za-z0-9_-]+)/(\d+)/?$")
_NAVER_ID_ONLY_RE = re.compile(r"^/([A-Za-z0-9_-]+)/?$")
# 네이버 SmartEditor ONE / 레거시 본문 컨테이너 — nav/chrome 제거용.
# cssselect 패키지가 없으므로 lxml 내장 XPath로 매칭한다 (SmartEditor ONE → 레거시 순).
_NAVER_CONTENT_XPATHS = (
    "//div[contains(concat(' ', normalize-space(@class), ' '), ' se-main-container ')]",
    "//*[@id='postViewArea']",
    "//div[contains(concat(' ', normalize-space(@class), ' '), ' post_ct ')]",
)


@dataclass(frozen=True)
class FetchQuality:
    char_count: int
    has_shell_markers: bool
    link_to_text_ratio: float

    @property
    def looks_like_shell(self) -> bool:
        """본문이 아니라 빈 셸/프레임셋으로 보이는지."""
        if self.char_count < MIN_FETCH_TEXT_LENGTH:
            return True
        if self.has_shell_markers and self.char_count < 2_000:
            return True
        return False


@dataclass(frozen=True)
class FetchTarget:
    url: str
    hostname: str
    port: int
    allowed_ips: frozenset[str]


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


async def fetch_url_text(url: str) -> tuple[str, str | None, FetchQuality | None]:
    """URL을 가져와 마크다운 형태 텍스트 + 추출 실패 사유 + 품질 신호를 반환.

    실패 시 (빈 문자열, 사유, None) 반환. 성공 시 (text, None, quality).
    네이버 블로그 호스트는 fetch 전에 모바일 본문 URL로 정규화한다.
    """
    # 네이버 데스크탑 URL은 프레임셋 셸만 돌려주므로, SSRF 검증 전에 모바일 form으로 정규화.
    normalized_url = _normalize_naver_blog_url(url)
    # getaddrinfo는 블로킹 DNS 호출 — async 요청 핸들러에서 이벤트 루프를 멈추지 않도록
    # 워커 스레드에서 실행 (리다이렉트 검증 포함 최대 1+MAX_REDIRECTS회 호출됨).
    target, validation_error = await asyncio.to_thread(_validate_fetch_target, normalized_url)
    if validation_error or target is None:
        return "", validation_error, None
    try:
        current_target = target
        async with httpx.AsyncClient(timeout=DEFAULT_FETCH_TIMEOUT, follow_redirects=False) as client:
            response: httpx.Response | None = None
            for _ in range(MAX_REDIRECTS + 1):
                response = await client.get(current_target.url, headers=_browser_headers())
                peer_error = _validate_response_peer(response, current_target)
                if peer_error:
                    return "", peer_error, None
                if response.status_code not in {301, 302, 303, 307, 308}:
                    break
                location = response.headers.get("location")
                if not location:
                    break
                next_url = urljoin(current_target.url, location)
                next_target, validation_error = await asyncio.to_thread(
                    _validate_fetch_target, next_url
                )
                if validation_error or next_target is None:
                    return "", f"리다이렉트 대상 차단: {validation_error}", None
                current_target = next_target
            if response is None:
                return "", "URL 접근 실패.", None
            if response.status_code in {301, 302, 303, 307, 308}:
                return "", "리다이렉트가 너무 많습니다.", None
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "html" not in content_type and "text" not in content_type:
                return "", f"HTML이 아닌 콘텐츠({content_type})는 자동 추출 불가.", None
            content = response.content[:MAX_HTML_BYTES]
            html = content.decode(response.encoding or "utf-8", errors="ignore")
            scoped_html = _scope_to_content_container(html)
            text = _html_to_markdown(scoped_html)
            quality = _assess_fetch_quality(html, text)
            return text[:MAX_RAW_TEXT_LENGTH], None, quality
    except httpx.HTTPStatusError as exc:
        return "", f"HTTP {exc.response.status_code} — URL 접근 실패.", None
    except Exception as exc:
        logger.warning("URL fetch failed for %s: %s", url, exc)
        return "", f"URL 접근 중 오류 — {exc}", None


def _normalize_naver_blog_url(url: str) -> str:
    """네이버 블로그 URL을 모바일 본문(PostView) form으로 정규화한다.

    데스크탑 `blog.naver.com/{blogId}/{logNo}` 와 PostView 쿼리 form은 모두
    프레임셋 셸을 돌려주므로, 본문을 서버 렌더하는 `m.blog.naver.com` form으로 바꾼다.
    네이버가 아닌 호스트는 그대로 둔다.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in _NAVER_BLOG_HOSTS:
        return url

    path = parsed.path or "/"
    # PostView.naver?blogId=X&logNo=Y (그리고 ?blogId=&logNo= 쿼리 form)
    if "PostView" in path:
        params = parse_qs(parsed.query)
        blog_id = (params.get("blogId") or [""])[0]
        log_no = (params.get("logNo") or [""])[0]
        if blog_id and log_no:
            return f"https://m.blog.naver.com/{blog_id}/{log_no}"
        return url

    # blog.naver.com/{blogId}/{logNo}
    match = _NAVER_ID_LOG_RE.match(path)
    if match:
        blog_id, log_no = match.group(1), match.group(2)
        return f"https://m.blog.naver.com/{blog_id}/{log_no}"

    # bare blog.naver.com/{blogId}
    match = _NAVER_ID_ONLY_RE.match(path)
    if match and match.group(1).lower() not in {"postview.naver"}:
        return f"https://m.blog.naver.com/{match.group(1)}"

    # 이미 m.blog.naver.com 형태이거나 인식하지 못한 path — 그대로 둔다.
    if host == "blog.naver.com":
        return url.replace("://blog.naver.com", "://m.blog.naver.com", 1)
    return url


def _scope_to_content_container(html: str) -> str:
    """네이버 본문 컨테이너가 있으면 그 안으로 추출 범위를 좁힌다.

    SmartEditor ONE(`div.se-main-container`) 또는 레거시(`#postViewArea`/`div.post_ct`)
    컨테이너를 lxml로 찾아 nav/chrome을 떨어뜨린다. 컨테이너가 없으면 전체 HTML 반환.
    """
    try:
        from lxml import html as lxml_html  # noqa: WPS433
    except ImportError:
        return html
    if not html or not html.strip():
        return html
    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return html
    for xpath in _NAVER_CONTENT_XPATHS:
        nodes = tree.xpath(xpath)
        if nodes:
            return lxml_html.tostring(nodes[0], encoding="unicode")
    return html


def _assess_fetch_quality(raw_html: str, text: str) -> FetchQuality:
    """fetch 결과가 본문인지 빈 셸인지 판단할 단순 신호를 계산한다."""
    char_count = len(text.strip())
    has_shell_markers = bool(SHELL_MARKER_PATTERN.search(raw_html))
    link_count = text.count("](")  # 마크다운 링크 개수 근사
    word_count = max(len(text.split()), 1)
    link_to_text_ratio = link_count / word_count
    return FetchQuality(
        char_count=char_count,
        has_shell_markers=has_shell_markers,
        link_to_text_ratio=link_to_text_ratio,
    )


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
    _, error = _validate_fetch_target(url)
    return error


def _validate_fetch_target(url: str) -> tuple[FetchTarget | None, str | None]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None, "지원하지 않는 URL scheme입니다 (http/https만 허용)."
    if not parsed.hostname:
        return None, "호스트가 없는 URL입니다."
    if parsed.username or parsed.password:
        return None, "인증 정보를 포함한 URL은 허용하지 않습니다."
    hostname = parsed.hostname.rstrip(".")
    if not hostname:
        return None, "호스트가 없는 URL입니다."
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return None, "호스트 DNS 조회에 실패했습니다."
    except ValueError:
        return None, "URL 포트가 올바르지 않습니다."
    allowed_ips: set[str] = set()
    for result in addresses:
        ip = ipaddress.ip_address(result[4][0])
        if _is_blocked_ip(ip):
            return None, "사설망/로컬망 주소는 크롤링할 수 없습니다."
        allowed_ips.add(str(ip))
    if not allowed_ips:
        return None, "호스트 DNS 조회에 실패했습니다."
    return FetchTarget(url=url, hostname=hostname, port=port, allowed_ips=frozenset(allowed_ips)), None


def _validate_response_peer(response: httpx.Response, target: FetchTarget) -> str | None:
    stream = response.extensions.get("network_stream")
    if stream is None:
        return "URL 연결 정보를 확인할 수 없어 크롤링을 중단했습니다."
    try:
        peername = stream.get_extra_info("peername")
    except Exception:
        return "URL 연결 정보를 확인할 수 없어 크롤링을 중단했습니다."
    if not peername:
        return "URL 연결 정보를 확인할 수 없어 크롤링을 중단했습니다."
    try:
        peer_ip = ipaddress.ip_address(peername[0])
    except (IndexError, TypeError, ValueError):
        return "URL 연결 정보를 확인할 수 없어 크롤링을 중단했습니다."
    if _is_blocked_ip(peer_ip) or str(peer_ip) not in target.allowed_ips:
        return "DNS 변경 또는 사설망 연결이 감지되어 크롤링을 중단했습니다."
    return None


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not ip.is_global or any([ip.is_loopback, ip.is_link_local, ip.is_multicast, ip.is_unspecified])


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
