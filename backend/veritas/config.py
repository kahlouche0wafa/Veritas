"""Central config loader — reads .env once, exposes typed getters.

Everything in VERITAS reads credentials through this module so a live-vs-
scripted switch, or dropping in new keys, is a single-file change.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

# Suppress dotenv's INFO-level warnings about blank lines / empty values in
# the user's .env — those are harmless and only clutter test output.
logging.getLogger("dotenv.main").setLevel(logging.ERROR)

from dotenv import load_dotenv  # noqa: E402

# Load .env from the repo root (C:\veritas\.env) once at import time.
REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env", override=False)


def _first_present(*names: str) -> str | None:
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return None


def _first_groq_key() -> str | None:
    # .env holds GROQ_API_KEY_1..N; pick the first non-empty one.
    for i in range(1, 64):
        v = os.getenv(f"GROQ_API_KEY_{i}")
        if v:
            return v
    return os.getenv("GROQ_API_KEY")


@dataclass(frozen=True)
class Settings:
    # --- Nokia Network as Code (CAMARA) ---
    nokia_base_url: str | None
    nokia_api_key: str | None

    # --- Supabase ---
    supabase_url: str | None
    supabase_service_key: str | None

    # --- LLM (Groq preferred, Google fallback) ---
    groq_api_key: str | None
    groq_model: str
    google_api_key: str | None
    google_model: str

    # --- Runtime toggles ---
    camara_mode: str  # "live" | "scripted" | "auto"

    @property
    def has_live_camara(self) -> bool:
        return bool(self.nokia_base_url and self.nokia_api_key)

    @property
    def has_supabase(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_key)

    @property
    def has_llm(self) -> bool:
        return bool(self.groq_api_key or self.google_api_key)


def load_settings() -> Settings:
    return Settings(
        nokia_base_url=_first_present("NOKIA_BASE_URL", "NAC_BASE_URL", "CAMARA_BASE_URL"),
        nokia_api_key=_first_present("NOKIA_API_KEY", "NAC_API_KEY", "CAMARA_API_KEY"),
        supabase_url=_first_present("SUPABASE_URL"),
        supabase_service_key=_first_present("SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY"),
        groq_api_key=_first_groq_key(),
        groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
        google_api_key=_first_present("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        google_model=os.getenv("GOOGLE_MODEL", "gemini-2.0-flash"),
        camara_mode=os.getenv("CAMARA_MODE", "auto").lower(),
    )


SETTINGS = load_settings()
