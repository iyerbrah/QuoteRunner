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
# "agent" talks to real customers; "customer_sim" plays a customer in the evals.
MODELS = {
    "agent": os.getenv("AGENT_MODEL", "gpt-5-nano"),
    "customer_sim": os.getenv("CUSTOMER_SIM_MODEL", "gpt-5-nano"),
}

# --- Prices in USD per 1 million tokens --------------------------------------
# Every model in MODELS must be listed here, or chat() refuses to call it
# (without a price we can't enforce the spend caps).
# gpt-5-nano is OpenAI's cheapest model with tool calling.
# Source: OpenAI list price, checked 2026-11-09.
# OpenRouter names the same model "openai/gpt-5-nano".
PRICES = {
    "gpt-5-nano": {"input": 0.05, "output": 0.40},
    "openai/gpt-5-nano": {"input": 0.05, "output": 0.40},
}

# --- Spend caps in USD --------------------------------------------------------
SESSION_COST_CAP_USD = float(os.getenv("SESSION_COST_CAP_USD", "0.05"))
DAILY_COST_CAP_USD = float(os.getenv("DAILY_COST_CAP_USD", "2.00"))

# --- Storage ------------------------------------------------------------------
DB_PATH = Path(os.getenv("DB_PATH", ROOT / "data" / "quoterunner.db"))
