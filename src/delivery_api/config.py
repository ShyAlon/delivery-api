"""Env-driven config. Zero-cost posture: defaults target free tiers
(Supabase Free 500MB/5GB, Vercel Hobby). No paid infra without asking Shy."""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Shared local configuration; process environment still has precedence.
load_dotenv(override=False)

# Abuse protection (scoping doc section 5): per-run hard cap + global cap.
PER_RUN_BUDGET_USD = float(os.environ.get("DELIVERY_PER_RUN_BUDGET_USD", "6.00"))
GLOBAL_BUDGET_USD = float(os.environ.get("DELIVERY_GLOBAL_BUDGET_USD", "100.00"))
ADMIN_TOKEN = os.environ.get("DELIVERY_ADMIN_TOKEN", "dev-admin-token")  # link creation is Shy-only
def default_data_dir() -> Path:
    """Stable per-user data outside source and distribution trees."""
    if os.environ.get("XDG_DATA_HOME"):
        return Path(os.environ["XDG_DATA_HOME"]).expanduser() / "requi-re"
    return Path.home() / ".local" / "share" / "requi-re"

DEFAULT_DB_PATH = default_data_dir() / "delivery.db"
DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
DATABASE_URL = os.environ.get("DELIVERY_DATABASE_URL", os.environ.get("DELIVERY_DB_PATH", f"sqlite:///{DEFAULT_DB_PATH}"))
# Compatibility alias for older deployments; new code uses DATABASE_URL.
DB_PATH = DATABASE_URL
def local_database_path() -> Path:
    if DATABASE_URL.startswith("sqlite:///"): return Path(DATABASE_URL.removeprefix("sqlite:///"))
    if "://" not in DATABASE_URL:return Path(DATABASE_URL)
    return DEFAULT_DB_PATH
TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL", "").strip()
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
TURSO_SYNC_INTERVAL = int(os.environ.get("TURSO_SYNC_INTERVAL", "60"))
if bool(TURSO_DATABASE_URL) != bool(TURSO_AUTH_TOKEN):
    raise RuntimeError("Turso configuration is incomplete: set both TURSO_DATABASE_URL and TURSO_AUTH_TOKEN, or neither.")
ARTIFACT_DIR = os.environ.get("DELIVERY_ARTIFACT_DIR", "artifacts")

SESSION_SECRET = os.environ.get("DELIVERY_SESSION_SECRET", "")
BASIC_AUTH_ENABLED = os.environ.get("DELIVERY_BASIC_AUTH_ENABLED", "0") != "0"
PASSWORD_MIN_LENGTH = 12

SESSION_COOKIE = "vmodel_session"
SESSION_TTL_SECONDS = int(os.environ.get("DELIVERY_SESSION_TTL_SECONDS", "43200"))
COOKIE_SECURE = os.environ.get("DELIVERY_COOKIE_SECURE", "1") != "0"
PUBLIC_ORIGIN = os.environ.get("DELIVERY_PUBLIC_ORIGIN", "").rstrip("/")
GOOGLE_CLIENT_ID = os.environ.get("DELIVERY_GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("DELIVERY_GOOGLE_CLIENT_SECRET", "")
GITHUB_CLIENT_ID = os.environ.get("DELIVERY_GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("DELIVERY_GITHUB_CLIENT_SECRET", "")
ADMIN_EMAILS = {x.strip().lower() for x in os.environ.get("DELIVERY_ADMIN_EMAILS", "").split(",") if x.strip()}
DEFAULT_USER_TOKEN_QUOTA = int(os.environ.get("DELIVERY_DEFAULT_USER_TOKEN_QUOTA", "250000"))
AUTH_RATE_LIMIT = int(os.environ.get("DELIVERY_AUTH_RATE_LIMIT", "20"))
RUN_START_RATE_LIMIT = int(os.environ.get("DELIVERY_RUN_START_RATE_LIMIT", "10"))

def is_admin(email: str | None) -> bool:
    return bool(email and email.lower() in ADMIN_EMAILS)

def oauth_provider(name: str) -> dict:
    providers = {
      "google": {"client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth", "token_url": "https://oauth2.googleapis.com/token",
        "user_url": "https://openidconnect.googleapis.com/v1/userinfo", "scope": "openid email profile"},
      "github": {"client_id": GITHUB_CLIENT_ID, "client_secret": GITHUB_CLIENT_SECRET,
        "authorize_url": "https://github.com/login/oauth/authorize", "token_url": "https://github.com/login/oauth/access_token",
        "user_url": "https://api.github.com/user", "scope": "read:user user:email"},
    }
    if name not in providers or not providers[name]["client_id"] or not providers[name]["client_secret"]:
        raise ValueError(f"OAuth provider {name!r} is not configured")
    return providers[name]


def validate_auth_config() -> None:
    values=(SESSION_SECRET,GOOGLE_CLIENT_ID,GOOGLE_CLIENT_SECRET,GITHUB_CLIENT_ID,GITHUB_CLIENT_SECRET)
    if not any(values): return
    complete_google=bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
    complete_github=bool(GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET)
    if len(SESSION_SECRET) < 32 or not (complete_google or complete_github):
        raise RuntimeError("OAuth is partially configured: set a 32+ character DELIVERY_SESSION_SECRET and a complete Google or GitHub client id/secret pair; remove all OAuth variables for local mode.")
    if bool(GOOGLE_CLIENT_ID) != bool(GOOGLE_CLIENT_SECRET) or bool(GITHUB_CLIENT_ID) != bool(GITHUB_CLIENT_SECRET):
        raise RuntimeError("OAuth client id/secret pairs must be configured together.")

INTERVIEW_ENGINE_URL = os.environ.get("INTERVIEW_ENGINE_URL", "http://127.0.0.1:8010").rstrip("/")
