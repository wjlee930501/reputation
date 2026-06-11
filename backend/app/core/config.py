import json
import os
from typing import Annotated
from urllib.parse import quote

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_CRITICAL_PRODUCTION_SECRETS = ("ADMIN_SECRET_KEY",)


def _resolve_secret(name: str, default: str = "") -> str:
    """Resolve a setting from GCP Secret Manager if available, otherwise from env.

    In production (Cloud Run), the service account has secretAccessor permission
    and Application Default Credentials are available. Falls back to env var
    for local development where Secret Manager may not be accessible.
    """
    env_value = os.getenv(name, default)
    if not env_value and os.getenv("APP_ENV") == "production":
        try:
            from google.cloud import secretmanager

            project = os.getenv("GCP_PROJECT_ID", "")
            if project:
                client = secretmanager.SecretManagerServiceClient()
                secret_path = client.secret_version_path(project, name, "latest")
                response = client.access_secret_version(request={"name": secret_path})
                env_value = response.payload.data.decode("UTF-8")
        except Exception:
            pass
    return env_value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "production"
    ADMIN_SECRET_KEY: str = ""
    # NoDecode: pydantic-settings의 env-source 자동 JSON 디코드를 끄고 raw 문자열을 검증자에 전달.
    ALLOWED_ORIGINS: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    TRUSTED_PROXY_IPS: Annotated[list[str], NoDecode] = ["127.0.0.1", "::1"]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.APP_ENV == "production":
            self.ADMIN_SECRET_KEY = _resolve_secret("ADMIN_SECRET_KEY", self.ADMIN_SECRET_KEY)
            self.ANTHROPIC_API_KEY = _resolve_secret("ANTHROPIC_API_KEY", self.ANTHROPIC_API_KEY)
            self.OPENAI_API_KEY = _resolve_secret("OPENAI_API_KEY", self.OPENAI_API_KEY)
            self.GEMINI_API_KEY = _resolve_secret("GEMINI_API_KEY", self.GEMINI_API_KEY)
            self.SLACK_WEBHOOK_URL = _resolve_secret("SLACK_WEBHOOK_URL", self.SLACK_WEBHOOK_URL)
            self.DB_PASSWORD = _resolve_secret("DB_PASSWORD", self.DB_PASSWORD)
            self._build_database_urls_from_secret_parts()
            self.SITE_REVALIDATE_SECRET = _resolve_secret(
                "SITE_REVALIDATE_SECRET", self.SITE_REVALIDATE_SECRET
            )
            self.SITE_BFF_SECRET = _resolve_secret("SITE_BFF_SECRET", self.SITE_BFF_SECRET)
            self._fail_if_critical_production_secrets_empty()
            self._validate_production_config()

    def _fail_if_critical_production_secrets_empty(self) -> None:
        missing = [
            secret_name
            for secret_name in _CRITICAL_PRODUCTION_SECRETS
            if not str(getattr(self, secret_name, "")).strip()
        ]
        if missing:
            names = ", ".join(missing)
            raise ValueError(f"Production critical admin secret(s) must be set: {names}")

    def _validate_production_config(self) -> None:
        """Fail fast on insecure production config (AUTH-1/AUTH-5/INFRA-3/OBS-5).

        These are env-driven values that must be set per-deployment. Crashing loudly
        at boot is preferable to silently mis-securing the public/admin surface.
        """
        errors: list[str] = []

        origins = [o.strip() for o in self.ALLOWED_ORIGINS if o.strip()]
        if not origins:
            errors.append("ALLOWED_ORIGINS must be set (CORS with credentials cannot use a wildcard).")
        for origin in origins:
            if origin == "*":
                errors.append("ALLOWED_ORIGINS must not contain '*' while credentials are allowed.")
            elif not origin.startswith("https://"):
                errors.append(f"ALLOWED_ORIGINS entry must be https://: {origin}")
            elif "localhost" in origin or "127.0.0.1" in origin:
                errors.append(f"ALLOWED_ORIGINS must not contain localhost in production: {origin}")

        proxies = [p.strip() for p in self.TRUSTED_PROXY_IPS if p.strip()]
        if not proxies or set(proxies) <= {"127.0.0.1", "::1"}:
            errors.append(
                "TRUSTED_PROXY_IPS must include the load-balancer/proxy hop in production "
                "(localhost-only defaults make X-Forwarded-For untrusted → rate-limit/consent_ip break)."
            )
        if any(p in {"0.0.0.0/0", "::/0"} for p in proxies):
            errors.append(
                "TRUSTED_PROXY_IPS must not be 0.0.0.0/0 or ::/0 — that trusts every hop, so the "
                "rightmost-untrusted X-Forwarded-For parse is bypassed and the client IP becomes "
                "spoofable. Set the actual LB/proxy CIDR ranges (e.g. GCP 130.211.0.0/22, 35.191.0.0/16)."
            )

        if not (self.DATABASE_URL and self.SYNC_DATABASE_URL):
            errors.append("DATABASE_URL/SYNC_DATABASE_URL (or DB_* secret parts) must resolve in production.")

        if errors:
            raise ValueError("Insecure production config:\n  - " + "\n  - ".join(errors))

    def _build_database_urls_from_secret_parts(self) -> None:
        if self.DATABASE_URL and self.SYNC_DATABASE_URL:
            return
        if not (self.DB_USER and self.DB_PASSWORD and self.DB_NAME and self.CLOUD_SQL_CONNECTION_NAME):
            return
        user = quote(self.DB_USER, safe="")
        password = quote(self.DB_PASSWORD, safe="")
        database = quote(self.DB_NAME, safe="")
        host = f"/cloudsql/{self.CLOUD_SQL_CONNECTION_NAME}"
        if not self.DATABASE_URL:
            self.DATABASE_URL = f"postgresql+asyncpg://{user}:{password}@/{database}?host={host}"
        if not self.SYNC_DATABASE_URL:
            self.SYNC_DATABASE_URL = f"postgresql://{user}:{password}@/{database}?host={host}"

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
    DATABASE_URL: str = ""
    SYNC_DATABASE_URL: str = ""
    DB_NAME: str = "reputation"
    DB_USER: str = "reputation"
    DB_PASSWORD: str = ""
    CLOUD_SQL_CONNECTION_NAME: str = ""
    # 연결 예산: Cloud SQL max_connections=100 (terraform/cloudsql.tf).
    # 인스턴스당 최대 pool+overflow 연결 × Cloud Run max instances 합이
    # max_connections를 넘으면 안 된다 (api 10 × (5+5) = 100 worst case —
    # worker/beat/migrate 여유분을 위해 인스턴스/풀 상향 시 pgbouncer 또는
    # max_connections 상향 선행). terraform/variables.tf api_max_instances 참조.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_TIMEOUT: int = 30  # seconds to wait for a connection
    DB_CONNECT_TIMEOUT: int = 10  # seconds to establish TCP connection
    DB_COMMAND_TIMEOUT: int = 30  # seconds for a single SQL statement (0=disabled)

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Anthropic — 콘텐츠 생성
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-sonnet-4-5"
    CLAUDE_MODEL_FAST: str = "claude-haiku-4-5-20251001"

    # Google Cloud — Imagen 3
    GCP_PROJECT_ID: str = ""
    GCP_LOCATION: str = "us-central1"
    GCP_STORAGE_BUCKET: str = "reputation-images"
    ASSET_LOCAL_UPLOAD_DIR: str = "/tmp/private_asset_uploads"

    # OpenAI — SoV
    OPENAI_API_KEY: str = ""
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
    # webhook SSRF 방어 — 허용 호스트(쉼표 구분). 기본은 Slack 공식 호스트만(V-013).
    SLACK_WEBHOOK_ALLOWED_HOSTS: str = "hooks.slack.com"

    # Report
    REPORT_OUTPUT_DIR: str = "/tmp/reports"
    GCS_REPORTS_BUCKET: str = "reputation-reports"

    # Sentry
    SENTRY_DSN: str = ""

    # Logging (OBS-1) — JSON for Cloud Logging in prod, readable text in dev.
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True

    # SoV
    SOV_REPEAT_COUNT: int = 10
    SOV_REPEAT_COUNT_WEEKLY: int = 5

    # Domain
    CNAME_TARGET: str = "aeo.motionlabs.io"

    # Admin
    ADMIN_BASE_URL: str = "http://localhost:3000"  # 🔴 CRITICAL: 환경변수로 분리 (.env에서 프로덕션 URL 설정)
    ADMIN_ACTOR_NAME: str = "AE"  # 세션 actor가 없을 때 쓰는 감사 로그 fallback

    # Site (public)
    SITE_BASE_URL: str = "https://reputation.co.kr"  # llms.txt absolute URL 등에 사용

    # Lead retention (개인정보보호법 제21조 — 보유기간)
    LEAD_RETENTION_DAYS: int = 180  # 수집 후 자동 파기까지 일수
    LEAD_CONSENT_VERSION: str = "v1.2026-05"  # 처리방침 버전 — 변경 시 재동의 필요

    # Public 폼 rate-limit
    PUBLIC_LEAD_RATE_LIMIT: str = "5/minute;30/hour;100/day"
    # Public 콘텐츠 허브 읽기 API rate-limit (병원/콘텐츠 조회 — 미인증 표면 보호, AUTH-2).
    # ISR 서버(단일 egress IP)와 브라우저(자산 직접 요청) 모두 수용하도록 넉넉히 설정.
    PUBLIC_SITE_RATE_LIMIT: str = "300/minute;6000/hour"

    # 발행 시 site(Vercel) sitemap·페이지 캐시 무효화. 빈 값이면 호출 생략.
    SITE_REVALIDATE_URL: str = ""
    SITE_REVALIDATE_SECRET: str = ""

    # Site BFF → backend 방문자 IP 전달 인증 (CDX-M1). site의 /api/leads BFF가 이 secret으로
    # 자신을 증명하면 X-Visitor-IP 헤더를 실제 클라이언트 IP로 채택한다(XFF 체인은 Vercel
    # egress hop에서 끊기므로). 빈 값이면 헤더 무시 — 기존 XFF right-to-left 파싱만 사용.
    SITE_BFF_SECRET: str = ""


settings = Settings()
