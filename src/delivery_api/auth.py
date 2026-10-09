"""OAuth identities and signed server-side-session handles.

Only an opaque session id is stored in the cookie. Identity and expiry remain
server-side so logout/revocation takes effect immediately.
"""
from __future__ import annotations

import hashlib
import base64
import hmac
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from . import config
from .observability import event as activity


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
    return config.CLOUD_TENANTS or config.BASIC_AUTH_ENABLED or bool(config.SESSION_SECRET and (config.GOOGLE_CLIENT_ID or config.GITHUB_CLIENT_ID))

EMAIL_RE=re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
def normalize_email(value:str)->str:return value.strip().lower()
def valid_email(value:str)->bool:return len(value)<=254 and bool(EMAIL_RE.fullmatch(value.strip()))

def password_problem(password: str) -> str | None:
    if len(password) < config.PASSWORD_MIN_LENGTH:return f"Use at least {config.PASSWORD_MIN_LENGTH} characters."
    if not re.search(r"[A-Z]",password):return "Add an uppercase letter."
    if not re.search(r"[a-z]",password):return "Add a lowercase letter."
    if not re.search(r"[0-9]",password):return "Add a number."
    if not re.search(r"[^A-Za-z0-9]",password):return "Add a symbol."
    return None

def hash_password(password: str) -> str:
    salt=os.urandom(16);derived=hashlib.scrypt(password.encode(),salt=salt,n=2**14,r=8,p=1,dklen=32)
    return "scrypt$16384$"+base64.urlsafe_b64encode(salt).decode()+"$"+base64.urlsafe_b64encode(derived).decode()

def verify_password(password: str, stored: str) -> bool:
    try:_,n,salt,digest=stored.split("$");actual=hashlib.scrypt(password.encode(),salt=base64.urlsafe_b64decode(salt),n=int(n),r=8,p=1,dklen=32);return hmac.compare_digest(actual,base64.urlsafe_b64decode(digest))
    except (ValueError,TypeError):return False

def basic_signup(request: Request, username: str, password: str) -> tuple[Actor,str]:
    limiter.check("auth",_client_ip(request),config.AUTH_RATE_LIMIT)
    username=normalize_email(username)
    if not valid_email(username):raise HTTPException(400,{"error":"Email address is not valid.","cause":"Enter a complete address with a local part, @, and a dotted domain.","next":"Check for missing characters or spaces, then try again."})
    problem=password_problem(password)
    if problem:raise HTTPException(400,{"error":"Password needs more strength.","cause":problem,"next":"Use 12+ characters with uppercase, lowercase, a number, and a symbol."})
    try:user=request.app.state.store.create_basic_user(username,hash_password(password),config.DEFAULT_USER_TOKEN_QUOTA)
    except ValueError as e:raise HTTPException(409,{"error":"Username is unavailable.","cause":str(e),"next":"Choose another username, or sign in if this is your account."})
    return Actor(user["user_id"],user["label"]),_new_session(request,user)

def basic_signin(request: Request, username: str, password: str) -> tuple[Actor,str]:
    limiter.check("auth",_client_ip(request),config.AUTH_RATE_LIMIT);username=normalize_email(username);user=request.app.state.store.basic_user(username)
    if not user or not user.get("password_hash") or not verify_password(password,user["password_hash"]):raise HTTPException(401,{"error":"Sign-in did not work.","cause":"The username or password is incorrect.","next":"Check both fields and try again, or create a new account."})
    return Actor(user["user_id"],user["label"]),_new_session(request,user)

def _new_session(request: Request,user: dict) -> str:
    sid=secrets.token_urlsafe(32);request.app.state.store.create_session(sid,user["user_id"],time.time()+config.SESSION_TTL_SECONDS);activity("auth.session.created","Server session created",user_id=user["user_id"],expires_in_seconds=config.SESSION_TTL_SECONDS);return sid


def refresh_session(request: Request) -> tuple[str, float]:
    """Explicit rolling renewal. No extension for expired or revoked cookies."""
    if not enabled():
        raise HTTPException(400, {"error": "No sign-in session in local mode."})
    old_id = request.cookies.get(config.SESSION_COOKIE)
    if not old_id:
        raise HTTPException(401, {"error": "Sign in required.", "next": "Sign in, then retry."})
    now = time.time()
    new_id = secrets.token_urlsafe(32)
    expires_at = now + config.SESSION_TTL_SECONDS
    if not request.app.state.store.rotate_session(old_id, new_id, expires_at, now):
        raise HTTPException(401, {"error": "Session expired or revoked.", "next": "Sign in again."})
    activity("auth.session.refreshed", "Active session handle rotated", expires_in_seconds=config.SESSION_TTL_SECONDS)
    return new_id, expires_at


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def actor(request: Request, *, required: bool = False) -> Actor:
    if config.CLOUD_TENANTS:
        verified = getattr(request.state, "supabase_actor", None)
        if verified is not None:
            return verified
        if required:
            raise HTTPException(401, "Supabase sign-in required")
        return Actor("anonymous", "anonymous")
    if not enabled():
        return Actor("local", "local user", admin=True)
    sid = request.cookies.get(config.SESSION_COOKIE)
    row = request.app.state.store.session(sid) if sid else None
    if not row or row["expires_at"] <= time.time():
        if sid:activity("auth.session.expired","Session handle was missing or expired",path=request.url.path,client=_client_ip(request))
        if required:
            activity("access.blocked","Authentication required",path=request.url.path,client=_client_ip(request))
            raise HTTPException(401,{"error":"Sign in required.","cause":"Your session is missing or expired.","next":"Sign in, then retry this action."})
        return Actor("anonymous", "anonymous")
    return Actor(row["user_id"], row["label"], row.get("email"), bool(row.get("is_admin")))


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
    OAuth callbacks are separately bound by one-use state.
    """
    if not enabled() or request.method in {"GET", "HEAD", "OPTIONS"}: return
    origin = request.headers.get("origin")
    if config.CLOUD_TENANTS and request.url.path.startswith('/auth/') and not origin:
        raise HTTPException(403,'Authentication requires same-origin request')
    if (request.cookies.get(config.SESSION_COOKIE) or request.cookies.get('requi_access') or request.cookies.get('requi_refresh')) and not origin:
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
