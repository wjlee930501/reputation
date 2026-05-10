import json
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "production"
    ADMIN_SECRET_KEY: str
    # NoDecode: pydantic-settings의 env-source 자동 JSON 디코드를 끄고 raw 문자열을 검증자에 전달.
    # .env에서 comma-separated 표기를 허용하기 위함.
    ALLOWED_ORIGINS: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    TRUSTED_PROXY_IPS: Annotated[list[str], NoDecode] = ["127.0.0.1", "::1"]

    @field_validator("ALLOWED_ORIGINS", "TRUSTED_PROXY_IPS", mode="before")
    @classmethod
    def _parse_list_setting(cls, value: object) -> object:
        # .env에서 두 가지 표기 모두 허용:
        #   SETTING=https://a.com,https://b.com   (comma-separated)
        #   SETTING=["https://a.com","https://b.com"]  (JSON array)
        # pydantic-settings 기본은 JSON만 받아 운영자 첫 셋업에서 막히던 표면을 보강.
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                return json.loads(stripped)
            return [origin.strip() for origin in stripped.split(",") if origin.strip()]
        return value

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
    # 기본은 web_search 미사용 (chat.completions = 모델 recall). True 시 Responses API +
    # web_search tool 사용. 약속한 "ChatGPT Search 답변 노출률" 측정에 정합하려면 True.
    OPENAI_CHATGPT_USE_WEB_SEARCH: bool = False

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
    ADMIN_ACTOR_NAME: str = "AE"  # 단일 운영자 이름 (감사 로그 actor) — 다중 사용자 도입 시 NextAuth로 전환

    # Site (public)
    SITE_BASE_URL: str = "https://reputation.co.kr"  # llms.txt absolute URL 등에 사용

    # Lead retention (개인정보보호법 제21조 — 보유기간)
    LEAD_RETENTION_DAYS: int = 180  # 수집 후 자동 파기까지 일수
    LEAD_CONSENT_VERSION: str = "v1.2026-05"  # 처리방침 버전 — 변경 시 재동의 필요

    # Public 폼 rate-limit
    PUBLIC_LEAD_RATE_LIMIT: str = "5/minute;30/hour;100/day"

    # 발행 시 site(Vercel) sitemap·페이지 캐시 무효화. 빈 값이면 호출 생략.
    SITE_REVALIDATE_URL: str = ""
    SITE_REVALIDATE_SECRET: str = ""


settings = Settings()
