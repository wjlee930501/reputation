"""공개 사이트 레이아웃 A/B/C — 마케터가 고르는 표현 계층 선택.

레이아웃은 화면 구성만 바꾼다. 구조화 데이터·llms.txt·사실 값은 레이아웃과 무관하다
(docs/plans/2026-11-13-clinic-site-layouts-abc.md). 여기서는 고르는 화면이 보여 줄
"같은 시·도 공개 병원이 쓰는 레이아웃" 분포만 계산한다 — 같은 지역 병원 사이트가 한
틀로 찍혀 보이지 않게 하려는 원래 요청(2026-11-12 다올연합의원)에 바로 답하는 값이다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.models.hospital import Hospital
from app.services.hospital_lifecycle import profile_publication_verdict
from app.services.naver_place import province_key

SITE_LAYOUTS: tuple[str, ...] = ("A", "B", "C")
# 레이아웃을 아직 고르지 않은 병원(레이아웃 도입 전 화면)을 세는 칸.
UNSET_LAYOUT = "unset"


def province_of(address: str | None, region: Iterable[str] | None = None) -> str | None:
    """주소 첫 토큰의 시·도. 주소가 없거나 읽히지 않으면 지역 입력에서 처음 읽히는 시·도.

    지역 입력은 자유 문장("경산, 사동, 대구")이라 주소보다 덜 정확하다 — 계약 등록처럼
    주소가 아직 없을 때만 쓴다.
    """

    first = (address or "").strip().split(maxsplit=1)
    if first:
        found = province_key(first[0])
        if found:
            return found
    for entry in region or ():
        for token in str(entry).replace(",", " ").split():
            found = province_key(token)
            if found:
                return found
    return None


@dataclass(frozen=True)
class LayoutDistribution:
    province: str | None
    counts: dict[str, int]
    total: int

    def as_dict(self) -> dict[str, object]:
        return {"province": self.province, "counts": self.counts, "total": self.total}


def layout_distribution(
    hospitals: Iterable[Hospital],
    *,
    province: str | None,
    exclude_hospital_id: object | None = None,
) -> LayoutDistribution:
    """같은 시·도에서 지금 공개 중인 병원이 쓰는 레이아웃 수.

    시·도를 모르면 세지 않는다(전국 수를 지역 수처럼 보이게 하지 않는다). 고르는 병원
    자신은 빼서, 바꾸려는 병원이 자기 값을 세는 일이 없게 한다.
    """

    counts = {layout: 0 for layout in (*SITE_LAYOUTS, UNSET_LAYOUT)}
    if province is None:
        return LayoutDistribution(province=None, counts=counts, total=0)
    total = 0
    for hospital in hospitals:
        if exclude_hospital_id is not None and hospital.id == exclude_hospital_id:
            continue
        if not profile_publication_verdict(hospital).allowed:
            continue
        if province_of(hospital.address, hospital.region) != province:
            continue
        layout = hospital.site_layout if hospital.site_layout in SITE_LAYOUTS else UNSET_LAYOUT
        counts[layout] += 1
        total += 1
    return LayoutDistribution(province=province, counts=counts, total=total)
