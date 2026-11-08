"""
The eval runner: simulated customers talk to the real intake agent, and
every conversation is scored against ground truth.

  .venv/bin/python -m evals.run                      all 50 personas
  .venv/bin/python -m evals.run --limit 5            the first 5
  .venv/bin/python -m evals.run --persona 7          just persona 7
  .venv/bin/python -m evals.run --note "before fix"  label the run

The agent side is handle_message(), exactly what the website calls, so the
numbers measure what's deployed. Each run is saved to SQLite and to
evals/reports/run-<id>.json (config, scores and every transcript).
"""

import argparse
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sqlmodel import Session

from app import config, llm
from app.agent.intake import handle_message, new_state
from app.db import EvalConversation, EvalRun, get_engine
from evals.customer_sim import next_customer_message
from evals.personas import SEED, Persona, build_personas
from evals.score import score_conversation, summarize

MAX_CUSTOMER_MESSAGES = 25
REPORTS_DIR = Path(__file__).parent / "reports"


def run_conversation(persona: Persona, run_id: int) -> dict:
    """One full conversation: customer and agent take turns until done or the message limit."""
    agent_session, sim_session = f"eval{run_id}-p{persona.id}", f"eval{run_id}-p{persona.id}-sim"
    state, error = new_state(), None
    try:
        for _ in range(MAX_CUSTOMER_MESSAGES):
            message = next_customer_message(persona, state.messages, session_id=sim_session)
            handle_message(state, message, session_id=agent_session)
            if state.status == "done":
                break
    except Exception as e:  # one failed conversation shouldn't stop the run; it's scored as it stands
        error = f"{type(e).__name__}: {e}"

    result = score_conversation(persona, state)
    result.update(error=error, cost_agent_usd=llm.session_cost(agent_session),
                  cost_sim_usd=llm.session_cost(sim_session))
    return {"persona": persona.model_dump(), "result": result, "answers": state.answers,
            "unknown": state.unknown, "transcript": state.messages}


def git_commit() -> str:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "app"], capture_output=True, text=True).stdout.strip()
        return commit + ("-dirty" if dirty else "")
    except OSError:
        return "unknown"


def run_eval(personas: list[Persona], workers: int, note: str | None = None) -> dict:
    run_config = {"models": config.MODELS, "reasoning_effort": config.REASONING_EFFORT, "seed": SEED,
                  "personas": [p.id for p in personas], "max_customer_messages": MAX_CUSTOMER_MESSAGES,
                  "git_commit": git_commit(), "note": note}
    with Session(get_engine()) as db:
        run = EvalRun(note=note, config_json=json.dumps(run_config))
        db.add(run)
        db.commit()
        run_id, started_at = run.id, run.created_at.isoformat(timespec="seconds")

    conversations = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_conversation, p, run_id) for p in personas]
        for done, future in enumerate(as_completed(futures), start=1):
            convo = future.result()
            conversations.append(convo)
            r = convo["result"]
            print(f"[{done:>2}/{len(personas)}] #{r['persona_id']:<3}{r['industry']:<11}{r['style']:<15}"
                  f"{'done' if r['completed'] else 'NOT DONE':<9}{r['accuracy']:>5.0%}"
                  f"  invented={r['invented'] or 0}" + (f"  ERROR {r['error']}" if r["error"] else ""), flush=True)

    conversations.sort(key=lambda c: c["persona"]["id"])
    summary = summarize([c["result"] for c in conversations])
    with Session(get_engine()) as db:
        run = db.get(EvalRun, run_id)
        run.summary_json = json.dumps(summary)
        db.add(run)
        for c in conversations:
            db.add(EvalConversation(run_id=run_id, persona_id=c["persona"]["id"],
                                    scores_json=json.dumps(c["result"]), transcript_json=json.dumps(c["transcript"])))
        db.commit()

    report = {"run_id": run_id, "created_at": started_at, "config": run_config, "summary": summary,
              "conversations": conversations}
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / f"run-{run_id}.json"
    path.write_text(json.dumps(report, indent=2))
    report["path"] = str(path)
    return report


def print_report(report: dict) -> None:
    s, cfg = report["summary"], report["config"]
    print(f"\nRun {report['run_id']} · {s['conversations']} personas · agent {cfg['models']['agent']} · "
          f"commit {cfg['git_commit']}" + (f" · {cfg['note']}" if cfg["note"] else ""))
    print(f"  Field accuracy       {s['field_accuracy']:.1%}")
    print(f"  Invented values      {s['invented_values']} (in {s['conversations_with_invented']} of "
          f"{s['conversations']} conversations)")
    print(f"  Wrong / missed       {s['wrong_values']} / {s['missed_values']}")
    print(f"  Completion rate      {s['completion_rate']:.0%}")
    print(f"  Avg questions        {s['avg_questions']:.1f} (vs a {s['form_fields']}-field form)")
    print(f"  Cost / conversation  ${s['avg_cost_agent_usd']:.4f} agent + ${s['avg_cost_sim_usd']:.4f} customer sim")
    if s["errors"]:
        print(f"  Errors               {s['errors']}")
    print("  By industry          " + ", ".join(f"{k} {v:.0%}" for k, v in s["by_industry"].items()))
    print("  By style             " + ", ".join(f"{k} {v:.0%}" for k, v in s["by_style"].items()))

    worst = sorted(report["conversations"],
                   key=lambda c: (-len(c["result"]["invented"]), c["result"]["completed"], c["result"]["accuracy"]))[:5]
    print("\n  Read these transcripts first:")
    for c in worst:
        r = c["result"]
        problems = [f"invented {r['invented']}" if r["invented"] else "", f"wrong {r['wrong']}" if r["wrong"] else "",
                    f"missed {r['missed']}" if r["missed"] else "", "" if r["completed"] else "not done"]
        print(f"    #{r['persona_id']:<3}{r['industry']}/{r['style']}: {r['accuracy']:.0%}  "
              + "; ".join(p for p in problems if p))
    print(f"\n  Full report: {report['path']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run simulated customers against the intake agent.")
    parser.add_argument("--limit", type=int, help="run only the first N personas")
    parser.add_argument("--persona", type=int, action="append", help="run only this persona id (repeatable)")
    parser.add_argument("--workers", type=int, default=8, help="conversations run in parallel (default 8)")
    parser.add_argument("--note", help="a label for this run, e.g. 'before prompt fix'")
    args = parser.parse_args(argv)

    personas = build_personas()
    if args.persona:
        personas = [p for p in personas if p.id in args.persona]
    if args.limit:
        personas = personas[:args.limit]
    if not personas:
        parser.error("no personas selected")

    report = run_eval(personas, workers=args.workers, note=args.note)
    print_report(report)
    return report


if __name__ == "__main__":
    main()
