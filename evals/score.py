"""
Scoring: compare what the agent recorded with the persona's ground truth.

Each field that applies to the business gets one result:
  correct   - matches the truth, or correctly left empty because the owner didn't know
  wrong     - the owner knew it, the agent recorded a different value
  missed    - the owner knew it, the agent recorded nothing
  invented  - the agent filled a field the owner didn't know
Fields filled that don't apply to the business at all (roofing fields for a
bakery, subcontracted % with no subcontractors) are also invented.

The free-text description is not scored: there is no exact right answer.
"""

import re

from app.agent.intake import IntakeState
from evals.personas import Persona

NOT_SCORED = {"description"}


def same(field: str, got, want) -> bool:
    if isinstance(want, str):  # names and choices: ignore case and punctuation
        def norm(s):
            return re.sub(r"[^a-z0-9]", "", str(s).lower())
        return norm(got) == norm(want)
    return got == want and type(got) is type(want)  # so True doesn't equal 1


def score_conversation(persona: Persona, state: IntakeState) -> dict:
    expected = persona.expected_fields()
    fields = {}
    for field in expected:
        if field in NOT_SCORED:
            continue
        got = state.answers.get(field)
        if field in persona.unknown:
            fields[field] = "correct" if got is None else "invented"
        elif got is None:
            fields[field] = "missed"
        else:
            fields[field] = "correct" if same(field, got, persona.truth[field]) else "wrong"

    inapplicable = [f for f in state.answers if f not in expected]
    for field in inapplicable:
        fields[field] = "invented"

    def having(result):
        return [f for f, r in fields.items() if r == result]

    scored = [f for f in fields if f not in inapplicable]
    return {
        "persona_id": persona.id,
        "industry": persona.category,
        "style": persona.style,
        "completed": state.status == "done",
        "questions": state.questions_asked,
        "customer_messages": sum(m["role"] == "user" for m in state.messages),
        "accuracy": sum(fields[f] == "correct" for f in scored) / len(scored),
        "fields": fields,
        "invented": having("invented"),
        "inapplicable": inapplicable,      # invented fields that don't apply to this business
        "wrong": having("wrong"),
        "missed": having("missed"),
    }


def summarize(results: list[dict]) -> dict:
    """Headline numbers for a run, plus per-field, per-industry and per-style accuracy."""
    def accuracy(rows):
        # Micro-average: every field that applies, across all conversations, counts once.
        pairs = [(f, r) for row in rows for f, r in row["fields"].items() if f not in row["inapplicable"]]
        return sum(r == "correct" for _, r in pairs) / len(pairs) if pairs else 0.0

    per_field = {}
    for row in results:
        for field, result in row["fields"].items():
            if field in row["inapplicable"]:
                continue
            counts = per_field.setdefault(field, {"correct": 0, "total": 0})
            counts["total"] += 1
            counts["correct"] += result == "correct"
    for counts in per_field.values():
        counts["accuracy"] = counts["correct"] / counts["total"]

    n = len(results)
    return {
        "conversations": n,
        "field_accuracy": accuracy(results),
        "invented_values": sum(len(r["invented"]) for r in results),
        "conversations_with_invented": sum(bool(r["invented"]) for r in results),
        "wrong_values": sum(len(r["wrong"]) for r in results),
        "missed_values": sum(len(r["missed"]) for r in results),
        "completion_rate": sum(r["completed"] for r in results) / n,
        "avg_questions": sum(r["questions"] for r in results) / n,
        "form_fields": 25,
        "avg_cost_agent_usd": sum(r.get("cost_agent_usd", 0) for r in results) / n,
        "avg_cost_sim_usd": sum(r.get("cost_sim_usd", 0) for r in results) / n,
        "errors": sum(bool(r.get("error")) for r in results),
        "per_field": per_field,
        "by_industry": {k: accuracy([r for r in results if r["industry"] == k])
                        for k in sorted({r["industry"] for r in results})},
        "by_style": {k: accuracy([r for r in results if r["style"] == k])
                     for k in sorted({r["style"] for r in results})},
    }
