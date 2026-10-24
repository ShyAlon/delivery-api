"""Env-driven config. Zero-cost posture: defaults target free tiers
(Supabase Free 500MB/5GB, Vercel Hobby). No paid infra without asking Shy."""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Shared local configuration; process environment still has precedence.
load_dotenv(override=False)

# Abuse protection (scoping doc section 5): global cap.
GLOBAL_BUDGET_USD = float(os.environ.get("DELIVERY_GLOBAL_BUDGET_USD", "100.00"))
ADMIN_TOKEN = os.environ.get("DELIVERY_ADMIN_TOKEN", "dev-admin-token")  # link creation is Shy-only
def default_data_dir() -> Path:
    """Stable per-user data outside source and distribution trees."""
    if os.environ.get("XDG_DATA_HOME"):
        return Path(os.environ["XDG_DATA_HOME"]).expanduser() / "requi-re"
    return Path.home() / ".local" / "share" / "requi-re"

DEFAULT_DB_PATH = default_data_dir() / "delivery.db"
# Configuration import is read-only. The selected SQLiteStore creates only its
# actual parent when opened; cloud/override users must not touch the default home.
DATABASE_URL = os.environ.get("DELIVERY_DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")
SUPABASE_URL = os.environ.get("DELIVERY_SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("DELIVERY_SUPABASE_ANON_KEY", "")
ARTIFACT_PUBLISH = os.environ.get("DELIVERY_ARTIFACT_PUBLISH", "").strip() == "1"  # PR B: publish manifest revisions after produce; default OFF
ARTIFACT_MANIFEST_READS = os.environ.get("DELIVERY_ARTIFACT_MANIFEST_READS", "").strip() == "1"  # PR B: manifest-backed artifact reads; default OFF until approved enable
ARTIFACT_RESTORE = os.environ.get("DELIVERY_ARTIFACT_RESTORE", "").strip() == "1"  # PR C: restore completed-phase artifacts onto this host on read; default OFF
CLOUD_TENANTS = DATABASE_URL.startswith(("postgres://", "postgresql://"))
if CLOUD_TENANTS and (not SUPABASE_URL or not SUPABASE_ANON_KEY):
    raise RuntimeError("Postgres requires Supabase Auth URL and public anon/publishable key; no local-auth fallback.")
if CLOUD_TENANTS and os.environ.get("TURSO_DATABASE_URL"):
    raise RuntimeError("Cloud tenant control plane cannot use a Turso replica/fallback.")
SUPABASE_ADMIN_SUBJECTS = {x.strip() for x in os.environ.get("DELIVERY_SUPABASE_ADMIN_SUBJECTS", "").split(",") if x.strip()}
# The operator, held in code. Admin when the signed login token carries this email (sign-in refuses unconfirmed emails).
OPERATOR_EMAIL = "shy.alon@gmail.com"

def local_database_path() -> Path:
    if DATABASE_URL.startswith("sqlite:///"): return Path(DATABASE_URL.removeprefix("sqlite:///"))
    if "://" not in DATABASE_URL:return Path(DATABASE_URL)
    return DEFAULT_DB_PATH
TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL", "").strip()
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
TURSO_SYNC_INTERVAL = int(os.environ.get("TURSO_SYNC_INTERVAL", "60"))
if bool(TURSO_DATABASE_URL) != bool(TURSO_AUTH_TOKEN):
    raise RuntimeError("Turso configuration is incomplete: set both TURSO_DATABASE_URL and TURSO_AUTH_TOKEN, or neither.")

SESSION_SECRET = os.environ.get("DELIVERY_SESSION_SECRET", "")
BASIC_AUTH_ENABLED = os.environ.get("DELIVERY_BASIC_AUTH_ENABLED", "0") != "0"
PASSWORD_MIN_LENGTH = 12

SESSION_COOKIE = "vmodel_session"
SESSION_TTL_SECONDS = int(os.environ.get("DELIVERY_SESSION_TTL_SECONDS", "43200"))
COOKIE_SECURE = os.environ.get("DELIVERY_COOKIE_SECURE", "1") != "0"
PUBLIC_ORIGIN = os.environ.get("DELIVERY_PUBLIC_ORIGIN", "").rstrip("/")
def alt_origins(env=os.environ) -> frozenset:
    """Extra exact browser origins for sign-in. Honoured ONLY in the local preview (DELIVERY_PREVIEW=1); the live service ignores the setting."""
    if env.get("DELIVERY_PREVIEW") != "1": return frozenset()
    return frozenset(x.strip().rstrip("/") for x in env.get("DELIVERY_ALT_ORIGINS", "").split(",") if x.strip())
ALT_ORIGINS = alt_origins()
GOOGLE_CLIENT_ID = os.environ.get("DELIVERY_GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("DELIVERY_GOOGLE_CLIENT_SECRET", "")
GITHUB_CLIENT_ID = os.environ.get("DELIVERY_GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("DELIVERY_GITHUB_CLIENT_SECRET", "")
ADMIN_EMAILS = {x.strip().lower() for x in os.environ.get("DELIVERY_ADMIN_EMAILS", "").split(",") if x.strip()}
DEFAULT_USER_TOKEN_QUOTA = int(os.environ.get("DELIVERY_DEFAULT_USER_TOKEN_QUOTA", "250000"))
AUTH_RATE_LIMIT = int(os.environ.get("DELIVERY_AUTH_RATE_LIMIT", "20"))
RUN_START_RATE_LIMIT = int(os.environ.get("DELIVERY_RUN_START_RATE_LIMIT", "10"))
# Credits an account must hold before the phase after the interview can start. One place; adjust once real cost is measured.
NEXT_PHASE_MIN_CREDITS = float(os.environ.get("DELIVERY_NEXT_PHASE_MIN_CREDITS", "5"))
# How a generator phase runs on the server: "process" (one child process per run, the default) or "thread" (inside the service, one at a time: the rollback).
RUN_ISOLATION = os.environ.get("DELIVERY_RUN_ISOLATION", "process").strip().lower()
# Process mode only. 2 is a starting value to revisit once real memory use per run is measured (see docs/LESSONS.md).
MAX_CONCURRENT_RUNS = max(1, int(os.environ.get("DELIVERY_MAX_CONCURRENT_RUNS", "2")))
# A phase that runs longer than this is stopped (seconds). 30 minutes.
PHASE_TIME_LIMIT_S = max(60, int(os.environ.get("DELIVERY_PHASE_TIME_LIMIT_S", "1800")))
# Memory guard (src/delivery_api/run_slots.py). A new run waits until free memory is at least the larger of MEMORY_START_FLOOR_MB and MEMORY_START_FACTOR times
# the biggest recent run, for at most MEMORY_WAIT_LIMIT_S seconds. When the server's memory use passes MEMORY_STOP_PERCENT, the biggest running run is stopped.
MEMORY_START_FLOOR_MB = max(0, int(os.environ.get("DELIVERY_MEMORY_START_FLOOR_MB", "600")))
MEMORY_START_FACTOR = max(1.0, float(os.environ.get("DELIVERY_MEMORY_START_FACTOR", "1.25")))
MEMORY_WAIT_LIMIT_S = max(0, int(os.environ.get("DELIVERY_MEMORY_WAIT_LIMIT_S", "900")))
MEMORY_STOP_PERCENT = min(100, max(50, int(os.environ.get("DELIVERY_MEMORY_STOP_PERCENT", "92"))))
# Operator resource samples (src/delivery_api/run_metrics.py): one every METRICS_INTERVAL_S seconds while a run is active; raw rows are kept METRICS_RAW_DAYS days, then one summary row per run.
METRICS_INTERVAL_S = max(2, int(os.environ.get("DELIVERY_METRICS_INTERVAL_S", "10")))
METRICS_IDLE_INTERVAL_S = max(5, int(os.environ.get("DELIVERY_METRICS_IDLE_INTERVAL_S", "30")))   # the container reading when no run is active
METRICS_RAW_DAYS = max(1, int(os.environ.get("DELIVERY_METRICS_RAW_DAYS", "3")))

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
INTERVIEW_ENGINE_TOKEN = os.environ.get("INTERVIEW_ENGINE_TOKEN", "local-interview-engine-token")
