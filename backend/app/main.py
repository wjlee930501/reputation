from contextlib import asynccontextmanager
import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response

from app.api.admin import hospitals as admin_hospitals
from app.api.admin import content as admin_content
from app.api.admin import reports as admin_reports
from app.api.admin import sov as admin_sov
from app.api.admin import query_targets as admin_query_targets
from app.api.admin import domain as admin_domain
from app.api.admin import essence as admin_essence
from app.api.admin import exposure_actions as admin_exposure_actions
from app.api.admin import leads as admin_leads
from app.api.admin import operations as admin_operations
from app.api.public import site as public_site
from app.api.public import leads as public_leads
from app.core.config import settings
from app.core.rate_limit import limiter
from app.core.security import verify_admin_key, verify_admin_rate_limit

if settings.SENTRY_DSN:
    import sentry_sdk
    sentry_sdk.init(dsn=settings.SENTRY_DSN, traces_sample_rate=0.1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(
    title="Re:putation API",
    description="병원이 ChatGPT·Gemini 답변에서 더 잘 이해되고 언급되도록 돕는 운영 플랫폼 — MotionLabs Inc.",
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.APP_ENV == "development" else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.APP_ENV == "development" else None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if settings.APP_ENV != "development":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


app.add_middleware(SecurityHeadersMiddleware)


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


app.add_middleware(RequestIdMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Admin-Key", "Authorization"],
)

# Admin 라우터: X-Admin-Key 인증 + rate limit 필수
admin_deps = [Depends(verify_admin_key), Depends(verify_admin_rate_limit)]
app.include_router(admin_hospitals.router, prefix="/api/v1", dependencies=admin_deps)
app.include_router(admin_content.router, prefix="/api/v1", dependencies=admin_deps)
app.include_router(admin_reports.router, prefix="/api/v1", dependencies=admin_deps)
app.include_router(admin_sov.router, prefix="/api/v1", dependencies=admin_deps)
app.include_router(admin_query_targets.router, prefix="/api/v1", dependencies=admin_deps)
app.include_router(admin_domain.router, prefix="/api/v1", dependencies=admin_deps)
app.include_router(admin_essence.router, prefix="/api/v1", dependencies=admin_deps)
app.include_router(admin_exposure_actions.router, prefix="/api/v1", dependencies=admin_deps)
app.include_router(admin_operations.router, prefix="/api/v1", dependencies=admin_deps)
app.include_router(admin_leads.router, prefix="/api/v1", dependencies=admin_deps)

# Public 라우터: 인증 불필요 (의도적)
app.include_router(public_site.router, prefix="/api/v1")
app.include_router(public_leads.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness():
    """Cloud Run readiness probe — DB 연결 확인 포함."""
    from app.core.database import get_async_sessionmaker

    try:
        sessionmaker = get_async_sessionmaker()
        async with sessionmaker() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        return {"status": "error", "database": str(e)}


@app.get("/health/live")
async def liveness():
    """Cloud Run liveness probe — 기본 응답."""
    return {"status": "ok"}
