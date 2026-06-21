from types import SimpleNamespace

import httpx
import pytest

from app.services.asset_extractor import (
    FetchTarget,
    _assess_fetch_quality,
    _normalize_naver_blog_url,
    _scope_to_content_container,
    _validate_fetch_url,
    _validate_response_peer,
    detect_extractor_for,
    extract_docx_text,
    extract_pdf_text,
    naver_blog_id_from,
)


@pytest.mark.parametrize(
    "raw_url, expected",
    [
        # 데스크탑 blogId/logNo → 모바일 본문 form
        (
            "https://blog.naver.com/jangpyeonhan/223456789012",
            "https://m.blog.naver.com/jangpyeonhan/223456789012",
        ),
        # 끝 슬래시 허용
        (
            "https://blog.naver.com/jangpyeonhan/223456789012/",
            "https://m.blog.naver.com/jangpyeonhan/223456789012",
        ),
        # PostView.naver?blogId=&logNo= 쿼리 form
        (
            "https://blog.naver.com/PostView.naver?blogId=jangpyeonhan&logNo=223456789012",
            "https://m.blog.naver.com/jangpyeonhan/223456789012",
        ),
        # 쿼리 파라미터 순서가 달라도 동작
        (
            "https://blog.naver.com/PostView.naver?logNo=223456789012&blogId=jangpyeonhan&redirect=Dlog",
            "https://m.blog.naver.com/jangpyeonhan/223456789012",
        ),
        # bare blogId
        (
            "https://blog.naver.com/jangpyeonhan",
            "https://m.blog.naver.com/jangpyeonhan",
        ),
        # 이미 모바일 form인 데스크탑-아닌 호스트는 그대로
        (
            "https://m.blog.naver.com/jangpyeonhan/223456789012",
            "https://m.blog.naver.com/jangpyeonhan/223456789012",
        ),
        # 네이버가 아닌 호스트는 변형하지 않음
        (
            "https://example.com/jangpyeonhan/223456789012",
            "https://example.com/jangpyeonhan/223456789012",
        ),
    ],
)
def test_normalize_naver_blog_url(raw_url, expected):
    assert _normalize_naver_blog_url(raw_url) == expected


def test_assess_fetch_quality_flags_frameset_shell():
    # 네이버 데스크탑 응답의 전형 — mainFrame iframe만 있는 프레임셋 셸.
    shell_html = (
        '<html><frameset><frame id="mainFrame" '
        'src="/jangpyeonhan/PostView.naver?blogId=x&logNo=1"></frameset></html>'
    )
    shell_text = "[mainFrame](/jangpyeonhan/PostView.naver)"
    quality = _assess_fetch_quality(shell_html, shell_text)
    assert quality.has_shell_markers is True
    assert quality.looks_like_shell is True


def test_assess_fetch_quality_accepts_real_body():
    body_text = "원장 인사말입니다. " * 60  # 충분히 긴 본문
    quality = _assess_fetch_quality("<div>본문</div>", body_text)
    assert quality.looks_like_shell is False


def test_scope_to_content_container_extracts_se_main_container():
    html = (
        "<html><body>"
        "<nav>상단 네비</nav>"
        '<div class="se-main-container"><p>원장 인사말 본문</p></div>'
        "<footer>하단</footer>"
        "</body></html>"
    )
    scoped = _scope_to_content_container(html)
    assert "원장 인사말 본문" in scoped
    assert "상단 네비" not in scoped
    assert "하단" not in scoped


def test_scope_to_content_container_falls_back_to_full_html():
    html = "<html><body><article>컨테이너 없는 일반 페이지</article></body></html>"
    assert _scope_to_content_container(html) == html


def test_detect_extractor_for_image_by_mime():
    assert detect_extractor_for("image/jpeg", "doctor.jpg") == "IMAGE"
    assert detect_extractor_for("image/png", "exterior.PNG") == "IMAGE"


def test_detect_extractor_for_image_by_extension():
    assert detect_extractor_for(None, "interior.webp") == "IMAGE"
    assert detect_extractor_for("application/octet-stream", "room.JPG") == "IMAGE"


def test_detect_extractor_for_pdf():
    assert detect_extractor_for("application/pdf", "interview.pdf") == "PDF"
    assert detect_extractor_for(None, "report.PDF") == "PDF"


def test_detect_extractor_for_docx():
    docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert detect_extractor_for(docx_mime, "manuscript.docx") == "DOCX"
    assert detect_extractor_for(None, "manuscript.DOCX") == "DOCX"


def test_detect_extractor_for_unknown():
    assert detect_extractor_for("text/plain", "notes.txt") == "UNKNOWN"
    assert detect_extractor_for(None, "no_ext") == "UNKNOWN"


def test_extract_pdf_text_returns_empty_on_invalid_bytes():
    assert extract_pdf_text(b"not-a-pdf") == ""


def test_extract_docx_text_returns_empty_on_invalid_bytes():
    assert extract_docx_text(b"not-a-docx") == ""


def test_validate_fetch_url_blocks_private_dns_result(monkeypatch):
    monkeypatch.setattr(
        "app.services.asset_extractor.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("127.0.0.1", 80))],
    )

    assert _validate_fetch_url("https://example.com/source") == "사설망/로컬망 주소는 크롤링할 수 없습니다."


def test_validate_fetch_url_blocks_credentials():
    assert _validate_fetch_url("https://user:pass@example.com/source") == (
        "인증 정보를 포함한 URL은 허용하지 않습니다."
    )


def test_validate_fetch_url_blocks_invalid_port():
    assert _validate_fetch_url("https://example.com:99999/source") == "URL 포트가 올바르지 않습니다."


def test_validate_response_peer_blocks_dns_rebinding_to_private_ip():
    target = FetchTarget(
        url="https://example.com/source",
        hostname="example.com",
        port=443,
        allowed_ips=frozenset({"93.184.216.34"}),
    )
    response = httpx.Response(
        200,
        extensions={
            "network_stream": SimpleNamespace(get_extra_info=lambda _name: ("127.0.0.1", 443))
        },
    )

    assert _validate_response_peer(response, target) == (
        "DNS 변경 또는 사설망 연결이 감지되어 크롤링을 중단했습니다."
    )


def test_validate_response_peer_blocks_unpinned_public_ip():
    target = FetchTarget(
        url="https://example.com/source",
        hostname="example.com",
        port=443,
        allowed_ips=frozenset({"93.184.216.34"}),
    )
    response = httpx.Response(
        200,
        extensions={
            "network_stream": SimpleNamespace(get_extra_info=lambda _name: ("142.250.191.78", 443))
        },
    )

    assert _validate_response_peer(response, target) == (
        "DNS 변경 또는 사설망 연결이 감지되어 크롤링을 중단했습니다."
    )


@pytest.mark.parametrize(
    "value, expected",
    [
        ("https://blog.naver.com/jangpyeonhan/223456789012", "jangpyeonhan"),
        ("https://m.blog.naver.com/jangpyeonhan", "jangpyeonhan"),
        ("https://blog.naver.com/PostView.naver?blogId=jangpyeonhan&logNo=1", "jangpyeonhan"),
        ("blog.naver.com/jangpyeonhan/1", "jangpyeonhan"),
        ("jangpyeonhan", "jangpyeonhan"),
        # 네이버가 아닌 호스트 / 빈 값 / blogId 없음 → None
        ("https://example.com/jangpyeonhan", None),
        ("", None),
        ("https://blog.naver.com/", None),
    ],
)
def test_naver_blog_id_from(value, expected):
    assert naver_blog_id_from(value) == expected


_SAMPLE_RSS = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<rss version="2.0"><channel>'
    b"<link>https://blog.naver.com/jangpyeonhan</link>"  # 블로그 홈 — 글 아님, 제외
    b"<item><link>https://blog.naver.com/jangpyeonhan/100</link></item>"
    b"<item><link>https://blog.naver.com/jangpyeonhan/100</link></item>"  # 중복
    b"<item><link>https://blog.naver.com/jangpyeonhan/200</link></item>"
    b"<item><link>https://blog.naver.com/jangpyeonhan/300</link></item>"
    b"</channel></rss>"
)


def _patch_rss(monkeypatch, rss_bytes: bytes):
    from app.services import asset_extractor as ax

    target = ax.FetchTarget(
        url="https://rss.blog.naver.com/jangpyeonhan.xml",
        hostname="rss.blog.naver.com",
        port=443,
        allowed_ips=frozenset({"1.2.3.4"}),
    )
    monkeypatch.setattr(ax, "_validate_fetch_target", lambda _url: (target, None))
    monkeypatch.setattr(ax, "_validate_response_peer", lambda _resp, _tgt: None)

    class _Resp:
        content = rss_bytes

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return _Resp()

    monkeypatch.setattr(ax.httpx, "AsyncClient", _Client)
    return ax


@pytest.mark.asyncio
async def test_fetch_naver_blog_post_urls_parses_dedups_excludes_home(monkeypatch):
    ax = _patch_rss(monkeypatch, _SAMPLE_RSS)
    urls, error = await ax.fetch_naver_blog_post_urls("https://blog.naver.com/jangpyeonhan", max_posts=10)
    assert error is None
    # 홈 링크 제외 + /100 중복 제거, 문서 순서 유지
    assert urls == [
        "https://blog.naver.com/jangpyeonhan/100",
        "https://blog.naver.com/jangpyeonhan/200",
        "https://blog.naver.com/jangpyeonhan/300",
    ]


@pytest.mark.asyncio
async def test_fetch_naver_blog_post_urls_respects_max_posts(monkeypatch):
    ax = _patch_rss(monkeypatch, _SAMPLE_RSS)
    urls, error = await ax.fetch_naver_blog_post_urls("jangpyeonhan", max_posts=2)
    assert error is None
    assert len(urls) == 2


@pytest.mark.asyncio
async def test_fetch_naver_blog_post_urls_rejects_non_naver_ref(monkeypatch):
    from app.services import asset_extractor as ax

    urls, error = await ax.fetch_naver_blog_post_urls("https://example.com/blog", max_posts=5)
    assert urls == []
    assert error is not None


def test_validate_response_peer_reads_server_addr_when_peername_none():
    # 실제 httpx/httpcore 동작 재현: peername=None, server_addr=(ip,port).
    # 이 키를 읽지 못하면 모든 URL 크롤링이 막혔었다(회귀 방지).
    target = FetchTarget(
        url="https://example.com/source",
        hostname="example.com",
        port=443,
        allowed_ips=frozenset({"93.184.216.34"}),
    )
    extras = {"server_addr": ("93.184.216.34", 443), "peername": None}
    response = httpx.Response(
        200,
        extensions={"network_stream": SimpleNamespace(get_extra_info=lambda key: extras.get(key))},
    )
    # 허용 IP와 일치하는 server_addr → 통과(None)
    assert _validate_response_peer(response, target) is None


def test_validate_response_peer_blocks_rebinding_via_server_addr():
    target = FetchTarget(
        url="https://example.com/source",
        hostname="example.com",
        port=443,
        allowed_ips=frozenset({"93.184.216.34"}),
    )
    extras = {"server_addr": ("127.0.0.1", 443), "peername": None}
    response = httpx.Response(
        200,
        extensions={"network_stream": SimpleNamespace(get_extra_info=lambda key: extras.get(key))},
    )
    assert _validate_response_peer(response, target) == (
        "DNS 변경 또는 사설망 연결이 감지되어 크롤링을 중단했습니다."
    )


def test_validate_response_peer_falls_back_to_socket_getpeername():
    target = FetchTarget(
        url="https://example.com/source",
        hostname="example.com",
        port=443,
        allowed_ips=frozenset({"93.184.216.34"}),
    )
    sock = SimpleNamespace(getpeername=lambda: ("93.184.216.34", 443))
    extras = {"server_addr": None, "peername": None, "socket": sock}
    response = httpx.Response(
        200,
        extensions={"network_stream": SimpleNamespace(get_extra_info=lambda key: extras.get(key))},
    )
    assert _validate_response_peer(response, target) is None
