from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.api.admin import hospitals as admin_hospitals
from app.api.admin import content as admin_content
from app.api.admin import reports as admin_reports
from app.api.admin import sov as admin_sov
from app.api.admin import domain as admin_domain
from app.api.public import site as public_site
from app.core.config import settings
from app.core.security import verify_admin_key

if settings.SENTRY_DSN:
    import sentry_sdk
    sentry_sdk.init(dsn=settings.SENTRY_DSN, traces_sample_rate=0.1)

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.REDIS_URL,
    default_limits=["60/minute"],
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(
    title="Re:putation API",
    description="병원 특화 AEO 관리 플랫폼 — MotionLabs Inc.",
    version="0.2.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Admin-Key", "Authorization"],
)

# Admin 라우터: X-Admin-Key 인증 필수
admin_deps = [Depends(verify_admin_key)]
app.include_router(admin_hospitals.router, prefix="/api/v1", dependencies=admin_deps)
app.include_router(admin_content.router, prefix="/api/v1", dependencies=admin_deps)
app.include_router(admin_reports.router, prefix="/api/v1", dependencies=admin_deps)
app.include_router(admin_sov.router, prefix="/api/v1", dependencies=admin_deps)
app.include_router(admin_domain.router, prefix="/api/v1", dependencies=admin_deps)

# Public 라우터: 인증 불필요 (의도적)
app.include_router(public_site.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}
