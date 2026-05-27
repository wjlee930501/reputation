from types import SimpleNamespace

import httpx

from app.services.asset_extractor import (
    FetchTarget,
    _validate_fetch_url,
    _validate_response_peer,
    detect_extractor_for,
    extract_docx_text,
    extract_pdf_text,
)


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
