"""Tests for the public /evals pages. Reports are built from real personas, scored for real."""

import html
import json

import pytest
from fastapi.testclient import TestClient

from app import eval_reports, main
from app.agent.intake import IntakeState
from evals.personas import build_personas
from evals.score import score_conversation, summarize

client = TestClient(main.app)
PERSONAS = build_personas()


def write_report(directory, run_id, count, note="", invent=False):
    """A report where the agent recorded each persona's truth (plus one invented value if asked)."""
    conversations = []
    for persona in PERSONAS[:count]:
        answers = dict(persona.truth)
        if invent and persona.id == 1:
            answers[persona.unknown[0]] = 1
        state = IntakeState(status="done", answers=answers, questions_asked=7,
                            messages=[{"role": "assistant", "content": "Hi!"},
                                      {"role": "user", "content": f"message from persona {persona.id}"}])
        conversations.append({"persona": persona.model_dump(), "result": score_conversation(persona, state),
                              "answers": answers, "unknown": [], "transcript": state.messages})
    report = {"run_id": run_id, "created_at": "2026-11-09T01:00:00+00:00",
              "config": {"models": {"agent": "gpt-5-nano"}, "reasoning_effort": "low", "git_commit": "abc1234",
                         "note": note, "personas": [p.id for p in PERSONAS[:count]]},
              "summary": summarize([c["result"] for c in conversations]), "conversations": conversations}
    (directory / f"run-{run_id}.json").write_text(json.dumps(report))


@pytest.fixture
def reports(tmp_path, monkeypatch):
    monkeypatch.setattr(eval_reports, "REPORTS_DIR", tmp_path)
    return tmp_path


def text(response):
    return html.unescape(response.text)


def test_no_runs_yet(reports):
    page = client.get("/evals")
    assert page.status_code == 200
    assert "No eval runs yet" in page.text


def test_headline_is_the_latest_full_run_not_a_quick_partial_one(reports):
    write_report(reports, 1, 50, note="before", invent=True)
    write_report(reports, 2, 50, note="after")
    write_report(reports, 3, 5, note="quick check")

    page = text(client.get("/evals"))

    assert "Latest full run:" in page and "run 2</strong>" in page
    assert "“after”" in page
    assert "100.0%" in page                       # run 2 is perfect
    for note in ["before", "after", "quick check"]:
        assert note in page                       # the history lists every run


def test_any_run_can_be_viewed(reports):
    write_report(reports, 1, 50, note="before", invent=True)
    write_report(reports, 2, 50, note="after")
    page = text(client.get("/evals?run=1"))
    assert "Showing:" in page and "run 1</strong>" in page
    assert "in 1 of 50 conversations" in page      # the invented value shows up
    assert "invented: Annual revenue" in page or "invented: Claims in last 3 years" in page


def test_three_sample_transcripts_one_per_main_industry(reports):
    write_report(reports, 1, 50)
    assert eval_reports.samples(eval_reports.load_report(1))
    industries = [c["persona"]["category"] for c in eval_reports.samples(eval_reports.load_report(1))]
    assert industries == ["roofing", "daycare", "restaurant"]


def test_transcript_page_shows_facts_results_and_messages(reports):
    write_report(reports, 1, 50, invent=True)
    page = text(client.get("/evals/runs/1/1"))
    assert page.count("doesn't know") >= 1
    assert "invented" in page
    assert "message from persona 1" in page


def test_unknown_run_or_persona_is_404(reports):
    write_report(reports, 1, 5)
    assert client.get("/evals?run=9").status_code == 404
    assert client.get("/evals/runs/9/1").status_code == 404
    assert client.get("/evals/runs/1/42").status_code == 404


def test_only_numbers_name_a_run(reports):
    write_report(reports, 1, 5)
    assert client.get("/evals/runs/..%2Fsecrets/1").status_code in (404, 422)
    assert client.get("/evals?run=../../.env").status_code == 422


def test_real_reports_render():
    """The committed reports in evals/reports must always render."""
    assert client.get("/evals").status_code == 200
    for report in eval_reports.all_reports():
        assert client.get(f"/evals?run={report['run_id']}").status_code == 200


def test_chat_page_links_to_evals():
    assert 'href="/evals"' in client.get("/").text
