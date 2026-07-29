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
import base64
import hashlib
import hmac

from app.core.config import settings


def derive_report_token(diagnosis_id) -> str:
    """진단 id → 토큰 원문. **결정적이다.**

    난수로 만들면 원문을 어디에도 저장하지 않으므로 접수 응답에서 한 번 쓰고 사라진다.
    그런데 리포트 메일은 측정이 끝난 **나중에** 같은 링크를 실어야 한다 — 그때 원문을
    복원할 방법이 없으면 진단 하나에 토큰이 두 개가 되고, 폐기·만료를 두 벌 관리하게 된다.

    HMAC은 서버 시크릿 없이는 계산할 수 없고, 재료인 진단 id 자체가 UUIDv4라
    시크릿을 알아도 대상 진단을 찍을 수 없다. DB에는 여전히 해시만 저장한다.
    """
    digest = hmac.new(
        settings.LEAD_REPORT_TOKEN_SECRET.encode(),
        f"lead-report|{diagnosis_id}".encode(),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def issue_report_token(diagnosis_id) -> tuple[str, str]:
    """(원문, 해시)."""
    raw = derive_report_token(diagnosis_id)
    return raw, hash_report_token(raw)


def hash_report_token(raw: str) -> str:
    pepper = settings.LEAD_REPORT_TOKEN_SECRET
    return hashlib.sha256(f"lead-report|{raw}|{pepper}".encode()).hexdigest()


def report_status_url(raw_token: str) -> str:
    """접수 직후 사용자를 보내는 주소. 메일이 안 와도 결과에 도달하는 경로다(설계 §6-3)."""
    return f"{settings.SITE_BASE_URL.rstrip('/')}/ai-diagnosis/status/{raw_token}"
