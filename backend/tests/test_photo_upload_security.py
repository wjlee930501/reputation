"""Security controls for operator-uploaded public hospital photos."""

from io import BytesIO

import pytest
from PIL import Image

from app.services.photo_upload import InvalidPhotoUpload, normalize_photo_upload


def _image_bytes(
    image_format: str,
    *,
    size: tuple[int, int] = (32, 24),
    frames: int = 1,
) -> bytes:
    # Given: a real raster encoded by Pillow.
    images = [Image.new("RGB", size, (index * 20, 100, 180)) for index in range(frames)]
    output = BytesIO()
    images[0].save(
        output,
        format=image_format,
        save_all=frames > 1,
        append_images=images[1:],
    )
    return output.getvalue()


@pytest.mark.parametrize("image_format", ["JPEG", "PNG", "WEBP"])
def test_normalize_photo_upload_preserves_legitimate_rasters_when_claims_lie(
    image_format: str,
) -> None:
    # Given: a legitimate supported image with an attacker-controlled filename/MIME claim.
    payload = _image_bytes(image_format)

    # When: the server decodes and normalizes the payload.
    normalized = normalize_photo_upload(payload)

    # Then: storage receives a bounded, metadata-free WebP derived from decoded pixels.
    assert normalized.mime_type == "image/webp"
    assert normalized.filename == "image.webp"
    assert len(normalized.data) <= 12 * 1024 * 1024
    with Image.open(BytesIO(normalized.data)) as decoded:
        assert decoded.format == "WEBP"
        assert decoded.size == (32, 24)
        assert decoded.getexif() == {}
        assert decoded.getpixel((0, 0)) != (0, 0, 0)


def test_normalize_photo_upload_rejects_non_image_bytes_with_image_claims() -> None:
    # Given: non-image bytes presented as an image.
    payload = b"<script>alert(1)</script>"

    # When / Then: decoding fails before the storage boundary.
    with pytest.raises(InvalidPhotoUpload, match="decode"):
        normalize_photo_upload(payload)


def test_normalize_photo_upload_rejects_animated_webp() -> None:
    # Given: a supported container carrying multiple frames.
    payload = _image_bytes("WEBP", frames=2)

    # When / Then: animation is rejected rather than silently reinterpreted.
    with pytest.raises(InvalidPhotoUpload, match="animated"):
        normalize_photo_upload(payload)


def test_normalize_photo_upload_rejects_excess_dimensions(monkeypatch) -> None:
    # Given: a valid image beyond the configured dimension boundary.
    payload = _image_bytes("PNG", size=(33, 2))
    monkeypatch.setattr("app.services.photo_upload.MAX_IMAGE_DIMENSION", 32)

    # When / Then: decoded dimensions reject the payload.
    with pytest.raises(InvalidPhotoUpload, match="dimensions"):
        normalize_photo_upload(payload)


def test_normalize_photo_upload_rejects_excess_pixels(monkeypatch) -> None:
    # Given: individually acceptable dimensions whose product is too large.
    payload = _image_bytes("PNG", size=(20, 20))
    monkeypatch.setattr("app.services.photo_upload.MAX_IMAGE_PIXELS", 399)

    # When / Then: the pixel budget rejects the payload.
    with pytest.raises(InvalidPhotoUpload, match="pixels"):
        normalize_photo_upload(payload)


def test_normalize_photo_upload_applies_orientation_and_strips_exif() -> None:
    # Given: a camera JPEG whose stored pixels require a 90-degree EXIF rotation.
    output = BytesIO()
    image = Image.new("RGB", (12, 8), (40, 100, 160))
    exif = image.getexif()
    exif[274] = 6
    image.save(output, format="JPEG", exif=exif)

    # When: the server normalizes the raster.
    normalized = normalize_photo_upload(output.getvalue())

    # Then: pixel orientation is canonical and no EXIF survives storage.
    with Image.open(BytesIO(normalized.data)) as decoded:
        assert decoded.size == (8, 12)
        assert decoded.getexif() == {}


def test_normalize_photo_upload_rejects_post_encode_payload_over_ceiling(monkeypatch) -> None:
    # Given: a valid raster but an intentionally tiny canonical-output budget.
    payload = _image_bytes("PNG", size=(32, 24))
    monkeypatch.setattr("app.services.photo_upload.MAX_ENCODED_BYTES", 1)

    # When / Then: storage never receives an unexpectedly inflated canonical image.
    with pytest.raises(InvalidPhotoUpload, match="encoded size"):
        normalize_photo_upload(payload)


def test_normalize_brand_graphic_can_use_lossless_webp() -> None:
    # Given: a flat-color official graphic where exact pixels matter.
    payload = _image_bytes("PNG", size=(16, 16))

    # When: the brand-specific lossless mode is requested.
    normalized = normalize_photo_upload(payload, lossless=True)

    # Then: decoded WebP pixels exactly match the source graphic.
    with Image.open(BytesIO(normalized.data)) as decoded:
        assert decoded.format == "WEBP"
        assert decoded.getpixel((0, 0)) == (0, 100, 180)


def test_normalize_photo_upload_preserves_alpha_channel() -> None:
    # Given: a transparent PNG used for a cutout or brand graphic.
    output = BytesIO()
    Image.new("RGBA", (8, 8), (10, 20, 30, 64)).save(output, format="PNG")

    # When: it is normalized to canonical WebP.
    normalized = normalize_photo_upload(output.getvalue(), lossless=True)

    # Then: transparency survives decoding and re-encoding.
    with Image.open(BytesIO(normalized.data)) as decoded:
        assert decoded.mode == "RGBA"
        assert decoded.getpixel((0, 0))[3] == 64


def test_generated_cover_normalization_center_crops_to_requested_aspect_ratio() -> None:
    payload = _image_bytes("PNG", size=(30, 20))

    normalized = normalize_photo_upload(payload, lossless=True, target_aspect_ratio=(16, 9))

    with Image.open(BytesIO(normalized.data)) as decoded:
        assert decoded.size == (30, 16)
