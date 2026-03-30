from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "production"
    ADMIN_SECRET_KEY: str
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000"]

    # DB
    DATABASE_URL: str
    SYNC_DATABASE_URL: str

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Anthropic — 콘텐츠 생성
    ANTHROPIC_API_KEY: str
    CLAUDE_MODEL: str = "claude-sonnet-4-5"
    CLAUDE_MODEL_FAST: str = "claude-haiku-4-5-20251001"

    # Google Cloud — Imagen 3
    GCP_PROJECT_ID: str = ""
    GCP_LOCATION: str = "us-central1"
    GCP_STORAGE_BUCKET: str = "reputation-images"

    # OpenAI — SoV
    OPENAI_API_KEY: str
    OPENAI_MODEL_QUERY: str = "gpt-4o"
    OPENAI_MODEL_PARSE: str = "gpt-4o-mini"

    # Gemini — SoV
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-flash-latest"

    # Slack
    SLACK_WEBHOOK_URL: str = ""

    # Report
    REPORT_OUTPUT_DIR: str = "/tmp/reports"
    GCS_REPORTS_BUCKET: str = "reputation-reports"

    # Sentry
    SENTRY_DSN: str = ""

    # SoV
    SOV_REPEAT_COUNT: int = 10
    SOV_REPEAT_COUNT_WEEKLY: int = 5

    # Domain
    CNAME_TARGET: str = "aeo.motionlabs.io"

    # Admin
    ADMIN_BASE_URL: str = "http://localhost:3000"  # 🔴 CRITICAL: 환경변수로 분리 (.env에서 프로덕션 URL 설정)


settings = Settings()
