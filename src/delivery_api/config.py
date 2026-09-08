"""Env-driven config. Zero-cost posture: defaults target free tiers
(Supabase Free 500MB/5GB, Vercel Hobby). No paid infra without asking Shy."""
from __future__ import annotations

import os

# Abuse protection (scoping doc section 5): per-run hard cap + global cap.
PER_RUN_BUDGET_USD = float(os.environ.get("DELIVERY_PER_RUN_BUDGET_USD", "6.00"))
GLOBAL_BUDGET_USD = float(os.environ.get("DELIVERY_GLOBAL_BUDGET_USD", "100.00"))
ADMIN_TOKEN = os.environ.get("DELIVERY_ADMIN_TOKEN", "dev-admin-token")  # link creation is Shy-only
DB_PATH = os.environ.get("DELIVERY_DB_PATH", "delivery.db")
ARTIFACT_DIR = os.environ.get("DELIVERY_ARTIFACT_DIR", "artifacts")
