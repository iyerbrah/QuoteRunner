"""The CI eval check must pass on the real code and fail when the pipeline breaks."""

from app import config, db, llm
from app.agent import submit
from evals import check


def isolate(monkeypatch):
    """check() swaps the database, the LLM and the portal client; put them back afterwards."""
    for obj, name in [(config, "DB_PATH"), (db, "_engine"), (llm, "chat"), (submit, "portal_client")]:
        monkeypatch.setattr(obj, name, getattr(obj, name))


def test_passes_on_the_current_code(monkeypatch):
    isolate(monkeypatch)
    assert check.check() == []


def test_fails_if_units_stop_being_converted(monkeypatch):
    isolate(monkeypatch)
    real = submit.apply_transform

    def no_thousands(m, value, field):   # a bug: revenue sent in dollars to a form that wants thousands
        return str(value) if m.transform == "thousands" else real(m, value, field)

    monkeypatch.setattr(submit, "apply_transform", no_thousands)
    failures = check.check()
    assert any("correct" in f for f in failures)


def test_fails_if_a_value_without_a_source_reaches_a_carrier(monkeypatch):
    isolate(monkeypatch)
    real = submit.build_payload

    def leaky(mapping, fields, state):    # a bug: fill blanks with a typical value
        payload, sources, missing, unmatched = real(mapping, fields, state)
        for f in fields:
            if f.type == "number" and f.name not in payload:
                payload[f.name] = "0"
        return payload, sources, [], unmatched

    monkeypatch.setattr(submit, "build_payload", leaky)
    failures = check.check()
    assert any("invented" in f for f in failures)
