"""Tests for the eval runner, with the customer and agent both faked."""

import json

import pytest
from sqlmodel import Session, select

from app import config, db
from evals import run as runner
from evals.personas import build_personas

PERSONA = build_personas()[0]


@pytest.fixture(autouse=True)
def temp_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(runner, "REPORTS_DIR", tmp_path / "reports")


def fake_agent(monkeypatch, finish_after=None, fail=False):
    """Replace the customer and the agent. The agent records the persona's truth and
    finishes after `finish_after` messages (never, if None)."""
    calls = []
    monkeypatch.setattr(runner, "next_customer_message", lambda persona, chat, session_id=None: "hello")

    def handle_message(state, text, session_id=None):
        if fail:
            raise RuntimeError("API down")
        calls.append(session_id)
        state.messages += [{"role": "user", "content": text}, {"role": "assistant", "content": "ok"}]
        state.questions_asked += 1
        if finish_after and len(calls) >= finish_after:
            state.answers, state.status = dict(PERSONA.truth), "done"
        return "ok"

    monkeypatch.setattr(runner, "handle_message", handle_message)
    return calls


def test_conversation_stops_when_done(monkeypatch):
    calls = fake_agent(monkeypatch, finish_after=3)
    convo = runner.run_conversation(PERSONA, run_id=1)
    assert len(calls) == 3
    assert convo["result"]["completed"] and convo["result"]["accuracy"] == 1.0
    assert calls[0] == f"eval1-p{PERSONA.id}"  # the agent's own session id, for cost tracing


def test_conversation_stops_at_the_message_limit(monkeypatch):
    calls = fake_agent(monkeypatch, finish_after=None)
    convo = runner.run_conversation(PERSONA, run_id=1)
    assert len(calls) == runner.MAX_CUSTOMER_MESSAGES
    assert not convo["result"]["completed"]


def test_an_error_is_recorded_not_raised(monkeypatch):
    fake_agent(monkeypatch, fail=True)
    convo = runner.run_conversation(PERSONA, run_id=1)
    assert "API down" in convo["result"]["error"]


def test_a_run_is_saved_to_sqlite_and_json(monkeypatch, tmp_path):
    fake_agent(monkeypatch, finish_after=2)

    report = runner.main(["--limit", "2", "--workers", "1", "--note", "test run"])

    with Session(db.get_engine()) as s:
        [run] = s.exec(select(db.EvalRun)).all()
        conversations = s.exec(select(db.EvalConversation)).all()
    assert run.note == "test run"
    assert json.loads(run.summary_json)["conversations"] == 2
    assert [c.persona_id for c in conversations] == [1, 2]
    saved = json.loads((tmp_path / "reports" / f"run-{run.id}.json").read_text())
    assert saved["summary"] == report["summary"]
    assert len(saved["conversations"]) == 2 and saved["conversations"][0]["transcript"]


def test_persona_flag_selects_one(monkeypatch):
    fake_agent(monkeypatch, finish_after=1)
    report = runner.main(["--persona", "7", "--workers", "1"])
    assert [c["persona"]["id"] for c in report["conversations"]] == [7]
