"""Apply the operator-approved visual identity values for the onboarded clinics.

`scripts/check_clinic_visual_readiness.py` reports which clinics are still missing
a logo, a primary color, hero copy or an access order. This fills in the values
that an audit has actually verified, so the remaining gaps in that report are the
ones that genuinely need an operator decision.

Every value here is traceable to a checked artifact, and nothing is invented:

- `#006772` for 노원탑365의원 comes from the official brand palette recorded in
  `artifacts/visual-audit-nowon-2026-08-21/recommendations.md`, together with the
  availability-first access order for a 365/야간 clinic.
- `#D6A72C` for 행복드림의원 is the primary color already stored for that clinic
  and confirmed in `artifacts/visual-system-audit-2026-08-21/system-report.md`.
- Clinics with no verified official color keep `brand_primary_color = NULL` so the
  public surface renders its neutral default instead of a made-up palette.

The second free accent color is retired for every clinic: the public surface
derives one contrast-safe ramp from the single primary color.

Hero copy is never hardcoded prose about a clinic's hours. For an
availability-first clinic it is composed from the business hours already stored
on the profile, and skipped entirely when those hours do not support the claim.
Logos are left untouched — a logo is only used when it was already stored.
"""

from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SyncSessionLocal
from app.models.hospital import Hospital
from app.utils.medical_filter import check_forbidden

logger = logging.getLogger(__name__)

WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri")
DAY_LABELS = {
    "mon": "월요일",
    "tue": "화요일",
    "wed": "수요일",
    "thu": "목요일",
    "fri": "금요일",
    "sat": "토요일",
    "sun": "일요일",
}
CLOSED_PATTERN = re.compile(r"휴진|휴무|closed", re.IGNORECASE)
TIME_PATTERN = re.compile(r"(\d{1,2}):(\d{2})")
EVENING_HOUR = 20

HERO_HEADLINE_LIMIT = 160
HERO_DESCRIPTION_LIMIT = 320


@dataclass(frozen=True, slots=True)
class ClinicVisualIdentity:
    """Verified values for one clinic. `None` means 'leave the stored value alone'."""

    slug: str
    name: str
    brand_primary_color: str | None = None
    site_access_mode: str | None = None
    # Compose the hero copy from stored business hours (availability-first clinics).
    availability_hero: bool = False
    evidence: str = ""


VERIFIED_CLINIC_VISUALS: tuple[ClinicVisualIdentity, ...] = (
    ClinicVisualIdentity(
        slug="noweontab365yiweon",
        name="노원탑365의원",
        brand_primary_color="#006772",
        site_access_mode="urgent",
        availability_hero=True,
        evidence="artifacts/visual-audit-nowon-2026-08-21/recommendations.md",
    ),
    ClinicVisualIdentity(
        slug="haengbogdeurimyiweon",
        name="행복드림의원",
        brand_primary_color="#D6A72C",
        evidence="artifacts/visual-system-audit-2026-08-21/system-report.md",
    ),
    ClinicVisualIdentity(
        slug="jangpyeonhanoegwayiweon",
        name="장편한외과의원",
        evidence="stored primary color kept; no second accent",
    ),
    ClinicVisualIdentity(
        slug="yeonsesogsiweonnaegwayiweon",
        name="연세속시원내과의원",
        evidence="no verified official color — stays on the neutral default",
    ),
    ClinicVisualIdentity(
        slug="seoulwnaegwayiweon-wiryejeom",
        name="서울W내과의원 위례점",
        evidence="no verified official color — stays on the neutral default",
    ),
    ClinicVisualIdentity(
        slug="gangsimjangnaegwayiweon",
        name="강심장내과의원",
        evidence="no verified official color — stays on the neutral default",
    ),
)


@dataclass
class VisualSeedResult:
    clinics_matched: int = 0
    clinics_missing: list[str] = field(default_factory=list)
    changes: dict[str, dict[str, object]] = field(default_factory=dict)

    @property
    def clinics_changed(self) -> int:
        return len(self.changes)


def _normalize_hours_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def _is_open(value: str | None) -> bool:
    return bool(value) and not CLOSED_PATTERN.search(value or "")


def _latest_hour(value: str) -> int:
    return max((int(hour) for hour, _ in TIME_PATTERN.findall(value)), default=0)


def availability_facts(business_hours: object) -> tuple[str, ...]:
    """Availability claims the stored hours actually support, in hero order."""
    hours = business_hours if isinstance(business_hours, dict) else {}
    normalized = {key: _normalize_hours_value(value) for key, value in hours.items()}
    open_hours = {key: value for key, value in normalized.items() if _is_open(value)}
    if not open_hours:
        return ()

    facts: list[str] = []
    if any(_latest_hour(value or "") >= EVENING_HOUR for value in open_hours.values()):
        facts.append("야간")
    if "sat" in open_hours and "sun" in open_hours:
        facts.append("주말")
    elif "sat" in open_hours:
        facts.append("토요일")
    return tuple(facts)


def _hours_sentence(business_hours: dict) -> str | None:
    normalized = {key: _normalize_hours_value(value) for key, value in business_hours.items()}
    open_hours = {key: value for key, value in normalized.items() if _is_open(value)}
    if not open_hours:
        return None

    parts: list[str] = []
    weekday_values = [open_hours[key] for key in WEEKDAY_KEYS if key in open_hours]
    weekday_open = [key for key in WEEKDAY_KEYS if key in open_hours]
    if len(weekday_open) == len(WEEKDAY_KEYS) and len(set(weekday_values)) == 1:
        parts.append(f"평일 {weekday_values[0]}")
    else:
        parts.extend(f"{DAY_LABELS[key]} {open_hours[key]}" for key in weekday_open)
    parts.extend(f"{DAY_LABELS[key]} {open_hours[key]}" for key in ("sat", "sun") if key in open_hours)
    return ", ".join(parts)


def compose_availability_hero_copy(business_hours: object) -> tuple[str, str] | None:
    """Short availability-first hero copy built only from stored business hours.

    Returns `(headline, description)`, or `None` when the stored hours cannot
    support an availability claim — in that case the clinic keeps whatever copy
    an operator already approved.
    """
    hours = business_hours if isinstance(business_hours, dict) else {}
    sentence = _hours_sentence(hours)
    if sentence is None:
        return None
    facts = availability_facts(hours)
    if not facts:
        return None

    headline = f"{'·'.join(facts)} 진료 시간을\n방문 전에 확인하세요"
    description = f"{sentence} 진료합니다. 당일 진료 여부는 전화로 확인해 주세요."
    if len(headline) > HERO_HEADLINE_LIMIT or len(description) > HERO_DESCRIPTION_LIMIT:
        return None
    if check_forbidden(headline) or check_forbidden(description):
        return None
    return headline, description


def plan_clinic_visual_changes(
    hospital: Hospital,
    identity: ClinicVisualIdentity,
) -> dict[str, object]:
    """Fields that need to change for this clinic. Empty dict means already applied."""
    changes: dict[str, object] = {}

    if identity.brand_primary_color and (
        hospital.brand_primary_color != identity.brand_primary_color
    ):
        changes["brand_primary_color"] = identity.brand_primary_color

    # One primary color plus a system-derived ramp — the free second accent is retired.
    if getattr(hospital, "brand_accent_color", None) is not None:
        changes["brand_accent_color"] = None

    if identity.site_access_mode and hospital.site_access_mode != identity.site_access_mode:
        changes["site_access_mode"] = identity.site_access_mode

    if identity.availability_hero:
        copy = compose_availability_hero_copy(hospital.business_hours)
        if copy is not None:
            headline, description = copy
            if hospital.hero_headline != headline:
                changes["hero_headline"] = headline
            if hospital.hero_description != description:
                changes["hero_description"] = description

    return changes


def seed_clinic_visual_identity(
    db: Session,
    apply_changes: bool = False,
    identities: tuple[ClinicVisualIdentity, ...] = VERIFIED_CLINIC_VISUALS,
) -> VisualSeedResult:
    result = VisualSeedResult()

    for identity in identities:
        hospital = db.execute(
            select(Hospital).where(Hospital.slug == identity.slug)
        ).scalar_one_or_none()
        if hospital is None:
            result.clinics_missing.append(identity.slug)
            logger.warning("clinic not found, skipping: %s", identity.slug)
            continue

        result.clinics_matched += 1
        changes = plan_clinic_visual_changes(hospital, identity)
        if not changes:
            logger.info("%s: visual identity already applied", identity.slug)
            continue

        result.changes[identity.slug] = changes
        logger.info("%s: %s", identity.slug, ", ".join(sorted(changes)))
        if apply_changes:
            for field_name, value in changes.items():
                setattr(hospital, field_name, value)

    if apply_changes:
        db.commit()
    else:
        db.rollback()
    return result


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply verified visual identity values for the onboarded clinics."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the values. Without this flag the run only reports what it would change.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with SyncSessionLocal() as db:
        result = seed_clinic_visual_identity(db, apply_changes=args.apply)

    logger.info(
        "clinic visual identity %s: matched=%d changed=%d missing=%s",
        "applied" if args.apply else "dry run",
        result.clinics_matched,
        result.clinics_changed,
        result.clinics_missing or "none",
    )


if __name__ == "__main__":
    _main()
