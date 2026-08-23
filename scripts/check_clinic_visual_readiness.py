#!/usr/bin/env python3
"""운영 중인 병원의 공개 표면 시각 승인 상태를 점검한다.

감사에서 확인된 문제는 코드가 아니라 데이터였다. 로고·대표색·첫 화면 카피·정보
우선순위가 비어 있으면 병원마다 같은 플랫폼 기본값으로 보인다. 이 스크립트는
어느 병원의 무엇이 비어 있는지 AE가 바로 볼 수 있게 정리한다.

사진은 의도적으로 점검 대상이 아니다. 실사진이 없는 병원도 정상 운영 대상이고,
사진을 필수로 만들면 공개가 막힌다.

사용:
    python3 scripts/check_clinic_visual_readiness.py
        # SYNC_DATABASE_URL을 읽어 실제 병원 상태를 조회
    python3 scripts/check_clinic_visual_readiness.py --json
    python3 scripts/check_clinic_visual_readiness.py --strict
        # 승인이 남은 병원이 있으면 exit 1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field

HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
ACCESS_MODES = frozenset({"urgent", "appointment", "specialist"})
STORED_LOGO_PREFIXES = ("gs://", "local://", "/assets/")

# 2026-08-21 감사 대상 6곳(노원탑365 포함). 조회 결과에 없으면 별도로 보고한다.
AUDITED_HOSPITALS = (
    "장편한외과의원",
    "연세속시원내과의원",
    "행복드림의원",
    "서울W내과의원 위례점",
    "강심장내과의원",
    "노원탑365의원",
)

VISUAL_FIELDS = ("logo", "primary_color", "hero_copy", "access_mode")

FIELD_LABELS = {
    "logo": "공식 로고",
    "primary_color": "대표색 1개",
    "hero_copy": "첫 화면 카피",
    "access_mode": "첫 화면 정보 우선순위",
}


@dataclass(frozen=True)
class VisualReadiness:
    name: str
    slug: str
    missing: tuple[str, ...] = field(default=())
    photo_count: int = 0

    @property
    def approved(self) -> bool:
        return not self.missing


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def evaluate_hospital(row: dict) -> VisualReadiness:
    """한 병원의 시각 승인 상태. 사진 수는 참고 정보일 뿐 판정에 쓰지 않는다."""
    missing: list[str] = []
    # 값이 있는 것으로는 부족하다 — 공개 표면은 우리 저장소·오리진이 아닌 주소를 쓰지
    # 않으므로 외부 CDN 로고는 저장돼 있어도 화면에 뜨지 않는다(O-5).
    # 판정 기준은 backend/app/services/hospital_logo.py의 is_stored_logo_ref와 같다.
    # 이 스크립트는 앱 패키지 없이 단독 실행되므로 임포트하지 않고 규칙만 맞춘다.
    if not _text(row.get("logo_url")).startswith(STORED_LOGO_PREFIXES):
        missing.append("logo")
    if not HEX_COLOR.fullmatch(_text(row.get("brand_primary_color"))):
        missing.append("primary_color")
    if not (_text(row.get("hero_headline")) or _text(row.get("hero_description"))):
        missing.append("hero_copy")
    if _text(row.get("site_access_mode")) not in ACCESS_MODES:
        missing.append("access_mode")

    return VisualReadiness(
        name=_text(row.get("name")),
        slug=_text(row.get("slug")),
        missing=tuple(missing),
        photo_count=int(row.get("photo_count") or 0),
    )


def evaluate_all(rows: list[dict]) -> list[VisualReadiness]:
    return [evaluate_hospital(row) for row in rows]


def format_report(results: list[VisualReadiness]) -> str:
    if not results:
        return "공개 운영 중인 병원을 찾지 못했습니다."

    lines = ["병원별 공개 표면 시각 승인 상태 (사진은 필수 아님)", ""]
    for result in sorted(results, key=lambda item: (item.approved, item.name)):
        if result.approved:
            lines.append(f"  [OK]   {result.name} — 승인 완료 (사진 {result.photo_count}장)")
            continue
        labels = ", ".join(FIELD_LABELS[key] for key in result.missing)
        lines.append(f"  [TODO] {result.name} — 승인 필요: {labels} (사진 {result.photo_count}장)")

    pending = [result for result in results if not result.approved]
    lines.append("")
    lines.append(f"승인 완료 {len(results) - len(pending)}곳 / 전체 {len(results)}곳")
    if pending:
        lines.append(
            "Admin 온보딩 '병원 기본 정보' 단계의 공개 표면 시각 요소에서 채울 수 있습니다."
        )
    return "\n".join(lines)


def missing_audited_hospitals(results: list[VisualReadiness]) -> tuple[str, ...]:
    """감사 대상 중 조회 결과에 없는 병원 — 데이터 범위를 조용히 놓치지 않기 위해."""
    found = {result.name for result in results}
    return tuple(name for name in AUDITED_HOSPITALS if name not in found)


def _load_rows() -> list[dict]:
    database_url = os.environ.get("SYNC_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit(
            "SYNC_DATABASE_URL(또는 DATABASE_URL)이 필요합니다. "
            "운영 DB 접근 없이 실행하려면 --json으로 저장된 결과를 사용하세요."
        )
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql://")

    import psycopg2  # 실제 조회할 때만 필요
    import psycopg2.extras

    query = """
        SELECT h.name,
               h.slug,
               h.logo_url,
               h.brand_primary_color,
               h.hero_headline,
               h.hero_description,
               h.site_access_mode,
               (
                   SELECT count(*)
                     FROM hospital_source_assets a
                    WHERE a.hospital_id = h.id
                      AND a.is_public
                      AND a.source_type::text LIKE 'PHOTO_%'
               ) AS photo_count
          FROM hospitals h
         WHERE h.site_live IS TRUE
            OR h.name = ANY(%(audited)s)
         ORDER BY h.name
    """
    with (
        psycopg2.connect(database_url) as connection,
        connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor,
    ):
        cursor.execute(query, {"audited": list(AUDITED_HOSPITALS)})
        return [dict(row) for row in cursor.fetchall()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="결과를 JSON으로 출력")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="승인이 남은 병원이 있으면 exit 1",
    )
    args = parser.parse_args(argv)

    results = evaluate_all(_load_rows())

    if args.json:
        print(
            json.dumps(
                {
                    "hospitals": [
                        {
                            "name": result.name,
                            "slug": result.slug,
                            "approved": result.approved,
                            "missing": list(result.missing),
                            "photo_count": result.photo_count,
                        }
                        for result in results
                    ],
                    "not_found": list(missing_audited_hospitals(results)),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(format_report(results))
        not_found = missing_audited_hospitals(results)
        if not_found:
            print(f"\n조회 결과에 없는 감사 대상: {', '.join(not_found)}")

    pending = [result for result in results if not result.approved]
    return 1 if args.strict and pending else 0


if __name__ == "__main__":
    sys.exit(main())
