import os

# setdefault가 아니라 강제 설정 — 테스트가 X-Admin-Key로 이 값을 보내므로, CI 잡 env가
# 다른 ADMIN_SECRET_KEY를 깔아두면 setdefault로는 401이 난다 (suite를 hermetic하게 유지).
os.environ["ADMIN_SECRET_KEY"] = "test-admin-key"
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/reputation_test")
os.environ.setdefault("SYNC_DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/reputation_test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

import pytest  # noqa: E402 — 위 환경변수 설정이 app import보다 먼저여야 한다.


@pytest.fixture(autouse=True)
def _reset_provider_client_singletons():
    """공급자 클라이언트는 lazy 싱글턴이다 — 테스트가 settings나 SDK 생성자를
    monkeypatch해도 앞 테스트가 캐시해 둔 클라이언트를 물려받지 않게 매번 비운다."""
    from app.services import content_ai_review, essence_engine, image_engine

    modules = (content_ai_review, essence_engine, image_engine)
    for module in modules:
        module._reset_clients_for_tests()
    yield
    for module in modules:
        module._reset_clients_for_tests()


@pytest.fixture(autouse=True)
async def _dispose_global_async_engine():
    """전역 async 엔진은 lazy 싱글턴이고 asyncpg 풀은 만든 이벤트 루프에 묶인다 —
    pytest-asyncio는 테스트마다 루프를 새로 만들므로, 앞 테스트가 만든 엔진을
    물려받으면 다음 테스트는 'Event loop is closed'를 만난다(예: provider_usage._persist가
    이 예외를 삼키고 False를 돌려줘 순서에 따라 실패). 테스트 루프가 살아 있을 때 정리한다."""
    from app.core import database

    yield
    engine = database.engine
    database.engine = None
    database.AsyncSessionLocal = None
    if engine is not None:
        await engine.dispose()
