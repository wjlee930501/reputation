"""Shared slowapi limiter — imported by `main.py` and any router that needs it.

Defining the limiter in its own module avoids the circular import that would
otherwise arise from `main.py` importing routers that import the limiter.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.REDIS_URL,
    default_limits=["60/minute"],
)
