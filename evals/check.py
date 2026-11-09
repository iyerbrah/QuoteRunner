"""
The eval check CI runs on every pull request: no API key, no LLM, no cost.

It replays the latest chat run's finished applications through the 3 carrier
portals (the same evals.quotes.replay the real eval uses), with the
hand-written reference mappings standing in for the model's. It fails if:
  - any invented value would reach a carrier,
  - any value a carrier receives is wrong, or any carrier decision differs
    from that carrier's rules applied to the true facts,
  - the results drift from the committed quotes report (then rerun
    `python -m evals.quotes` and commit the new report with the change).

  .venv/bin/python -m evals.check
"""

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import config, db, eval_reports, llm
from app.agent import submit
from evals import quotes
from evals.reference_mappings import as_tool_answer

COMPARED = ["businesses", "submissions", "values_checked", "invented_at_carrier", "quotes",
            "businesses_with_a_quote_first", "businesses_with_a_quote"]


def reference_llm(messages, tools=None, tool_choice=None, model_role="agent", session_id=None):
    """Answers the submission agent's map_fields tool with the reference mapping. Any other
    LLM call would mean the replay isn't offline, so it fails loudly."""
    if not tools or tools[0]["function"]["name"] != "map_fields":
        raise RuntimeError("evals.check must not make real LLM calls")
    portal_fields = tools[0]["function"]["parameters"]["properties"]["fields"]["items"]["properties"][
        "portal_field"]["enum"]
    return SimpleNamespace(content=None, tool_calls=[SimpleNamespace(function=SimpleNamespace(
        name="map_fields", arguments=json.dumps(as_tool_answer(portal_fields))))])


def check() -> list[str]:
    from app.main import app

    chat_report = eval_reports.latest_full_run(eval_reports.all_reports())
    committed = (eval_reports.latest_quotes_report() or {}).get("summary")
    with tempfile.TemporaryDirectory() as tmp:
        config.DB_PATH, db._engine = Path(tmp) / "check.db", None   # a throwaway database
        llm.chat = reference_llm
        submit.portal_client = lambda: TestClient(app)
        rows, _ = quotes.replay(chat_report, session_id="eval-check", verbose=False)
        db._engine = None
    businesses = sum(c["result"]["completed"] for c in chat_report["conversations"])
    s = quotes.summarize(rows, businesses)

    print(f"Replayed {s['businesses']} applications from chat run {chat_report['run_id']}: "
          f"{s['submissions']} portal submissions, {s['values_checked']} values received, "
          f"{s['values_correct']:.1%} correct, {s['invented_at_carrier']} invented, "
          f"decisions {s['decisions_match']:.0%} as the rules dictate, {s['quotes']} quotes.")

    failures = []
    if s["invented_at_carrier"]:
        failures.append(f"{s['invented_at_carrier']} invented values would reach a carrier")
    if s["values_correct"] < 1:
        failures.append(f"only {s['values_correct']:.1%} of values received were correct")
    if s["decisions_match"] < 1:
        failures.append(f"only {s['decisions_match']:.0%} of carrier decisions matched their rules")
    if committed is None:
        failures.append("no committed quotes report: run python -m evals.quotes")
    else:
        for key in COMPARED:
            if s[key] != committed[key]:
                failures.append(f"{key} is {s[key]} but the committed report says {committed[key]}: "
                                "rerun python -m evals.quotes and commit the report")
    return failures


def main() -> int:
    failures = check()
    for f in failures:
        print(f"FAIL: {f}")
    print("Eval check passed." if not failures else f"Eval check failed ({len(failures)}).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
