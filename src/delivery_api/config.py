"""Env-driven config. Zero-cost posture: defaults target free tiers
(Supabase Free 500MB/5GB, Vercel Hobby). No paid infra without asking Shy."""
from __future__ import annotations

import os

# Abuse protection (scoping doc section 5): per-run hard cap + global cap.
PER_RUN_BUDGET_USD = float(os.environ.get("DELIVERY_PER_RUN_BUDGET_USD", "6.00"))
GLOBAL_BUDGET_USD = float(os.environ.get("DELIVERY_GLOBAL_BUDGET_USD", "100.00"))
ADMIN_TOKEN = os.environ.get("DELIVERY_ADMIN_TOKEN", "dev-admin-token")  # link creation is Shy-only
DATABASE_URL = os.environ.get("DELIVERY_DATABASE_URL", os.environ.get("DELIVERY_DB_PATH", "sqlite:///delivery.db"))
# Compatibility alias for older deployments; new code uses DATABASE_URL.
DB_PATH = DATABASE_URL
ARTIFACT_DIR = os.environ.get("DELIVERY_ARTIFACT_DIR", "artifacts")
