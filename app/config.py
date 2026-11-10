"""
All settings in one place. Secrets and anything that changes between
laptop and server come from environment variables (or the .env file);
the price table lives here in code.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # does nothing if there is no .env file

# --- Provider and keys -------------------------------------------------------
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")  # "openai" or "openrouter"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# --- Which model each role uses ----------------------------------------------
# Each provider has its own model setting, so switching LLM_PROVIDER is the only
# change needed. AGENT_MODEL / CUSTOMER_SIM_MODEL override it for one role.
OPENAI_MODEL = os.getenv("OPENAI_MODEL") or "gpt-5.4-mini"
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL") or "nvidia/nemotron-3.5-super-vl:free"
PROVIDER_MODEL = OPENROUTER_MODEL if LLM_PROVIDER == "openrouter" else OPENAI_MODEL

# "agent" talks to real customers; "customer_sim" plays a customer in the evals.
MODELS = {
    "agent": os.getenv("AGENT_MODEL") or PROVIDER_MODEL,
    "customer_sim": os.getenv("CUSTOMER_SIM_MODEL") or PROVIDER_MODEL,
}

# How hard reasoning models (like gpt-5.4-mini) "think" before answering:
# minimal, low, medium or high. Thinking is billed as output tokens and adds
# latency, so we keep it low. Set REASONING_EFFORT= (empty) to not send it.
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "low") or None

# --- Prices in USD per 1 million tokens --------------------------------------
# Every model in MODELS must be listed here, or chat() refuses to call it
# (without a price we can't enforce the spend caps).
# gpt-5-nano is OpenAI's cheapest model with tool calling.
# Source: OpenAI list price, checked 2026-11-09 (gpt-5.4-mini: 2026-10-25).
# OpenRouter names the same model "openai/gpt-5-nano".
# nvidia/nemotron-3.5-super-vl:free is OpenRouter's free tier (rate-limited, $0),
# checked 2026-11-10. Free models may log prompts to improve the model.
PRICES = {
    "gpt-5-nano": {"input": 0.05, "output": 0.40},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
    "openai/gpt-5-nano": {"input": 0.05, "output": 0.40},
    "nvidia/nemotron-3.5-super-vl:free": {"input": 0.0, "output": 0.0},
}

# --- Spend caps in USD --------------------------------------------------------
SESSION_COST_CAP_USD = float(os.getenv("SESSION_COST_CAP_USD", "0.05"))
DAILY_COST_CAP_USD = float(os.getenv("DAILY_COST_CAP_USD", "2.00"))

# --- Web limits ---------------------------------------------------------------
MAX_MESSAGES_PER_SESSION = int(os.getenv("MAX_MESSAGES_PER_SESSION", "40"))  # customer messages
MAX_MESSAGE_CHARS = 1000
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))         # per IP

# --- Carrier portals -----------------------------------------------------------
# Where the submission agent finds the (fictional) carrier portals. They live in
# this same app, so by default it calls itself over HTTP like any other website.
PORTAL_BASE_URL = os.getenv("PORTAL_BASE_URL", "http://127.0.0.1:8000")

# --- Link previews ------------------------------------------------------------
# The site's public address. Open Graph tags need absolute URLs for the page and
# its preview image, so shared links show a proper card.
SITE_URL = os.getenv("SITE_URL", "https://quoterunner.ramiyer.tech").rstrip("/")

# --- Storage ------------------------------------------------------------------
DB_PATH = Path(os.getenv("DB_PATH", ROOT / "data" / "quoterunner.db"))
