"""리포트/상태 페이지 열람 토큰 (PRD F5-4).

**서명 토큰(JWT류)이 아니라 불투명 난수 + 해시 저장**을 쓴다.

서명 토큰의 장점은 DB 조회 없이 검증된다는 것인데, 우리는 어차피 조회해야 한다 —
폐기(`revoked_at`), 만료, 열람 횟수를 기록해야 하기 때문이다. 조회가 전제라면
난수 쪽이 엄격히 낫다: 서명 키가 유출돼도 위조가 불가능하고(DB에 없는 토큰은 그냥
없는 토큰이다), audience를 잘못 넣어 Admin 세션 토큰과 섞일 여지도 없다.

저장은 `sha256(token + pepper)`. DB가 유출돼도 토큰 원문이 나오지 않는다.
pepper를 섞는 이유는 32바이트 난수를 무지개표로 뒤집을 수는 없지만, 애플리케이션
비밀 없이는 해시를 만들 수 없게 해 DB 단독 유출에서 토큰을 못 만들게 하기 위함이다.
"""
import hashlib
import secrets

from app.core.config import settings

# 32바이트 = 256비트. URL-safe base64로 43자.
_TOKEN_BYTES = 32


def generate_report_token() -> tuple[str, str]:
    """(원문, 해시). 원문은 이 순간 이후 어디에도 저장하지 않는다 — 링크에만 실린다."""
    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    return raw, hash_report_token(raw)


def hash_report_token(raw: str) -> str:
    pepper = settings.LEAD_REPORT_TOKEN_SECRET
    return hashlib.sha256(f"lead-report|{raw}|{pepper}".encode()).hexdigest()


def report_status_url(raw_token: str) -> str:
    """접수 직후 사용자를 보내는 주소. 메일이 안 와도 결과에 도달하는 경로다(설계 §6-3)."""
    return f"{settings.SITE_BASE_URL.rstrip('/')}/ai-diagnosis/status/{raw_token}"
