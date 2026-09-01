"""Anthropic 호출 재시도 필터 — 결정적 4xx를 유료로 반복 호출하지 않기 위한 공통 판정.

tenacity 재시도 1회 = 유료 호출 1회다. 잘못된 요청·인증·권한·모델 오타는 같은 입력으로
다시 호출해도 항상 같은 4xx라서, 재시도는 지연만 늘리고 비용/쿼터만 태운다. 타임아웃·5xx·
429·파서 실패만 재시도한다.
"""

from __future__ import annotations

import anthropic

# 같은 요청을 다시 보내도 결과가 바뀌지 않는 클라이언트 오류.
NON_RETRYABLE_ANTHROPIC_ERRORS: tuple[type[BaseException], ...] = (
    anthropic.BadRequestError,
    anthropic.AuthenticationError,
    anthropic.PermissionDeniedError,
    anthropic.NotFoundError,
)


def is_retryable_anthropic_error(exc: BaseException) -> bool:
    """결정적 Anthropic 4xx면 False — 즉시 중단하고 호출부의 폴백으로 넘긴다."""

    return not isinstance(exc, NON_RETRYABLE_ANTHROPIC_ERRORS)


__all__ = ("NON_RETRYABLE_ANTHROPIC_ERRORS", "is_retryable_anthropic_error")
