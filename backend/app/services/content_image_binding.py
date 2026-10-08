"""Select only byte-bound images for public content surfaces."""

from dataclasses import dataclass

from app.models.content import ContentItem
from app.models.hospital import Hospital
from app.services.content_publication import image_certification_current
from app.services.image_engine import IMAGE_POLICY_VERSION, image_content_hash_from_url


@dataclass(frozen=True, slots=True)
class PublicImageAsset:
    image_url: str
    content_hash: str


def certified_public_image_asset(
    item: ContentItem,
    hospital: Hospital | None = None,
) -> PublicImageAsset | None:
    """Select the article certificate, then a current hospital fallback certificate."""

    if image_certification_current(item):
        return PublicImageAsset(
            image_url=str(item.image_url),
            content_hash=str(item.image_content_hash),
        )
    if hospital is None:
        return None
    image_url = str(getattr(hospital, "fallback_image_url", None) or "").strip()
    content_hash = str(
        getattr(hospital, "fallback_image_content_hash", None) or ""
    ).strip()
    source_url = str(getattr(hospital, "fallback_image_source_url", None) or "").strip()
    hero_url = str(getattr(hospital, "hero_image_url", None) or "").strip()
    verified_at = getattr(hospital, "fallback_image_verified_at", None)
    policy_version = getattr(hospital, "fallback_image_policy_version", None)
    if not (image_url and content_hash and source_url and hero_url and verified_at):
        return None
    if source_url != hero_url or policy_version != IMAGE_POLICY_VERSION:
        return None
    if image_content_hash_from_url(image_url) != content_hash:
        return None
    return PublicImageAsset(image_url=image_url, content_hash=content_hash)
