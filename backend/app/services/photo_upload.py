"""Decode untrusted photo uploads into one bounded canonical raster format."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from io import BytesIO
from typing import Final

from PIL import Image, ImageOps

MAX_IMAGE_PIXELS: Final = 25_000_000
MAX_IMAGE_DIMENSION: Final = 8_192
MAX_ENCODED_BYTES: Final = 12 * 1024 * 1024
SUPPORTED_IMAGE_FORMATS: Final = frozenset({"JPEG", "PNG", "WEBP"})


@dataclass(frozen=True, slots=True)
class NormalizedPhoto:
    data: bytes
    filename: str = "image.webp"
    mime_type: str = "image/webp"


@dataclass(frozen=True, slots=True)
class InvalidPhotoUpload(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


def _center_crop_aspect(image: Image.Image, ratio: tuple[int, int]) -> Image.Image:
    ratio_width, ratio_height = ratio
    if ratio_width <= 0 or ratio_height <= 0:
        raise ValueError("target aspect ratio must be positive")
    width, height = image.size
    if width * ratio_height > height * ratio_width:
        crop_width = max(1, height * ratio_width // ratio_height)
        left = (width - crop_width) // 2
        return image.crop((left, 0, left + crop_width, height))
    if width * ratio_height < height * ratio_width:
        crop_height = max(1, width * ratio_height // ratio_width)
        top = (height - crop_height) // 2
        return image.crop((0, top, width, top + crop_height))
    return image


def normalize_photo_upload(
    data: bytes,
    *,
    lossless: bool = False,
    target_aspect_ratio: tuple[int, int] | None = None,
) -> NormalizedPhoto:
    """Fully decode, orient, and re-encode a supported single-frame image."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as uploaded:
                if uploaded.format not in SUPPORTED_IMAGE_FORMATS:
                    raise InvalidPhotoUpload("unsupported image format")
                width, height = uploaded.size
                if width <= 0 or height <= 0 or max(width, height) > MAX_IMAGE_DIMENSION:
                    raise InvalidPhotoUpload("image dimensions exceed the safe limit")
                if width * height > MAX_IMAGE_PIXELS:
                    raise InvalidPhotoUpload("image pixels exceed the safe limit")
                if getattr(uploaded, "is_animated", False) or getattr(uploaded, "n_frames", 1) != 1:
                    raise InvalidPhotoUpload("animated images are not supported")
                uploaded.load()
                oriented = ImageOps.exif_transpose(uploaded)
                has_alpha = "A" in oriented.getbands()
                canonical = oriented.convert("RGBA" if has_alpha else "RGB")
                if target_aspect_ratio is not None:
                    canonical = _center_crop_aspect(canonical, target_aspect_ratio)
                output = BytesIO()
                canonical.save(
                    output,
                    format="WEBP",
                    lossless=lossless,
                    quality=100 if lossless else 82,
                    method=6,
                    exact=True,
                )
                encoded = output.getvalue()
                if len(encoded) > MAX_ENCODED_BYTES:
                    raise InvalidPhotoUpload("canonical encoded size exceeds the safe limit")
                return NormalizedPhoto(data=encoded)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise InvalidPhotoUpload("image pixels exceed the safe limit") from exc
    except (OSError, SyntaxError) as exc:
        raise InvalidPhotoUpload("image decode failed") from exc
