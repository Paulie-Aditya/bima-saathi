"""Environment config and feature flags."""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

IST = ZoneInfo("Asia/Kolkata")
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "bima.db")))


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# Agora
AGORA_APP_ID = env("AGORA_APP_ID")
AGORA_APP_CERT = env("AGORA_APP_CERT")
AGORA_CUSTOMER_ID = env("AGORA_CUSTOMER_ID")
AGORA_CUSTOMER_SECRET = env("AGORA_CUSTOMER_SECRET")
AGORA_API_BASE = env("AGORA_API_BASE", "https://api.agora.io/api/conversational-ai-agent/v2")
AGENT_RTC_UID = env("AGENT_RTC_UID", "1001")

# ASR / TTS
SARVAM_API_KEY = env("SARVAM_API_KEY")
ASR_LANGUAGE = env("ASR_LANGUAGE", "hi-IN")
MURF_API_KEY = env("MURF_API_KEY")
MURF_VOICE_ID = env("MURF_VOICE_ID")
MURF_VOICE_ID_KN = env("MURF_VOICE_ID_KN")
MURF_MODEL = env("MURF_MODEL", "FALCON")
MURF_LOCALE = env("MURF_LOCALE", "hi-IN")
MURF_BASE_URL = env("MURF_BASE_URL")  # e.g. wss://global.api.murf.ai/v1/speech/stream-input

# LLM (any OpenAI-compatible endpoint)
LLM_API_KEY = env("LLM_API_KEY")
LLM_BASE_URL = env("LLM_BASE_URL") or None
LLM_MODEL = env("LLM_MODEL", "gpt-4o-mini")
VISION_MODEL = env("VISION_MODEL", LLM_MODEL)
LLM_SHARED_SECRET = env("LLM_SHARED_SECRET", "dev-secret")
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
WEB_BASE_URL = env("WEB_BASE_URL", "http://localhost:3000").rstrip("/")
MAX_TOOL_ROUNDS = int(env("MAX_TOOL_ROUNDS", "4"))

# Integrations
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_DEMO_CHAT_ID = env("TELEGRAM_DEMO_CHAT_ID")
TELEGRAM_DESK_CHAT_ID = env("TELEGRAM_DESK_CHAT_ID")
GOOGLE_SERVICE_ACCOUNT_JSON = env("GOOGLE_SERVICE_ACCOUNT_JSON")  # path or inline JSON
GOOGLE_CALENDAR_ID = env("GOOGLE_CALENDAR_ID")

# MVP+ flags: env sets the starting value; the admin page can flip them at runtime (F19).
FLAG_NAMES = ["FEATURE_RAG", "FEATURE_CALLBACK", "FEATURE_DOCS", "FEATURE_KANNADA"]
DEFAULT_FLAGS = {name: env(name, "1") not in ("0", "false", "off", "") for name in FLAG_NAMES}


def agora_ready() -> bool:
    return bool(AGORA_APP_ID and AGORA_CUSTOMER_ID and AGORA_CUSTOMER_SECRET)


def llm_ready() -> bool:
    return bool(LLM_API_KEY)
