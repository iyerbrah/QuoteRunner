"""Tests for the web app, through FastAPI's test client. The LLM is faked."""

import html

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import config, db, llm, main
from app.agent.intake import GREETING, IntakeState
from app.agent.schema import Submission
from app.llm import BudgetExceeded
from fakes import ROOF_DETAILS, ROOFER, ROOFER_MESSAGE, ext, run


@pytest.fixture(autouse=True)
def fresh_app(tmp_path, monkeypatch):
    """Each test: its own empty database and an empty rate limiter."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "_engine", None)
    main._recent_requests.clear()


client = TestClient(main.app)


def say(message, session_id=None):
    return client.post("/api/chat", json={"session_id": session_id, "message": message})


def saved_state(session_id) -> IntakeState:
    with Session(db.get_engine()) as s:
        return IntakeState.model_validate_json(s.get(db.ChatSession, session_id).state_json)


def finish_roofer(monkeypatch):
    """A roofer gives everything at once, then approves the summary."""
    run(monkeypatch, ext({**ROOFER, **ROOF_DETAILS}), ext(approve=True))
    session_id = say(ROOFER_MESSAGE).json()["session_id"]
    return session_id, say("yes, all correct", session_id)


# --- Page -----------------------------------------------------------------------
def test_page_shows_greeting_and_examples():
    page = client.get("/")
    assert page.status_code == 200
    assert GREETING in html.unescape(page.text)  # Jinja escapes the apostrophe
    for label in ["Roofing crew in Texas", "Daycare in Florida", "Restaurant with a bar"]:
        assert label in page.text


def test_static_files_are_served():
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


# --- Chat -----------------------------------------------------------------------
def test_first_message_creates_a_session_and_returns_the_form(monkeypatch):
    fake = run(monkeypatch, ext({"description": "roofing crew", "industry_category": "roofing",
                                 "state": "TX", "num_employees": 8}))

    res = say("8-person roofing crew in Texas")

    assert res.status_code == 200
    data = res.json()
    assert data["session_id"] and data["reply"] == "(next question)"
    filled = [f["field"] for f in data["form"]["fields"] if f["status"] == "filled"]
    assert filled == ["description", "industry_category", "state", "num_employees"]
    assert data["form"]["progress"] > 0
    assert fake.calls[0]["session_id"] == data["session_id"]  # cost is traced per session


def test_state_is_saved_between_messages(monkeypatch):
    run(monkeypatch,
        ext({"description": "roofing crew", "industry_category": "roofing", "state": "TX", "num_employees": 8}),
        ext(unknown=["annual_revenue_usd"]))
    session_id = say("8-person roofing crew in Texas").json()["session_id"]

    data = say("no idea", session_id).json()

    revenue = next(f for f in data["form"]["fields"] if f["field"] == "annual_revenue_usd")
    assert revenue["status"] == "unknown"
    assert saved_state(session_id).unknown == ["annual_revenue_usd"]


def test_unknown_session_is_rejected():
    res = say("hello", session_id="does-not-exist")
    assert res.status_code == 404
    assert "start over" in res.json()["error"]


@pytest.mark.parametrize("message", ["", "   ", "x" * 1001])
def test_empty_or_too_long_messages_are_rejected(message):
    assert say(message).status_code == 422


# --- Limits and errors ----------------------------------------------------------
def test_message_limit_per_session(monkeypatch):
    monkeypatch.setattr(config, "MAX_MESSAGES_PER_SESSION", 2)
    fake = run(monkeypatch, ext(), ext())
    session_id = say("one").json()["session_id"]
    say("two", session_id)
    calls = len(fake.calls)

    res = say("three", session_id)

    assert res.status_code == 429
    assert "message limit" in res.json()["error"]
    assert len(fake.calls) == calls  # no LLM call made


def test_rate_limit_per_ip(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 2)
    run(monkeypatch, ext(), ext())
    assert say("one").status_code == 200
    assert say("two").status_code == 200
    res = say("three")
    assert res.status_code == 429
    assert "too quickly" in res.json()["error"]


def test_budget_exceeded_gives_a_polite_message_and_saves_nothing(monkeypatch):
    def over_budget(*args, **kwargs):
        raise BudgetExceeded("Today's spend reached the daily cap")
    monkeypatch.setattr(llm, "chat", over_budget)

    res = say("hello")

    assert res.status_code == 503
    assert "spending limit" in res.json()["error"]
    with Session(db.get_engine()) as s:
        assert s.exec(select(db.ChatSession)).all() == []


def test_llm_error_leaves_the_conversation_unchanged(monkeypatch):
    run(monkeypatch, ext({"description": "roofing crew", "industry_category": "roofing"}))
    session_id = say("roofing crew").json()["session_id"]
    before = saved_state(session_id)

    def broken(*args, **kwargs):
        raise RuntimeError("API down")
    monkeypatch.setattr(llm, "chat", broken)
    res = say("Texas", session_id)

    assert res.status_code == 502
    assert saved_state(session_id) == before


# --- Download -------------------------------------------------------------------
def test_download_only_once_done(monkeypatch):
    run(monkeypatch, ext({**ROOFER, **ROOF_DETAILS}))
    session_id = say(ROOFER_MESSAGE).json()["session_id"]  # now confirming, not done
    assert client.get(f"/api/sessions/{session_id}/application.json").status_code == 409


def test_download_finished_application(monkeypatch):
    session_id, last = finish_roofer(monkeypatch)
    assert last.json()["form"]["status"] == "done"

    res = client.get(f"/api/sessions/{session_id}/application.json")

    assert res.status_code == 200
    assert "attachment" in res.headers["content-disposition"]
    sub = Submission.model_validate_json(res.text)
    assert sub.applicant.business_name == "Lone Star Roofing"
    assert sub.industry_details["roof_max_height_ft"] == 30


def test_download_unknown_session():
    assert client.get("/api/sessions/nope/application.json").status_code == 404
