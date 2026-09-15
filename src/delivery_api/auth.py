"""OAuth identities and signed server-side-session handles.

Only an opaque session id is stored in the cookie. Identity and expiry remain
server-side so logout/revocation takes effect immediately.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

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
                raise HTTPException(429, {"error": "Too many requests.", "cause": f"The {bucket} rate limit was reached.", "next": f"Wait {window} seconds, then try again."})
            hits.append(now); self._hits[(bucket, key)] = hits


limiter = RateLimiter()


def enabled() -> bool:
    return bool(config.SESSION_SECRET and (config.GOOGLE_CLIENT_ID or config.GITHUB_CLIENT_ID))


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def actor(request: Request, *, required: bool = False) -> Actor:
    if not enabled():
        return Actor("local", "local user", admin=True)
    sid = request.cookies.get(config.SESSION_COOKIE)
    row = request.app.state.store.session(sid) if sid else None
    if not row or row["expires_at"] <= time.time():
        if required: raise HTTPException(401, "Sign in with Google or GitHub to perform this action.")
        return Actor("anonymous", "anonymous")
    return Actor(row["user_id"], row["label"], row.get("email"), bool(row.get("is_admin")))


def require_actor(request: Request) -> Actor:
    limiter.check("run-start", _client_ip(request), config.RUN_START_RATE_LIMIT)
    return actor(request, required=True)


def require_admin(request: Request) -> Actor:
    a = actor(request, required=True)
    if not a.admin: raise HTTPException(403, "Administrator access is required.")
    return a


def csrf_guard(request: Request) -> None:
    """Reject cross-site state changes in authenticated mode.

    SameSite=Lax cookies plus strict Origin matching protect ordinary forms and
    fetch calls without exposing a bearer CSRF token to browser JavaScript.
    OAuth callbacks are separately bound by one-use state.
    """
    if not enabled() or request.method in {"GET", "HEAD", "OPTIONS"}: return
    origin = request.headers.get("origin")
    if request.cookies.get(config.SESSION_COOKIE) and not origin:
        raise HTTPException(403, "Authenticated state changes require a same-origin request.")
    if origin and origin.rstrip("/") != (config.PUBLIC_ORIGIN or str(request.base_url)).rstrip("/"):
        raise HTTPException(403, "Cross-site request rejected.")


def oauth_start(request: Request, provider: str):
    limiter.check("auth", _client_ip(request), config.AUTH_RATE_LIMIT)
    p = config.oauth_provider(provider)
    state = secrets.token_urlsafe(32)
    request.app.state.store.create_oauth_state(hashlib.sha256(state.encode()).hexdigest(), provider, time.time() + 600)
    callback = (config.PUBLIC_ORIGIN + f"/auth/{provider}/callback") if config.PUBLIC_ORIGIN else str(request.url_for("oauth_callback", provider=provider))
    url = httpx.URL(p["authorize_url"], params={"client_id": p["client_id"], "redirect_uri": callback,
        "response_type": "code", "scope": p["scope"], "state": state})
    return RedirectResponse(str(url), status_code=302)


def oauth_callback(request: Request, provider: str, code: str, state: str):
    limiter.check("auth", _client_ip(request), config.AUTH_RATE_LIMIT)
    p = config.oauth_provider(provider)
    digest = hashlib.sha256(state.encode()).hexdigest()
    if not request.app.state.store.consume_oauth_state(digest, provider, time.time()):
        raise HTTPException(400, "OAuth state is invalid or expired. Start sign-in again.")
    callback = (config.PUBLIC_ORIGIN + f"/auth/{provider}/callback") if config.PUBLIC_ORIGIN else str(request.url_for("oauth_callback", provider=provider))
    token = httpx.post(p["token_url"], data={"client_id": p["client_id"], "client_secret": p["client_secret"], "code": code, "redirect_uri": callback}, headers={"Accept": "application/json"}, timeout=10).json()
    if not token.get("access_token"): raise HTTPException(401, "OAuth provider did not issue an access token.")
    api_headers={"Authorization": f"Bearer {token['access_token']}", "Accept": "application/json"}
    profile = httpx.get(p["user_url"], headers=api_headers, timeout=10).json()
    external = str(profile["sub"] if provider == "google" else profile["id"])
    email = profile.get("email")
    if provider == "github" and not email:
        emails=httpx.get("https://api.github.com/user/emails",headers=api_headers,timeout=10).json()
        verified=[x for x in emails if x.get("verified")]
        email=next((x.get("email") for x in verified if x.get("primary")),None) or next((x.get("email") for x in verified),None)
    label = email or profile.get("name") or profile.get("login") or f"{provider}:{external}"
    user = request.app.state.store.upsert_user(provider, external, email, label, config.DEFAULT_USER_TOKEN_QUOTA, config.is_admin(email))
    sid = secrets.token_urlsafe(32)
    request.app.state.store.create_session(sid, user["user_id"], time.time() + config.SESSION_TTL_SECONDS)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(config.SESSION_COOKIE, sid, max_age=config.SESSION_TTL_SECONDS, httponly=True, secure=config.COOKIE_SECURE, samesite="lax", path="/")
    return response
