"""무료 진단 1회 제한의 신원 정규화·해시 (설계 §2-3).

**전화번호와 이메일을 각각 잠근다.** 둘 중 하나라도 이미 쓰였으면 거부한다 —
전화번호만 잠그면 메일만 바꿔 남의 병원을 계속 신청할 수 있고, 이메일만 잠그면
한 사람이 여러 병원을 훑을 수 있다. 함께 걸어야 우회에 새 번호와 새 메일이
**동시에** 필요해진다.

원문이 아니라 해시만 저장한다. 원문은 `sales_leads`에 있고 180일 뒤 파기되지만
해시는 남는다 — 보관기간이 지났다고 두 번째 무료 진단을 주는 것이 아니기 때문이다.
전화번호는 공간이 좁아(국내 유선/휴대폰) 순수 sha256이면 전수 대입으로 역산되므로
pepper를 섞는다.
"""
import hashlib
import re

from app.core.config import settings

_NON_DIGITS = re.compile(r"\D+")
_WHITESPACE = re.compile(r"\s+")

# 국내 번호를 국제 표기로 적었을 때의 접두. `+82 2-123-4567` → `021234567`
_KR_COUNTRY_PREFIXES = ("0082", "82")


class InvalidPhoneNumber(ValueError):
    pass


class InvalidEmail(ValueError):
    pass


def normalize_phone(raw: str) -> str:
    """전화번호 → 숫자만. 국가번호는 0으로 치환한다.

    `02-123-4567` · `+82 2 123 4567` · `(02) 123-4567`이 모두 같은 키가 되어야
    표기 방식만 바꾼 재신청이 통하지 않는다.
    """
    digits = _NON_DIGITS.sub("", raw or "")
    if not digits:
        raise InvalidPhoneNumber("전화번호를 입력해 주세요.")

    # 국내 번호는 항상 0으로 시작하므로 82로 시작하는 것은 국가번호 표기다.
    # `+82 2-...`는 0을 붙여야 하고 `+82 02-...`는 이미 있는 0을 그대로 둬야 한다 —
    # 둘을 구분하지 않으면 같은 번호가 두 개의 잠금 키로 갈라진다.
    for prefix in _KR_COUNTRY_PREFIXES:
        if digits.startswith(prefix):
            rest = digits[len(prefix) :]
            digits = rest if rest.startswith("0") else "0" + rest
            break

    # 국내 최단(지역번호 2자리 + 국번 3자리 + 4자리 = 9)보다 짧거나, 국제번호를 넘어서면 거부.
    if not 9 <= len(digits) <= 15:
        raise InvalidPhoneNumber("전화번호 형식을 확인해 주세요.")
    return digits


def normalize_email(raw: str) -> str:
    """이메일 → trim·lower·로컬파트의 `+태그` 제거.

    `+태그` 제거는 **가장 게으른 우회만** 막는다(`me+1@gmail.com`). 작정한 사람은 새
    주소를 만들면 되고, PRD F1-6이 그 위험을 이미 수용했다. 도메인 단위 차단 같은
    것으로 확대하지 않는다 — 정상 사용자를 더 많이 막게 된다.
    """
    value = _WHITESPACE.sub("", (raw or "")).lower()
    if value.count("@") != 1:
        raise InvalidEmail("이메일 형식을 확인해 주세요.")
    local, domain = value.split("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise InvalidEmail("이메일 형식을 확인해 주세요.")

    local = local.split("+", 1)[0]
    if not local:
        raise InvalidEmail("이메일 형식을 확인해 주세요.")
    return f"{local}@{domain}"


def _peppered_hash(namespace: str, value: str) -> str:
    """namespace를 섞어 전화번호 해시와 이메일 해시가 서로 충돌하지 않게 한다."""
    pepper = settings.LEAD_LOCK_HASH_PEPPER
    return hashlib.sha256(f"{namespace}|{value}|{pepper}".encode()).hexdigest()


def phone_lock_hash(raw_phone: str) -> str:
    return _peppered_hash("phone", normalize_phone(raw_phone))


def email_lock_hash(raw_email: str) -> str:
    return _peppered_hash("email", normalize_email(raw_email))
