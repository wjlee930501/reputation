from app.services.asset_extractor import detect_extractor_for, extract_docx_text, extract_pdf_text


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
