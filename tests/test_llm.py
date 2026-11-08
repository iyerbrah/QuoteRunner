"""
Tests for app/llm.py. The OpenAI SDK is replaced by a fake, so these run
offline, cost nothing, and give the same result every time.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from app import config, db, llm
from app.db import LLMCall, utc_now
from app.llm import BudgetExceeded, chat, estimate_cost, make_client


# --- Fakes and fixtures -------------------------------------------------------
class FakeCompletions:
    """Stands in for client.chat.completions. Records every request it gets."""

    def __init__(self, reply="hi", input_tokens=1000, output_tokens=200, error=None):
        self.requests = []
        self.error = error
        self.response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply, tool_calls=None))],
            usage=SimpleNamespace(prompt_tokens=input_tokens, completion_tokens=output_tokens),
        )

    def create(self, **request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.response


@pytest.fixture(autouse=True)
def test_settings(tmp_path, monkeypatch):
    """Every test gets its own empty database and known models, prices and caps."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(config, "MODELS", {"agent": "test-model", "customer_sim": "test-model"})
    monkeypatch.setattr(config, "PRICES", {"test-model": {"input": 0.75, "output": 4.50}})
    monkeypatch.setattr(config, "SESSION_COST_CAP_USD", 0.25)
    monkeypatch.setattr(config, "DAILY_COST_CAP_USD", 10.0)
    monkeypatch.setattr(config, "REASONING_EFFORT", None)


def use_fake(monkeypatch, **kwargs) -> FakeCompletions:
    completions = FakeCompletions(**kwargs)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(llm, "get_client", lambda: client)
    return completions


def logged_calls() -> list[LLMCall]:
    with Session(db.get_engine()) as s:
        return list(s.exec(select(LLMCall)))


def add_spend(cost_usd, session_id=None, created_at=None):
    """Pretend an earlier call already spent this much."""
    with Session(db.get_engine()) as s:
        s.add(LLMCall(session_id=session_id, role="agent", model="test-model",
                      cost_usd=cost_usd, success=True, created_at=created_at or utc_now()))
        s.commit()


HELLO = [{"role": "user", "content": "hello"}]


# --- Cost ---------------------------------------------------------------------
def test_cost_from_tokens():
    # 1000 input tokens at $0.75/M + 200 output tokens at $4.50/M
    assert estimate_cost("test-model", 1000, 200) == pytest.approx(0.00075 + 0.0009)
    assert estimate_cost("test-model", 1_000_000, 1_000_000) == pytest.approx(5.25)


def test_cost_for_unpriced_model_raises():
    with pytest.raises(ValueError, match="No price"):
        estimate_cost("mystery-model", 10, 10)


# --- Provider -----------------------------------------------------------------
def test_openai_client_uses_default_url(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    assert "api.openai.com" in str(make_client("openai").base_url)


def test_openrouter_client_uses_openrouter_url(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "test-key")
    assert str(make_client("openrouter").base_url).startswith("https://openrouter.ai/api/v1")


def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        make_client("anthropic")


# --- Logging ------------------------------------------------------------------
def test_successful_call_returns_message_and_logs_row(monkeypatch):
    fake = use_fake(monkeypatch, reply="Hi there")

    message = chat(HELLO, session_id="s1")

    assert message.content == "Hi there"
    assert fake.requests == [{"model": "test-model", "messages": HELLO}]  # no tools sent
    [row] = logged_calls()
    assert (row.session_id, row.role, row.model) == ("s1", "agent", "test-model")
    assert (row.input_tokens, row.output_tokens) == (1000, 200)
    assert row.cost_usd == pytest.approx(0.00165)
    assert row.success and row.error is None
    assert row.latency_ms >= 0


def test_tools_and_role_are_passed_through(monkeypatch):
    fake = use_fake(monkeypatch)
    tools = [{"type": "function", "function": {"name": "record_fields", "parameters": {}}}]
    choice = {"type": "function", "function": {"name": "record_fields"}}

    chat(HELLO, tools=tools, tool_choice=choice, model_role="customer_sim")

    assert fake.requests[0]["tools"] == tools
    assert fake.requests[0]["tool_choice"] == choice
    assert logged_calls()[0].role == "customer_sim"


def test_reasoning_effort_is_sent_when_set(monkeypatch):
    fake = use_fake(monkeypatch)
    monkeypatch.setattr(config, "REASONING_EFFORT", "low")

    chat(HELLO)

    assert fake.requests[0]["reasoning_effort"] == "low"


def test_failed_call_is_logged_and_error_reraised(monkeypatch):
    use_fake(monkeypatch, error=RuntimeError("network down"))

    with pytest.raises(RuntimeError, match="network down"):
        chat(HELLO, session_id="s1")

    [row] = logged_calls()
    assert not row.success
    assert "network down" in row.error
    assert row.cost_usd == 0


# --- Spend caps ---------------------------------------------------------------
def test_session_cap_blocks_call_before_it_is_made(monkeypatch):
    fake = use_fake(monkeypatch)
    add_spend(0.25, session_id="s1")

    with pytest.raises(BudgetExceeded, match="session cap"):
        chat(HELLO, session_id="s1")

    assert fake.requests == []  # the API was never called


def test_other_sessions_spend_does_not_count(monkeypatch):
    use_fake(monkeypatch)
    add_spend(0.25, session_id="s1")

    chat(HELLO, session_id="s2")  # no error


def test_daily_cap_counts_all_sessions(monkeypatch):
    fake = use_fake(monkeypatch)
    monkeypatch.setattr(config, "DAILY_COST_CAP_USD", 1.0)
    add_spend(0.6, session_id="a")
    add_spend(0.5, session_id="b")

    with pytest.raises(BudgetExceeded, match="daily cap"):
        chat(HELLO, session_id="c")
    assert fake.requests == []


def test_yesterdays_spend_does_not_count_today(monkeypatch):
    use_fake(monkeypatch)
    monkeypatch.setattr(config, "DAILY_COST_CAP_USD", 1.0)
    add_spend(5.0, created_at=utc_now() - timedelta(days=1))

    chat(HELLO)  # no error


# --- Bad configuration --------------------------------------------------------
def test_unpriced_model_fails_before_calling(monkeypatch):
    fake = use_fake(monkeypatch)
    monkeypatch.setattr(config, "MODELS", {"agent": "mystery-model"})

    with pytest.raises(ValueError, match="No price"):
        chat(HELLO)
    assert fake.requests == [] and logged_calls() == []


def test_unknown_role_raises(monkeypatch):
    use_fake(monkeypatch)
    with pytest.raises(ValueError, match="model_role"):
        chat(HELLO, model_role="broker")
