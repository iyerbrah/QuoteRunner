"""
The quotes eval: every finished application from the latest chat eval goes to
all 3 carrier portals, and every value each portal RECEIVED is checked against
the simulated owner's ground truth.

  .venv/bin/python -m evals.quotes

Ground truth for each portal field is the hand-written reference mapping
(evals/reference_mappings.py) applied to the persona's true facts. So this
catches mistakes anywhere in the pipeline: a fact recorded wrong in the chat,
a field the model mapped wrong, a unit converted wrong.

When a carrier needs a fact the owner didn't know (usually revenue), the owner
"looks it up": the eval supplies just the requested facts from the persona's
hidden true values, and the agent resubmits to that carrier, as a broker would
after one follow-up question.

Each portal field that arrived is one of:
  correct   - what the carrier should have received
  wrong     - a different value
  invented  - a value the owner never stated (didn't know it, or it doesn't apply)
  missing   - nothing, though the owner knew it
The mapping is re-learned from scratch each run (3 LLM calls), then cached.
"""

import argparse
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

from app import config, llm
from app.agent import submit
from app.agent.intake import IntakeState
from app.carriers import CARRIERS, validate_form
from app.db import PortalMapping, PortalSubmission, get_engine, utc_now
from app.eval_reports import all_reports, latest_full_run
from evals.personas import Persona, build_personas
from evals.reference_mappings import REFERENCE_MAPPINGS
from evals.run import git_commit

REPORTS_DIR = Path(__file__).parent / "reports"
TEXT_SOURCES = {"description"}  # free text: checked for presence, not exact words


def expected_payload(slug: str, facts: dict) -> dict[str, str]:
    """What this carrier should receive, given the facts the owner has stated."""
    fields = {f.name: f for f in CARRIERS[slug].fields}
    payload = {}
    for name, spec in REFERENCE_MAPPINGS[slug].items():
        source = spec[0]
        if source not in facts:                  # unknown to the owner, or doesn't apply
            continue
        m = submit.FieldMap(portal_field=name, source=source, transform=spec[1],
                            option_map=spec[2] if len(spec) > 2 else {})
        text = submit.apply_transform(m, facts[source], fields[name])
        if text is not None:
            payload[name] = text
    return payload


def expected_status(slug: str, facts: dict) -> str:
    """What should happen: the carrier's own rules applied to the stated facts."""
    carrier, payload = CARRIERS[slug], expected_payload(slug, facts)
    for f in carrier.fields:
        if f.required and f.name not in payload:
            spec = REFERENCE_MAPPINGS[slug].get(f.name)
            if spec and spec[1] == "choice" and spec[0] in facts:
                return "not_offered"
            return "needs_info"
    data, errors = validate_form(carrier, payload)
    return "invalid" if errors else carrier.decide(data).status


def received(reference: str) -> dict:
    with Session(get_engine()) as db:
        row = db.exec(select(PortalSubmission).where(PortalSubmission.reference == reference)).first()
    return json.loads(row.received_json) if row else {}


def score_fields(slug: str, facts: dict, got: dict) -> dict[str, str]:
    want, results = expected_payload(slug, facts), {}
    for f in CARRIERS[slug].fields:
        spec = REFERENCE_MAPPINGS[slug].get(f.name)
        g, w = got.get(f.name), want.get(f.name)
        if g is None and w is None:
            results[f.name] = "correct"
        elif g is None:
            results[f.name] = "missing"
        elif w is None:
            results[f.name] = "invented"
        elif spec and spec[0] in TEXT_SOURCES:
            results[f.name] = "correct"            # description present on both sides
        elif spec and spec[0] == "business_name":
            same = "".join(ch for ch in g.lower() if ch.isalnum()) == "".join(ch for ch in w.lower() if ch.isalnum())
            results[f.name] = "correct" if same else "wrong"
        else:
            results[f.name] = "correct" if g == w else "wrong"
    return results


def mapping_accuracy() -> dict[str, float]:
    """How much of the LLM's cached mapping matches the reference, per carrier."""
    out = {}
    with Session(get_engine()) as db:
        rows = {r.carrier: json.loads(r.mapping_json) for r in db.exec(select(PortalMapping))}
    for slug, reference in REFERENCE_MAPPINGS.items():
        learned, right = rows.get(slug, {}), 0
        for name in (f.name for f in CARRIERS[slug].fields):
            spec, m = reference.get(name), learned.get(name)
            want = (spec[0], spec[1], spec[2] if spec and len(spec) > 2 else {}) if spec else (None,)
            have = (m["source"], m["transform"], m["option_map"]) if m and m["source"] else (None,)
            right += want == have
        out[slug] = right / len(CARRIERS[slug].fields)
    return out


def score_row(persona: Persona, r, facts: dict, stage: str) -> dict:
    got = received(r.reference) if r.reference else {}
    return {"persona_id": persona.id, "industry": persona.category, "carrier": r.carrier, "stage": stage,
            "status": r.status, "expected_status": expected_status(r.carrier, facts),
            "premium": r.premium, "reason": r.reason, "missing": r.missing_fields,
            "fields": score_fields(r.carrier, facts, got) if r.reference else {}}


def run(fresh_mapping: bool = True) -> dict:
    chat_report = latest_full_run(all_reports())
    session_id = f"quotes-eval-{int(time.time())}"
    if fresh_mapping:
        with Session(get_engine()) as db:
            db.exec(delete(PortalMapping))
            db.commit()
    submit.portal_client = lambda: TestClient(__import__("app.main", fromlist=["app"]).app)

    rows, timings = [], []
    finished = [c for c in chat_report["conversations"] if c["result"]["completed"]]
    # Rebuilt from the seed (identical to the report's) so the hidden true values are there.
    personas = {p.id: p for p in build_personas()}
    for i, convo in enumerate(finished):
        persona = personas[convo["persona"]["id"]]
        assert persona.truth == convo["persona"]["truth"], "personas changed since this chat run"
        state = IntakeState(status="done", answers=convo["answers"], unknown=convo["unknown"])
        start = time.perf_counter()
        results = submit.get_quotes(state, session_id=session_id)
        timings.append(time.perf_counter() - start)
        line = []
        for r in results:
            rows.append(score_row(persona, r, persona.truth, "first"))
            final = r
            if r.status == "needs_info":   # the owner looks up just what was asked for
                looked_up = {f: persona.unknown_truth.get(f, persona.truth.get(f)) for f in r.missing_fields}
                looked_up = {f: v for f, v in looked_up.items() if v is not None}
                if looked_up:
                    after = IntakeState(status="done", answers={**state.answers, **looked_up},
                                        unknown=[f for f in state.unknown if f not in looked_up])
                    final = submit.submit_to_carrier(r.carrier, after, session_id=session_id)
                    rows.append(score_row(persona, final, {**persona.truth, **looked_up}, "after_followup"))
            line.append(f"{r.carrier}={r.status}" + (f"->{final.status}" if final is not r else ""))
        print(f"[{i + 1:>2}/{len(finished)}] #{persona.id:<3}{persona.category:<11}" + "  ".join(line), flush=True)

    arrived = [r for r in rows if r["fields"]]
    verdicts = [v for r in arrived for v in r["fields"].values()]
    first = [r for r in rows if r["stage"] == "first"]
    # The final answer per business and carrier: after the follow-up, if there was one.
    final = {(r["persona_id"], r["carrier"]): r for r in rows}.values()
    statuses = ("quoted", "declined", "needs_info", "not_offered", "error")
    by_carrier = {slug: {s: sum(r["status"] == s for r in final if r["carrier"] == slug) for s in statuses}
                  for slug in CARRIERS}

    def with_a_quote(rs):
        return len({r["persona_id"] for r in rs if r["status"] == "quoted"})

    businesses = len(finished)
    summary = {
        "businesses": businesses,
        "submissions": len(rows),
        "followups": sum(r["stage"] == "after_followup" for r in rows),
        "arrived": len(arrived),
        "values_checked": len(verdicts),
        "values_correct": sum(v == "correct" for v in verdicts) / max(1, len(verdicts)),
        "invented_at_carrier": sum(v == "invented" for v in verdicts),
        "wrong_at_carrier": sum(v == "wrong" for v in verdicts),
        "missing_at_carrier": sum(v == "missing" for v in verdicts),
        "decisions_match": sum(r["status"] == r["expected_status"] for r in rows) / max(1, len(rows)),
        "businesses_with_a_quote_first": with_a_quote(first),
        "businesses_with_a_quote": with_a_quote(final),
        "quotes": sum(r["status"] == "quoted" for r in final),
        "first_run_seconds": round(timings[0], 1) if timings else 0,
        "seconds_per_business": round(sum(timings[1:]) / max(1, len(timings) - 1), 3),
        "mapping_cost_usd": llm.session_cost(session_id),
        "mapping_accuracy": mapping_accuracy(),
        "by_carrier": by_carrier,
    }
    REPORTS_DIR.mkdir(exist_ok=True)
    run_id = 1 + max([int(p.stem.split("-")[1]) for p in REPORTS_DIR.glob("quotes-*.json")] or [0])
    report = {"run_id": run_id, "created_at": utc_now().isoformat(timespec="seconds"),
              "config": {"agent_model": config.MODELS["agent"], "reasoning_effort": config.REASONING_EFFORT,
                         "git_commit": git_commit(), "chat_run": chat_report["run_id"]},
              "summary": summary, "rows": rows}
    path = REPORTS_DIR / f"quotes-{run_id}.json"
    path.write_text(json.dumps(report, indent=2))
    report["path"] = str(path)
    return report


def print_report(report: dict) -> None:
    s = report["summary"]
    print(f"\nQuotes run {report['run_id']} · {s['businesses']} businesses from chat run "
          f"{report['config']['chat_run']} · {s['submissions']} portal submissions")
    print(f"  Values received correctly   {s['values_correct']:.1%} of {s['values_checked']}")
    print(f"  Invented values at carriers {s['invented_at_carrier']}")
    print(f"  Wrong / missing             {s['wrong_at_carrier']} / {s['missing_at_carrier']}")
    print(f"  Decisions match the rules   {s['decisions_match']:.0%}")
    print(f"  Businesses with a quote     {s['businesses_with_a_quote_first']} of {s['businesses']} at first, "
          f"{s['businesses_with_a_quote']} after {s['followups']} follow-ups ({s['quotes']} quotes in all)")
    print(f"  Time                        {s['first_run_seconds']}s first (mapping), "
          f"{s['seconds_per_business']}s per business after")
    print(f"  Mapping                     ${s['mapping_cost_usd']:.4f}, accuracy "
          + ", ".join(f"{k} {v:.0%}" for k, v in s["mapping_accuracy"].items()))
    for slug, counts in s["by_carrier"].items():
        print(f"  {CARRIERS[slug].name:<22}" + "  ".join(f"{k} {v}" for k, v in counts.items() if v))
    mismatches = [r for r in report["rows"] if r["status"] != r["expected_status"]
                  or any(v != "correct" for v in r["fields"].values())]
    if mismatches:
        print("\n  Look at these:")
        for r in mismatches[:8]:
            bad = {k: v for k, v in r["fields"].items() if v != "correct"}
            print(f"    #{r['persona_id']} {r['carrier']} [{r['stage']}]: {r['status']} "
                  f"(expected {r['expected_status']}) {bad}")
    print(f"\n  Full report: {report['path']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Send the finished eval applications to the carrier portals.")
    parser.add_argument("--keep-mapping", action="store_true", help="reuse the cached mapping instead of re-learning it")
    args = parser.parse_args(argv)
    report = run(fresh_mapping=not args.keep_mapping)
    print_report(report)
    return report


if __name__ == "__main__":
    main()
