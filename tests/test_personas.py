"""Tests for the eval personas and the simulated customer's prompt."""

from app.agent.intake import label, validate_field
from evals.customer_sim import fact_card, sim_prompt
from evals.personas import CATEGORIES, STYLES, build_personas

PERSONAS = build_personas()


def test_fifty_personas_identical_every_time():
    assert len(PERSONAS) == 50
    assert build_personas() == PERSONAS
    assert len({p.id for p in PERSONAS}) == 50


def test_spread_across_industries_states_and_styles():
    for category in CATEGORIES:
        assert sum(p.category == category for p in PERSONAS) >= 10
    assert len({p.truth["state"] for p in PERSONAS}) >= 20
    assert {p.style for p in PERSONAS} == set(STYLES)


def test_each_owner_has_one_or_two_unknown_facts():
    for p in PERSONAS:
        assert 1 <= len(p.unknown) <= 2
        assert set(p.unknown) <= set(p.expected_fields())
        assert not set(p.unknown) & set(p.truth)


def test_every_field_that_applies_is_known_or_unknown():
    for p in PERSONAS:
        assert set(p.truth) | set(p.unknown) == set(p.expected_fields()), p.id


def test_ground_truth_passes_the_agents_own_validation():
    for p in PERSONAS:
        for field, value in p.truth.items():
            assert validate_field(field, value, p.truth) == value, (p.id, field)


def test_changes_answer_personas_have_a_wrong_first_value():
    for p in PERSONAS:
        assert (p.style == "changes_answer") == (p.change is not None)
        if p.change:
            assert p.change["first"] != p.truth[p.change["field"]]
            assert str(p.change["first"]) in sim_prompt(p)


def test_fact_card_lists_known_facts_but_not_unknown_ones():
    for p in PERSONAS:
        card = fact_card(p)
        assert p.truth["business_name"] in card
        for field in p.unknown:
            assert label(field) not in card
        assert "industry_category" not in card and "Industry:" not in card
