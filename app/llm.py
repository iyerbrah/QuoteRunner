"""
The only place the app talks to an LLM.

chat() does four things on every call:
  1. checks the spend caps BEFORE calling (raises BudgetExceeded if hit)
  2. calls the model through the OpenAI SDK
  3. works out the cost from the token counts and the price table
  4. logs the call to SQLite, whether it worked or failed

Try it:  .venv/bin/python -m app.llm "Say hi in five words"
"""

import time
from datetime import timedelta

from openai import OpenAI
from sqlalchemy import func
from sqlmodel import Session, select

from app import config
from app.db import LLMCall, get_engine, utc_now

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class BudgetExceeded(Exception):
    """A spend cap was reached, so the call was not made."""


# --- Client -------------------------------------------------------------------
def make_client(provider: str) -> OpenAI:
    """Both providers speak the same API; OpenRouter just has a different URL and key."""
    if provider == "openai":
        return OpenAI(api_key=config.OPENAI_API_KEY, timeout=60)
    if provider == "openrouter":
        return OpenAI(api_key=config.OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL, timeout=60)
    raise ValueError(f"LLM_PROVIDER must be 'openai' or 'openrouter', not {provider!r}")


_client = None


def get_client() -> OpenAI:
    """Build the client once and reuse it. Tests replace this function with a fake."""
    global _client
    if _client is None:
        _client = make_client(config.LLM_PROVIDER)
    return _client


# --- Cost and budget ----------------------------------------------------------
def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """USD cost of one call. Prices are per 1 million tokens."""
    if model not in config.PRICES:
        raise ValueError(f"No price for model {model!r}; add it to PRICES in config.py")
    price = config.PRICES[model]
    return input_tokens / 1_000_000 * price["input"] + output_tokens / 1_000_000 * price["output"]


def _total_spent(session: Session, *conditions) -> float:
    """Sum of cost_usd over the logged calls that match the conditions."""
    query = select(func.coalesce(func.sum(LLMCall.cost_usd), 0.0)).where(*conditions)
    return session.exec(query).one()


def session_cost(session_id: str) -> float:
    """Total USD spent so far by one session (used by the eval runner)."""
    with Session(get_engine()) as s:
        return _total_spent(s, LLMCall.session_id == session_id)


def check_budget(session_id: str | None) -> None:
    """Raise BudgetExceeded if this session, or the whole app today, has hit its cap."""
    with Session(get_engine()) as s:
        if session_id is not None:
            spent = _total_spent(s, LLMCall.session_id == session_id)
            if spent >= config.SESSION_COST_CAP_USD:
                raise BudgetExceeded(f"Session spend ${spent:.4f} reached the "
                                     f"${config.SESSION_COST_CAP_USD:.2f} session cap")

        start_of_day = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
        spent_today = _total_spent(s, LLMCall.created_at >= start_of_day,
                                   LLMCall.created_at < start_of_day + timedelta(days=1))
        if spent_today >= config.DAILY_COST_CAP_USD:
            raise BudgetExceeded(f"Today's spend ${spent_today:.4f} reached the "
                                 f"${config.DAILY_COST_CAP_USD:.2f} daily cap")


def _log(**fields) -> None:
    with Session(get_engine()) as s:
        s.add(LLMCall(**fields))
        s.commit()


# --- The one public function --------------------------------------------------
def chat(messages, tools=None, tool_choice=None, model_role="agent", session_id=None):
    """
    Send messages to the model for this role and return the reply message
    (it has .content for text and .tool_calls for tool calls).
    """
    if model_role not in config.MODELS:
        raise ValueError(f"Unknown model_role {model_role!r}; expected one of {list(config.MODELS)}")
    model = config.MODELS[model_role]
    if model not in config.PRICES:  # fail before spending money we can't count
        raise ValueError(f"No price for model {model!r}; add it to PRICES in config.py")

    check_budget(session_id)

    # Only send tools/tool_choice when given: the API rejects tools=null.
    request = {"model": model, "messages": messages}
    if tools:
        request["tools"] = tools
    if tool_choice:
        request["tool_choice"] = tool_choice
    if config.REASONING_EFFORT:
        request["reasoning_effort"] = config.REASONING_EFFORT

    log = {"session_id": session_id, "role": model_role, "model": model}
    start = time.perf_counter()
    try:
        response = get_client().chat.completions.create(**request)
    except Exception as e:
        latency_ms = round((time.perf_counter() - start) * 1000)
        _log(**log, latency_ms=latency_ms, success=False, error=f"{type(e).__name__}: {e}"[:500])
        raise
    latency_ms = round((time.perf_counter() - start) * 1000)

    input_tokens = response.usage.prompt_tokens if response.usage else 0
    output_tokens = response.usage.completion_tokens if response.usage else 0
    _log(**log, input_tokens=input_tokens, output_tokens=output_tokens,
         cost_usd=estimate_cost(model, input_tokens, output_tokens),
         latency_ms=latency_ms, success=True)
    return response.choices[0].message


if __name__ == "__main__":
    import sys

    prompt = " ".join(sys.argv[1:]) or "Say hi in five words."
    reply = chat([{"role": "user", "content": prompt}], session_id="smoke-test")
    print("Reply:", reply.content)
    with Session(get_engine()) as s:
        row = s.exec(select(LLMCall).order_by(LLMCall.id.desc())).first()
    print("Logged:", row.model_dump())
