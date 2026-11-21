"""측정 키워드 30개 세트의 그룹·역할 규칙 (2026-11-21, docs/plans/2026-11-21-measurement-30-design.md).

병원마다 월간 측정 질문을 그룹 A~D로 나누고, 매달 같은 **고정** 질문과 달마다 바꾸는 **교체**
질문을 둔다. 이 모듈은 모델·DB를 부르지 않는 순수 규칙만 둔다 — 추적 세트(`sov_tracking_set`),
측정 spec(`measurement_selection`), 콘텐츠 주제 선택이 같은 판정을 쓰게 하려는 것이다.

- A 병원명 질문("{병원명} 어때?"): 이름이 질문에 있어 거의 늘 노출되므로 대표 노출률에 넣지 않는다.
  측정 의도는 문구가 아니라 저장된 그룹으로 정한다 → `BRAND`.
- B 진료·검사 탐색, C 증상·고민, D 상황·조건: 대표 노출률(`LOCAL`)에 들어간다.
- 지난달 비교의 기준(측정 기준 지문·맞춘 칸)은 **고정 질문만**이다 — 교체 질문이 바뀌어도
  비교가 끊기지 않게.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any, Final

from sqlalchemy import or_

from app.services.sov_engine import QUERY_INTENT_BRAND, QUERY_INTENT_LOCAL

GROUP_BRAND: Final = "A"
QUESTION_GROUPS: Final[tuple[str, ...]] = ("A", "B", "C", "D")
ROLE_FIXED: Final = "FIXED"
ROLE_ROTATING: Final = "ROTATING"
SET_ROLES: Final[tuple[str, ...]] = (ROLE_FIXED, ROLE_ROTATING)

# 지침 3-4: 고정 20 + 교체 10 = 30개 내외. 마케터가 몇 개 빼거나 교체를 비워 둘 수 있게 범위로 둔다.
SET_FIXED_MIN: Final = 15
SET_FIXED_MAX: Final = 20
SET_ROTATING_MAX: Final = 10
SET_TOTAL_MAX: Final = SET_FIXED_MAX + SET_ROTATING_MAX

# 측정 기준 지문의 판. 옛 15개 세트의 지문(크기 + 문구)과 섞이지 않게 앞에 붙인다.
SET_FINGERPRINT_VERSION: Final = "set30-v1"


def _text(value: object) -> str:
    return str(value or "").strip()


def target_question_group(target: object) -> str | None:
    value = _text(getattr(target, "question_group", None)).upper()
    return value if value in QUESTION_GROUPS else None


def target_set_role(target: object) -> str | None:
    value = _text(getattr(target, "set_role", None)).upper()
    return value if value in SET_ROLES else None


def _is_active(target: object) -> bool:
    return _text(getattr(target, "status", "")).upper() == "ACTIVE"


def is_set_member(target: object) -> bool:
    """30개 세트로 측정하는 질문 — ACTIVE이고 그룹과 역할이 모두 있다."""

    return (
        _is_active(target)
        and target_question_group(target) is not None
        and target_set_role(target) is not None
    )


def uses_curated_set(targets: Iterable[object]) -> bool:
    """병원이 30개 방식인가 — 역할 있는 ACTIVE 질문이 하나라도 있으면 그렇다.

    그러면 추적 세트는 사람이(또는 초안 적용이) 정한 역할을 따르고, 측정 이력 기준 자동 제안
    (`sov_tracking_set.propose_tracking_set`)이 덮어쓰지 않는다.
    """

    return any(_is_active(target) and target_set_role(target) is not None for target in targets)


def set_measurement_intent(target: object) -> str | None:
    """30개 세트 질문의 측정 의도. 세트 질문이 아니면 None(문구 분류를 따른다).

    A는 BRAND, B~D는 LOCAL이다. 문구 분류(`classify_query_intent`)를 쓰지 않는다 — 마케터가 쓴
    증상 질문이 옛 정보형 표지("초기 증상이 뭔지 알려줘")와 겹쳐 INFO로 빠지거나, 병원명 질문이
    LOCAL로 대표 노출률에 들어가면 안 된다.
    """

    group = target_question_group(target)
    if group is None or target_set_role(target) is None:
        return None
    return QUERY_INTENT_BRAND if group == GROUP_BRAND else QUERY_INTENT_LOCAL


def content_eligible(target: object) -> bool:
    """이 질문을 글 주제·노출 보완 제안의 근거로 써도 되는가.

    병원명 질문(A)은 쓸 수 없다 — "{병원명} 어때?"는 글 주제가 아니라 병원 정보 정확도 점검이다.
    """

    return target_question_group(target) != GROUP_BRAND


def content_eligible_clause(column: Any) -> Any:
    """`content_eligible`의 SQL 꼴 — `AIQueryTarget.question_group` 칸을 받는다."""

    return or_(column.is_(None), column != GROUP_BRAND)


def _question_texts(target: object) -> list[str]:
    texts = {
        _text(getattr(variant, "query_text", ""))
        for variant in (getattr(target, "variants", ()) or ())
        if bool(getattr(variant, "is_active", True))
    }
    texts.discard("")
    if not texts and _text(getattr(target, "name", "")):
        texts.add(_text(getattr(target, "name", "")))
    return sorted(texts)


def fixed_members(targets: Iterable[object]) -> list[object]:
    return [
        target
        for target in targets
        if is_set_member(target) and target_set_role(target) == ROLE_FIXED
    ]


def set_fingerprint(targets: Iterable[object]) -> tuple[str, int]:
    """(측정 기준 지문, 기준 크기). 고정 질문의 그룹과 문구만으로 만든다.

    교체 질문은 넣지 않는다 — 넣으면 교체 10개를 바꾸는 매달 지난달 비교가 끊긴다. 그룹을 넣는
    이유: 같은 문구를 다른 그룹으로 옮기면 대표 노출률의 분모(A 제외)가 바뀐다.
    """

    rows = fixed_members(targets)
    lines = sorted(
        {
            f"{target_question_group(target)}|{text}"
            for target in rows
            for text in _question_texts(target)
        }
    )
    material = f"{SET_FINGERPRINT_VERSION}\n{len(rows)}\n" + "\n".join(lines)
    return hashlib.sha256(material.encode()).hexdigest()[:16], len(rows)


def set_problems(targets: Iterable[object]) -> list[str]:
    """30개 세트가 측정할 수 있는 모양인지. 비어 있으면 유효하다(사유는 운영 로그·사고 문구로 간다)."""

    members = [target for target in targets if is_set_member(target)]
    fixed = sum(target_set_role(target) == ROLE_FIXED for target in members)
    rotating = sum(target_set_role(target) == ROLE_ROTATING for target in members)
    problems: list[str] = []
    if not SET_FIXED_MIN <= fixed <= SET_FIXED_MAX:
        problems.append(f"fixed questions {fixed}, requires {SET_FIXED_MIN}..{SET_FIXED_MAX}")
    if rotating > SET_ROTATING_MAX:
        problems.append(f"rotating questions {rotating}, requires at most {SET_ROTATING_MAX}")
    if fixed + rotating > SET_TOTAL_MAX:
        problems.append(f"set size {fixed + rotating}, requires at most {SET_TOTAL_MAX}")
    if not any(
        target_question_group(target) != GROUP_BRAND and target_set_role(target) == ROLE_FIXED
        for target in members
    ):
        problems.append("no fixed B~D question for the headline rate")
    return problems


def manifest_query_set(target: object) -> dict[str, str] | None:
    """측정표 `platform_provenance.query_sets`에 얼릴 값. 세트 질문이 아니면 None."""

    group = target_question_group(target)
    role = target_set_role(target)
    if group is None or role is None:
        return None
    return {"group": group, "role": role}
