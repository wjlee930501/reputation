import os


# setdefault가 아니라 강제 설정 — 테스트가 X-Admin-Key로 이 값을 보내므로, CI 잡 env가
# 다른 ADMIN_SECRET_KEY를 깔아두면 setdefault로는 401이 난다 (suite를 hermetic하게 유지).
os.environ["ADMIN_SECRET_KEY"] = "test-admin-key"
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/reputation_test")
os.environ.setdefault("SYNC_DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/reputation_test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
