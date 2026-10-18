import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DB_PATH = ROOT / "data" / "applier.db"
PROFILE_DIR = ROOT / "profile"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")

COMPOSE_MODEL = os.getenv("COMPOSE_MODEL", "claude-opus-5-5")
TRIAGE_MODEL = os.getenv("TRIAGE_MODEL", "claude-haiku-5-5")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "")

# Scouting
YC_RECENT_BATCHES = int(os.getenv("YC_RECENT_BATCHES", "2"))
YC_REGIONS = [r.strip() for r in os.getenv("YC_REGIONS", "").split(",") if r.strip()]
NEWS_LOOKBACK_DAYS = int(os.getenv("NEWS_LOOKBACK_DAYS", "14"))

# Only companies scoring at least this (1-10) in triage get researched.
MIN_FIT_SCORE = int(os.getenv("MIN_FIT_SCORE", "6"))
WORKERS = int(os.getenv("WORKERS", "4"))
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "5"))
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
