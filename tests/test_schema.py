"""Tests for validation of base fields and the Submission model."""

import pytest

from app.agent.schema import (BASE_FIELD_GROUP, Applicant, Exposures, LossHistory, Operations,
                              Submission, validate_base_field)


@pytest.mark.parametrize("given", ["TX", " tx ", "Texas", "texas"])
def test_state_codes_and_names_are_normalised(given):
    assert validate_base_field("state", given) == "TX"


def test_multi_word_state_names():
    assert validate_base_field("state", "New York") == "NY"


@pytest.mark.parametrize("field, bad", [
    ("state", "ZZ"), ("state", "Narnia"), ("num_employees", -3), ("num_employees", "a dozen"),
    ("annual_revenue_usd", -1), ("years_in_business", 500), ("industry_category", "spaceflight"),
    ("business_name", "   "),
])
def test_invalid_values_are_rejected(field, bad):
    with pytest.raises(ValueError):
        validate_base_field(field, bad)


def test_numbers_as_text_are_accepted():
    assert validate_base_field("num_employees", "12") == 12


def test_every_base_field_has_a_group():
    assert set(BASE_FIELD_GROUP) == {
        "business_name", "state", "years_in_business", "description", "industry_category",
        "num_employees", "annual_revenue_usd", "num_vehicles", "prior_claims_3yr"}


def test_submission_is_grouped_like_acord_125():
    sub = Submission(applicant=Applicant(state="Florida"), operations=Operations(industry_category="Daycare"),
                     exposures=Exposures(), loss_history=LossHistory())
    data = sub.model_dump()
    assert list(data)[:4] == ["applicant", "operations", "exposures", "loss_history"]
    assert data["applicant"]["state"] == "FL"
    assert data["operations"]["industry_category"] == "daycare"
