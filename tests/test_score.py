"""Tests for eval scoring."""

import pytest

from app.agent.intake import IntakeState
from evals.personas import Persona
from evals.score import same, score_conversation, summarize

TRUTH = {"description": "roofing contractor", "industry_category": "roofing", "state": "TX",
         "num_employees": 8, "num_vehicles": 3, "prior_claims_3yr": 0, "years_in_business": 6,
         "business_name": "Lone Star Roofing", "roof_max_height_ft": 30, "roofing_work_type": "both",
         "uses_torch_down": False, "uses_subcontractors": False}
ROOFER = Persona(id=1, category="roofing", style="terse", truth=TRUTH, unknown=["annual_revenue_usd"])


def score(**changes):
    answers = {**TRUTH, **changes}
    answers = {k: v for k, v in answers.items() if v is not None}
    return score_conversation(ROOFER, IntakeState(status="done", answers=answers, questions_asked=12))


def test_perfect_conversation():
    r = score()
    assert r["accuracy"] == 1.0
    assert r["invented"] == r["wrong"] == r["missed"] == []
    assert r["completed"] and r["questions"] == 12


def test_unknown_fact_left_empty_is_correct_but_filled_is_invented():
    assert score()["fields"]["annual_revenue_usd"] == "correct"
    r = score(annual_revenue_usd=900_000)
    assert r["invented"] == ["annual_revenue_usd"]
    assert r["accuracy"] < 1.0


def test_wrong_and_missed_values():
    r = score(num_employees=9, num_vehicles=None)
    assert r["wrong"] == ["num_employees"]
    assert r["missed"] == ["num_vehicles"]


def test_fields_that_do_not_apply_are_invented_and_not_in_accuracy():
    r = score(subcontracted_pct=20, serves_alcohol=True)  # no subcontractors; not a restaurant
    assert set(r["invented"]) == {"subcontracted_pct", "serves_alcohol"}
    assert r["inapplicable"] == ["subcontracted_pct", "serves_alcohol"]
    assert r["accuracy"] == 1.0


def test_description_is_not_scored():
    assert "description" not in score(description="something else")["fields"]


@pytest.mark.parametrize("got, want, ok", [
    ("lone star roofing", "Lone Star Roofing", True),
    ("Lone Star Roofing, LLC", "Lone Star Roofing", False),
    (1, True, False),          # 1 must not count as yes
    (30, 30, True),
])
def test_same(got, want, ok):
    assert same("f", got, want) is ok


def test_summary_numbers():
    good, bad = score(), score(annual_revenue_usd=1, num_employees=9)
    bad["completed"] = False
    s = summarize([good, bad])
    n_fields = len(good["fields"])
    assert s["field_accuracy"] == pytest.approx((2 * n_fields - 2) / (2 * n_fields))
    assert s["invented_values"] == 1 and s["conversations_with_invented"] == 1
    assert s["completion_rate"] == 0.5
    assert s["per_field"]["num_employees"] == {"correct": 1, "total": 2, "accuracy": 0.5}
    assert s["by_industry"] == {"roofing": s["field_accuracy"]}
