import json
import logging
import os
from typing import Annotated
from urllib.parse import quote, urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = logging.getLogger(__name__)

# 프로덕션 부팅 시 비어 있으면 즉시 실패(fail-fast)시키는 시크릿.
# SLACK_WEBHOOK_URL 포함: 모든 주요 이벤트 알림이 Slack로 나가므로(CLAUDE.md), 누락 시
# V0/콘텐츠/월간 리포트 알림이 조용히 사라진다 → AE가 운영 이벤트를 놓친다.
_CRITICAL_PRODUCTION_SECRETS = ("ADMIN_SECRET_KEY", "SLACK_WEBHOOK_URL")


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
        except Exception as exc:  # noqa: BLE001 — 조회 실패해도 env 폴백으로 부팅은 계속.
            logger.warning(
                "Secret Manager 조회 실패 — env/기본값으로 폴백: secret=%s error=%s",
                name,
                exc,
            )
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
            # Jina는 무인증 free tier가 지원되는 선택 기능이고 Terraform도 이
            # secret을 소유하지 않는다. 존재하지 않는 secret을 런타임 SA로 매번
            # 조회하면 정상 부팅마다 IAM 403 경고가 남으므로, 키가 필요할 때만
            # Cloud Run env/secret mount로 명시적으로 주입한다.
            self.SLACK_WEBHOOK_URL = _resolve_secret("SLACK_WEBHOOK_URL", self.SLACK_WEBHOOK_URL)
            self.DB_PASSWORD = _resolve_secret("DB_PASSWORD", self.DB_PASSWORD)
            self._build_database_urls_from_secret_parts()
            self.SITE_REVALIDATE_SECRET = _resolve_secret(
                "SITE_REVALIDATE_SECRET", self.SITE_REVALIDATE_SECRET
            )
            self.SITE_BFF_SECRET = _resolve_secret("SITE_BFF_SECRET", self.SITE_BFF_SECRET)
            self._fail_if_critical_production_secrets_empty()
            self._validate_production_config()
            self._warn_if_production_flow_config_incomplete()

    def _warn_if_production_flow_config_incomplete(self) -> None:
        """프로덕션 부팅 시 핵심 플로우 설정이 비어 있거나 placeholder면 경고(중단하지 않음).

        fail-fast 대상은 아니지만(보안 표면과 무관), 비어 있으면 해당 플로우가 조용히
        실패하므로 부팅 로그에 영향 범위를 남긴다.
        """
        flow_impact = {
            "ANTHROPIC_API_KEY": "콘텐츠 자동 생성(Claude Sonnet) 중단",
            "OPENAI_API_KEY": "SoV 측정(ChatGPT) + 대표 이미지 생성(gpt-image) 중단",
            "GEMINI_API_KEY": "SoV 측정(Gemini) 중단",
        }
        for name, impact in flow_impact.items():
            if not str(getattr(self, name, "")).strip():
                logger.warning("프로덕션 시크릿 %s 미설정 — %s", name, impact)

        # GCS 버킷 placeholder 감지 — 버킷명은 전역 유일 제약 때문에 기본값
        # 'reputation-images'/'reputation-reports'가 실제 소유 버킷일 수 없다.
        # 규칙: '<name>-<GCP_PROJECT_ID>' (terraform storage.tf / setup-gcp.sh와 동일).
        if self.GCP_STORAGE_BUCKET.strip() == "reputation-images":
            logger.warning(
                "GCP_STORAGE_BUCKET가 placeholder 기본값 'reputation-images' — 실제 버킷명 "
                "'reputation-images-<GCP_PROJECT_ID>'로 설정 필요(콘텐츠 이미지 업로드 실패 위험)."
            )
        if self.GCS_REPORTS_BUCKET.strip() == "reputation-reports":
            logger.warning(
                "GCS_REPORTS_BUCKET가 placeholder 기본값 'reputation-reports' — 실제 버킷명 "
                "'reputation-reports-<GCP_PROJECT_ID>'로 설정 필요(PDF 리포트 업로드 실패 위험)."
            )

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
            errors.append(
                "ALLOWED_ORIGINS must be set (CORS with credentials cannot use a wildcard)."
            )
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
            errors.append(
                "DATABASE_URL/SYNC_DATABASE_URL (or DB_* secret parts) must resolve in production."
            )
        self._validate_production_redis_url(errors)

        if not self.OPENAI_CHATGPT_USE_WEB_SEARCH:
            errors.append(
                "OPENAI_CHATGPT_USE_WEB_SEARCH must be true in production — model recall is not "
                "ChatGPT Search exposure."
            )
        if not self.CERTIFICATE_MANAGER_AUTO_PROVISION:
            errors.append(
                "CERTIFICATE_MANAGER_AUTO_PROVISION must be true in production — otherwise new "
                "custom domains require an out-of-band Terraform change."
            )

        self._validate_external_https_url("ADMIN_BASE_URL", self.ADMIN_BASE_URL, errors)
        self._validate_external_https_url("SITE_BASE_URL", self.SITE_BASE_URL, errors)

        # 리드 진단 시크릿은 dev 기본값으로 뜨면 안 된다.
        # LEAD_LOCK_HASH_PEPPER가 기본값이면 전화번호(공간이 좁다)가 무지개표로 역산되고,
        # LEAD_REPORT_TOKEN_SECRET이 기본값이면 누구나 남의 리포트 링크를 위조할 수 있다.
        for name, dev_default in (
            ("LEAD_LOCK_HASH_PEPPER", "dev-lock-pepper-change-me"),
            ("LEAD_REPORT_TOKEN_SECRET", "dev-report-secret-change-me"),
        ):
            value = str(getattr(self, name, "")).strip()
            if not value or value == dev_default:
                errors.append(f"{name} must be set to a production secret (dev default detected).")

        if errors:
            raise ValueError("Insecure production config:\n  - " + "\n  - ".join(errors))

    @staticmethod
    def _validate_external_https_url(name: str, value: str, errors: list[str]) -> None:
        stripped = value.strip()
        if not stripped:
            errors.append(f"{name} must be set in production.")
            return

        parsed = urlparse(stripped)
        if parsed.scheme != "https" or not parsed.netloc:
            errors.append(f"{name} must be an absolute https:// URL in production: {stripped}")
            return

        hostname = (parsed.hostname or "").lower()
        if hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(".localhost"):
            errors.append(f"{name} must not point to localhost in production: {stripped}")

    def _validate_production_redis_url(self, errors: list[str]) -> None:
        stripped = self.REDIS_URL.strip()
        if not stripped:
            errors.append("REDIS_URL must be set in production.")
            return

        parsed = urlparse(stripped)
        if parsed.scheme not in {"redis", "rediss"} or not parsed.netloc:
            errors.append(
                f"REDIS_URL must be an absolute redis:// or rediss:// URL in production: {stripped}"
            )
            return

        hostname = (parsed.hostname or "").lower()
        if hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(".localhost"):
            errors.append(f"REDIS_URL must not point to localhost in production: {stripped}")

    def _build_database_urls_from_secret_parts(self) -> None:
        if self.DATABASE_URL and self.SYNC_DATABASE_URL:
            return
        if not (
            self.DB_USER and self.DB_PASSWORD and self.DB_NAME and self.CLOUD_SQL_CONNECTION_NAME
        ):
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
    # 연결 예산 (scripts/check_db_connection_budget.py가 CI에서 강제):
    #   API(async)  : api_max_instances × (DB_POOL_SIZE + DB_MAX_OVERFLOW)
    #                 = 7 × (3 + 2) = 35
    #   Worker(sync): worker_max_instances × CELERY_CONCURRENCY
    #                 × (DB_WORKER_POOL_SIZE + DB_WORKER_MAX_OVERFLOW)
    #                 = 5 × 2 × (2 + 2) = 40
    #   합계 75 ≤ Cloud SQL max_connections(100) × 0.8 = 80.
    #   최소 20개 연결은 운영/마이그레이션/일시적 롤아웃 중첩을 위해 남긴다.
    #   (beat는 DB 미사용, migrate Job은 배포 전 단발 실행이라 피크와 겹치지 않음.)
    #   sync 엔진은 Celery prefork 자식마다 lazily 생성된다(database.py) — 그래서
    #   워커 풀은 인스턴스가 아니라 자식(concurrency) 단위로 곱해진다.
    #   인스턴스/풀/concurrency 상향 시 pgbouncer나 max_connections 상향을 선행하고
    #   위 스크립트로 불변식을 재확인할 것.
    # terraform/variables.tf(api_max_instances, worker_max_instances),
    # terraform/cloudrun.tf(CELERY_CONCURRENCY), terraform/cloudsql.tf(max_connections) 참조.
    DB_POOL_SIZE: int = 3  # API(async) 엔진 풀 크기
    DB_MAX_OVERFLOW: int = 2  # API(async) 오버플로 한도
    DB_WORKER_POOL_SIZE: int = 2  # Worker(sync) 엔진 풀 — Celery prefork 자식당
    DB_WORKER_MAX_OVERFLOW: int = 2  # Worker(sync) 오버플로 — Celery prefork 자식당
    DB_POOL_TIMEOUT: int = 30  # seconds to wait for a connection
    DB_CONNECT_TIMEOUT: int = 10  # seconds to establish TCP connection
    DB_COMMAND_TIMEOUT: int = 30  # seconds for a single SQL statement (0=disabled)

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Anthropic — 콘텐츠 생성
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-sonnet-4-5"
    CLAUDE_MODEL_FAST: str = "claude-haiku-4-5-20251001"

    # Jina Reader — 프로파일 자동 채우기 시 네이버 플레이스 등 봇 차단 사이트 우회 읽기.
    # 선택값: 비어 있어도 무인증 free tier로 동작(분당 제한 빡빡). 키가 있으면 상향.
    JINA_API_KEY: str = ""

    # Google Cloud — Imagen 3 (이미지 폴백)
    GCP_PROJECT_ID: str = ""
    GCP_LOCATION: str = "us-central1"
    GCP_STORAGE_BUCKET: str = "reputation-images"
    ASSET_LOCAL_UPLOAD_DIR: str = "/tmp/private_asset_uploads"
    # Certificate Manager 기반 신규 커스텀 도메인 자동 프로비저닝.
    # 실제 서비스는 Terraform이 소유한 map 안에 병원별 cert/map entry만 추가한다.
    CERTIFICATE_MANAGER_AUTO_PROVISION: bool = False
    CERTIFICATE_MANAGER_LOCATION: str = "global"
    CERTIFICATE_MAP_NAME: str = "reputation-certmap"

    # 콘텐츠 대표 이미지 생성기
    #   "openai" → gpt-image-2 (기본, editorial 일러스트·항목별 다양성)
    #   "imagen" → Vertex AI Imagen 3 폴백
    IMAGE_PROVIDER: str = "openai"
    OPENAI_IMAGE_MODEL: str = "gpt-image-2"
    OPENAI_IMAGE_SIZE: str = "1536x864"  # 16:9 (16의 배수, 비율≤3:1) — 카드 레이아웃 일치
    OPENAI_IMAGE_QUALITY: str = "high"  # low|medium|high

    # OpenAI — SoV
    OPENAI_API_KEY: str = ""
    # 두 모델 모두 **날짜 스냅샷으로 고정**한다. 부동 별칭(gpt-5-mini, gpt-4o-mini)은
    # OpenAI가 갱신하면 측정 기준선이 조용히 이동해, 언급률 변화가 플랫폼 탓인지
    # 우리 측정 도구 탓인지 구분할 수 없게 된다.
    #
    # OPENAI_MODEL_QUERY  = 답변 모델(측정 대상). 소비자가 보는 답변을 재현한다.
    #   mini 티어는 ChatGPT가 환자에게 주는 모델이 아니다 — 역할 정의상 최신 정식 모델을 쓴다.
    #   2026-07-29 실측(지역 의도 질문 10회, 웹검색 강제):
    #     gpt-5.6-luna        p50 24.7s / p90 34.8s   ← 채택
    #     gpt-5-mini-...08-07 p50 62.8s / p90 86.8s   (구세대인데 2.5배 느림)
    #   비용은 병원당 월 약 $9 증가(측정 호출 기준). 요금제 대비 1% 미만.
    # OPENAI_MODEL_PARSE  = 판정 모델(측정 도구=자). 답변 모델과 의도적으로 분리하며,
    #      기준선 유지를 위해 바꾸지 않는다. 폐기 예정 없음.
    OPENAI_MODEL_QUERY: str = "gpt-5.6-luna"
    OPENAI_MODEL_PARSE: str = "gpt-4o-mini-2024-07-18"
    # 프로덕션은 Responses API + web_search tool만 허용한다. False는 모델 recall이므로
    # _validate_production_config에서 부팅을 차단한다.
    OPENAI_CHATGPT_USE_WEB_SEARCH: bool = True

    # Gemini — SoV
    GEMINI_API_KEY: str = ""
    # 답변 모델이므로 OpenAI와 동일하게 **버전 고정**한다. `gemini-flash-latest`는
    # 부동 별칭이라 Google이 갱신하면 기준선이 조용히 이동한다(2026-07-29 확인 시
    # gemini-3.6-flash로 해석됨). 측정 기준선을 고정하는 것이 별칭의 최신성보다 중요하다.
    GEMINI_MODEL: str = "gemini-3.6-flash"

    # Cost Guard — 전역 비용 가드레일 + 킬스위치.
    # 콘텐츠/이미지/SoV 호출은 병원 수에 비례해 무제한 확장되므로 카테고리별 일/월 호출
    # 상한과 킬스위치로 지출 폭주를 차단한다. 기본값은 병원 50개 × 요금제 상한 × 재시도
    # 여유를 감안한 대략치이며, 실제 계약 규모에 맞춰 조정한다.
    COST_GUARD_ENABLED: bool = True
    # 월간 상한 (병원50 × 리더 20편 × 재시도 여유 ≈ 3000)
    COST_GUARD_MONTHLY_CONTENT_CALLS: int = 3000
    COST_GUARD_MONTHLY_IMAGE_CALLS: int = 3000
    # SoV: 병원50 × 주간 spec 다수 × 4주 여유 ≈ 20000
    COST_GUARD_MONTHLY_SOV_QUERIES: int = 20000
    # 일일 상한 = 월간의 1/10 수준(피크 하루 폭주 차단용)
    COST_GUARD_DAILY_CONTENT_CALLS: int = 250
    COST_GUARD_DAILY_IMAGE_CALLS: int = 250
    COST_GUARD_DAILY_SOV_QUERIES: int = 2000
    # 무료 진단(1단) 답변 호출 상한. **선착순 자리 수는 호출 상한이 아니다** —
    # 자리 20개 × 질의3 × 플랫폼2 × 반복3 = 360건이 정상 상한이고, 측정 재시도(최대 3회)가
    # 겹치면 그 3배까지 늘어난다. 일일 500은 정상분 360 + 복구 여유이며, 이 선을 넘는
    # 상황은 수요가 아니라 재시도 폭주이므로 멈추는 것이 맞다.
    COST_GUARD_DAILY_LEADGEN_CALLS: int = 500
    # 월간: 정상분 360/일 × 30일 = 10,800 + 여유
    COST_GUARD_MONTHLY_LEADGEN_CALLS: int = 12000

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
    # 주간 측정 반복 횟수. 월간 리포트는 새로 측정하지 않고 이 기록을 집계하므로
    # 이 값이 곧 원장 보고 숫자의 표본 크기다.
    # (SOV_REPEAT_COUNT라는 '월간용' 설정이 있었으나 앱이 한 번도 읽지 않는 죽은 값이라
    #  제거했다 — 월간 측정이라는 것이 애초에 없다.)
    SOV_REPEAT_COUNT_WEEKLY: int = 5
    # 주간 측정에서 HIGH 우선순위 쿼리 상한 — 초과분은 잘라내고 ops 알림 (비용 가드)
    SOV_HIGH_PRIORITY_CAP: int = 30
    # 우선순위와 무관한 주간 측정 spec 총상한 — NORMAL/LOW 증가에 따른 무제한 비용 방지
    SOV_TOTAL_SPEC_CAP: int = 100

    # Domain
    CNAME_TARGET: str = "cname.reputation.motionlabs.kr"
    CUSTOM_DOMAIN_IP_TARGETS: str = ""

    # Admin
    ADMIN_BASE_URL: str = (
        "http://localhost:3000"  # 🔴 CRITICAL: 환경변수로 분리 (.env에서 프로덕션 URL 설정)
    )
    ADMIN_ACTOR_NAME: str = "AE"  # 세션 actor가 없을 때 쓰는 감사 로그 fallback
    # X-Admin-Actor가 활성 AdminUser와 매칭되지 않는데도 쓰기 요청이 들어오면 거부한다.
    # 기본 False — 헤더를 아예 보내지 않는 배치/시스템 호출(default_actor 폴백)은 이 옵션과
    # 무관하며, 켜더라도 영향받지 않는다. 운영 중 Admin BFF가 검증된 이메일을 항상 붙이는
    # 것이 확인된 뒤 켜서, 비활성화된 계정의 쓰기를 백엔드에서 실제로 끊는 용도.
    ADMIN_REJECT_UNVERIFIED_ACTOR: bool = False

    # Site (public)
    SITE_BASE_URL: str = "https://reputation.motionlabs.kr"  # llms.txt absolute URL 등에 사용

    # IndexNow — 발행 즉시 검색 색인에 알린다.
    # sitemap은 크롤러가 오기를 기다리는 수동 신호라 색인까지 지연·누락이 생긴다.
    # Bing 인덱스는 OpenAI 웹검색이 참조하므로, 여기 들어가야 AI 답변에 인용될 수 있다.
    # 키는 site/app/indexnow-key.txt 라우트가 같은 값을 응답해야 한다(호스트 소유 증명).
    INDEXNOW_ENABLED: bool = True
    INDEXNOW_KEY: str = ""  # 미설정이면 제출을 건너뛴다(발행은 정상 진행)

    # Lead retention (개인정보보호법 제21조 — 보유기간)
    LEAD_RETENTION_DAYS: int = 180  # 수집 후 자동 파기까지 일수
    LEAD_CONSENT_VERSION: str = "v1.2026-05"  # 처리방침 버전 — 변경 시 재동의 필요

    # ── 리드마그넷 무료 진단 (1단). 설계: docs/plans/2026-07-29-lead-diagnosis-funnel-design.md
    # 하루 자리 수. **이 값이 곧 예산 상한이다** — 건당 최악 1,600원이므로 20건이면
    # 하루 32,000원으로 수학적으로 묶인다. 그래서 cost_guard에 별도 카테고리를 두지 않는다.
    # 랜딩의 "오늘 남은 자리 N/20"에 그대로 노출되는 마케팅 숫자이기도 하다.
    LEADGEN_DAILY_SLOTS: int = 20
    # 질의 3개 고정. 5개는 건당 2,498원으로 상한까지 여유가 4%뿐이라 재시도 몇 번에 넘긴다.
    LEADGEN_QUERY_COUNT: int = 3
    LEADGEN_REPEAT_COUNT: int = 3
    # 무료 진단 전용 공급자 동시성. sov_engine의 전역 세마포어를 그대로 쓰면 유료 측정과
    # 경합하므로(PRD F6-1) 풀을 분리한다. 실제 값은 출시 전 20건 동시 주입 부하 시험으로 확정.
    LEADGEN_PROVIDER_CONCURRENCY: int = 15
    # 질의 단위 공유 캐시 수명. 질의에 병원명이 없어 답변이 병원 무관이므로 공유 가능하다.
    # 길게 잡을수록 싸지지만 리포트가 오래된 스냅샷이 된다 — 측정 일시는 항상 원본을 표기한다.
    LEADGEN_QUERY_CACHE_TTL_DAYS: int = 7
    # 잠금 해시 pepper. 유출된 DB만으로 전화번호를 역산하지 못하게 한다(번호 공간이 좁다).
    # **바뀌면 기존 잠금이 전부 풀린다** — 로테이션 금지, Secret Manager 고정값.
    LEAD_LOCK_HASH_PEPPER: str = "dev-lock-pepper-change-me"
    # 리포트/상태 페이지 서명 키. Admin 세션 HMAC과 audience가 다르다(PRD F5-4).
    LEAD_REPORT_TOKEN_SECRET: str = "dev-report-secret-change-me"
    LEAD_REPORT_TOKEN_TTL_DAYS: int = 30
    # 리포트 발송 (Resend). 비어 있으면 발송을 시도하지 않고 delivery 행에 사유를 남긴다 —
    # 조용히 성공 처리하면 "발송 완료"인데 아무도 못 받는 상태가 된다.
    RESEND_API_KEY: str = ""
    LEAD_MAIL_FROM: str = "Re:putation <noreply@reputation.motionlabs.kr>"
    LEAD_MAIL_REPLY_TO: str = ""

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
