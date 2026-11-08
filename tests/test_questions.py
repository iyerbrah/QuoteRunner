"""Tests for question order, skip logic and industry follow-ups."""

import pytest

from app.agent.questions import (BASE_QUESTIONS, INDUSTRY_QUESTIONS, PRIORITY, Question,
                                 industry_categories, next_question, validate_answer)
from app.agent.schema import Operations

BASE_DONE = {"description": "x", "state": "TX", "num_employees": 3, "annual_revenue_usd": 1,
             "num_vehicles": 1, "prior_claims_3yr": 0, "years_in_business": 2, "business_name": "B"}


def questions_asked(answers: dict, unknown=()) -> list[str]:
    """Answer every question in turn and record which fields got asked."""
    answers, asked = dict(answers), []
    while (q := next_question(answers, list(unknown))) is not None:
        asked.append(q.field)
        answers[q.field] = True if q.type == "bool" else 1
    return asked


def test_starts_with_what_the_business_does():
    assert next_question({}, []).field == "description"


def test_follows_priority_order():
    assert questions_asked({"industry_category": "other"}) == [f for f in PRIORITY if f != "industry_category"]


def test_skips_answered_and_unknown_fields():
    q = next_question({"description": "roofing", "industry_category": "roofing"}, unknown=["state"])
    assert q.field == "num_employees"


def test_roofer_is_asked_roof_height_but_daycare_is_not():
    roofer = questions_asked({**BASE_DONE, "industry_category": "roofing"})
    daycare = questions_asked({**BASE_DONE, "industry_category": "daycare"})
    assert "roof_max_height_ft" in roofer
    assert "roof_max_height_ft" not in daycare
    assert "licensed_capacity" in daycare


def test_other_industry_gets_no_follow_ups():
    assert next_question({**BASE_DONE, "industry_category": "other"}, []) is None


def test_only_if_condition():
    restaurant = {**BASE_DONE, "industry_category": "restaurant"}
    assert "alcohol_sales_pct" not in questions_asked({**restaurant, "serves_alcohol": False})
    assert "alcohol_sales_pct" in questions_asked({**restaurant, "serves_alcohol": True})


def test_validate_answer_types_and_bounds():
    height = INDUSTRY_QUESTIONS["roofing"][0]
    assert validate_answer(height, "35") == 35
    with pytest.raises(ValueError):
        validate_answer(height, -5)
    with pytest.raises(ValueError):
        validate_answer(height, "tall")

    work_type = INDUSTRY_QUESTIONS["roofing"][1]
    assert validate_answer(work_type, " Commercial ") == "commercial"
    with pytest.raises(ValueError):
        validate_answer(work_type, "industrial")

    torch = INDUSTRY_QUESTIONS["roofing"][2]
    assert validate_answer(torch, "yes") is True


def test_field_names_are_unique():
    fields = PRIORITY + [q.field for qs in INDUSTRY_QUESTIONS.values() for q in qs]
    assert len(fields) == len(set(fields))
    assert set(PRIORITY) == set(BASE_QUESTIONS)


def test_new_industry_needs_no_code_changes(monkeypatch):
    """The understanding check: one dict entry adds an industry everywhere."""
    monkeypatch.setitem(INDUSTRY_QUESTIONS, "tree_service", [
        Question(field="max_tree_height_ft", label="Tallest tree (ft)", type="int", min=0, max=400,
                 ask="What's the tallest tree you work on, in feet?"),
    ])
    assert "tree_service" in industry_categories()
    assert Operations(industry_category="tree_service").industry_category == "tree_service"
    assert questions_asked({**BASE_DONE, "industry_category": "tree_service"}) == ["max_tree_height_ft"]
