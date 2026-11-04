"""Signed tenant identity, request rate limits and same-origin checks."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

from . import config


@dataclass(frozen=True)
class Actor:
    id: str
    label: str
    email: str | None = None
    admin: bool = False


class RateLimiter:
    def __init__(self):
        self._hits: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    def check(self, bucket: str, key: str, limit: int, window: int = 60) -> None:
        now = time.time()
        with self._lock:
            hits = [t for t in self._hits.get((bucket, key), []) if t > now - window]
            if len(hits) >= limit:
                raise HTTPException(429, {"error": "Too many requests.", "cause": f"The {bucket} rate limit was reached.", "next": f"Wait {window} seconds, then try again."}, headers={"Retry-After": str(window)})
            hits.append(now); self._hits[(bucket, key)] = hits


limiter = RateLimiter()


def enabled() -> bool:
    return True

def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def actor(request: Request, *, required: bool = False) -> Actor:
    verified = getattr(request.state, "supabase_actor", None)
    if verified is not None:
        from . import ops_health
        ops_health.touch(verified.id,getattr(verified,"label",None),getattr(verified,"email",None))   # who is signed in right now, for the operator health page
        return verified
    if required:
        raise HTTPException(401, "Supabase sign-in required")
    return Actor("anonymous", "anonymous")


def require_actor(request: Request) -> Actor:
    """Authenticate an actor without charging an unrelated action bucket."""
    return actor(request, required=True)


def require_run_start(request: Request) -> Actor:
    """Authenticate and rate-limit only operations that start model work."""
    a = actor(request, required=True)
    limiter.check("run-start", _client_ip(request), config.RUN_START_RATE_LIMIT)
    return a


def require_admin(request: Request) -> Actor:
    a = actor(request, required=True)
    if not a.admin: raise HTTPException(403, "Administrator access is required.")
    return a


def csrf_guard(request: Request) -> None:
    """Reject cross-site state changes in authenticated mode.

    SameSite=Lax cookies plus strict Origin matching protect ordinary forms and
    fetch calls without exposing a bearer CSRF token to browser JavaScript.
    """
    if not enabled() or request.method in {"GET", "HEAD", "OPTIONS"}: return
    origin = request.headers.get("origin")
    if request.url.path.startswith('/auth/') and not origin:
        raise HTTPException(403,'Authentication requires same-origin request')
    if (request.cookies.get(config.SESSION_COOKIE) or request.cookies.get('requi_access') or request.cookies.get('requi_refresh')) and not origin:
        raise HTTPException(403, "Authenticated state changes require a same-origin request.")
    if origin and origin.rstrip("/") != (config.PUBLIC_ORIGIN or str(request.base_url)).rstrip("/") and origin.rstrip("/") not in config.ALT_ORIGINS:
        raise HTTPException(403, {"error": "Cross-site request rejected.", "cause": "This page was opened from a different address than the server expects.", "next": "Open the app from its official address and try again. If it keeps happening, tell the operator."})
