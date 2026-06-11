"""커스텀 도메인 정규화/검증 공용 유틸.

저장 경로(Admin PATCH /domain)와 조회 경로(Public by-domain lookup)가 같은
정규화 규칙을 쓰지 않으면 저장은 되는데 호스트 매칭이 안 되는 도메인이 생긴다.
규칙: 소문자, 앞뒤 공백 제거, 스킴/경로/쿼리 제거, 포트 제거, 끝 점(.) 제거.
"""
import re

# 호스트명 검증 (예: info.jangpyeon.com) — 최소 2레이블 + 알파벳 TLD.
HOSTNAME_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")

_MAX_HOSTNAME_LENGTH = 253


def normalize_domain(value: str | None) -> str | None:
    """입력을 비교 가능한 호스트명으로 정규화한다. 비어 있으면 None.

    "https://Clinic.Example.COM:443/path." → "clinic.example.com"
    검증은 하지 않는다 — is_valid_hostname과 조합해서 사용.
    """
    if not value:
        return None
    candidate = value.strip().lower()
    if "://" in candidate:
        candidate = candidate.split("://", 1)[1]
    # 경로/쿼리/프래그먼트 제거
    candidate = candidate.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    # 사용자정보(user@host) 제거
    if "@" in candidate:
        candidate = candidate.rsplit("@", 1)[1]
    # 포트 제거 (호스트명 전용 — IPv6 리터럴은 호스트명 검증에서 어차피 탈락)
    if ":" in candidate:
        candidate = candidate.split(":", 1)[0]
    # FQDN 끝 점 제거
    candidate = candidate.rstrip(".")
    return candidate or None


def is_valid_hostname(value: str | None) -> bool:
    """정규화된 값이 유효한 공개 호스트명인지 검사한다."""
    if not value or len(value) > _MAX_HOSTNAME_LENGTH:
        return False
    return bool(HOSTNAME_RE.match(value))
