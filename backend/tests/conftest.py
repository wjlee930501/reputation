import os


os.environ.setdefault("ADMIN_SECRET_KEY", "test-admin-key")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/reputation_test")
os.environ.setdefault("SYNC_DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/reputation_test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
