"""Tests for the parts that don't need the internet.  Run with:  pytest"""

from types import SimpleNamespace

from intake import (BusinessApplication, extract_fields, merge_updates,
                    recommend_coverage)


def policies(app):
    return [r["policy"] for r in recommend_coverage(app)]


# --- Validation -------------------------------------------------------------
def test_state_is_normalised():
    app, rejected = merge_updates(BusinessApplication(), {"state": " tx "})
    assert app.state == "TX" and rejected == []


def test_bad_values_are_rejected_but_good_ones_kept():
    app, rejected = merge_updates(BusinessApplication(),
                                  {"state": "ZZ", "num_employees": -3, "business_name": "Acme Roofing"})
    assert app.business_name == "Acme Roofing"
    assert app.state is None and app.num_employees is None
    assert set(rejected) == {"state", "num_employees"}


def test_unknown_and_empty_fields_are_ignored():
    app, _ = merge_updates(BusinessApplication(business_name="A"), {"favourite_colour": "blue", "business_name": None})
    assert app.business_name == "A"


def test_progress():
    assert BusinessApplication().progress() == 0
    assert BusinessApplication(business_name="A", state="FL").progress() == 2 / 8


# --- Coverage rules -----------------------------------------------------------
def test_everyone_gets_general_liability():
    assert policies(BusinessApplication()) == ["General Liability"]


def test_employees_vehicles_and_claims():
    app = BusinessApplication(state="FL", num_employees=5, num_vehicles=2,
                              annual_revenue_usd=800_000, prior_claims_3yr=3)
    p = policies(app)
    assert "Workers' Compensation" in p
    assert "Commercial Auto" in p
    assert "Business Owner's Policy (BOP)" in p
    assert "Flag: hard-to-place risk" in p


def test_texas_workers_comp_note():
    recs = recommend_coverage(BusinessApplication(state="TX", num_employees=3))
    wc = next(r for r in recs if r["policy"] == "Workers' Compensation")
    assert "Texas" in wc["why"]


def test_professional_liability_for_consultants():
    assert "Professional Liability (E&O)" in policies(BusinessApplication(industry="Marketing consultant"))


# --- LLM call with a fake client (no API key needed) --------------------------
def test_extract_fields_reads_tool_output():
    fake_block = SimpleNamespace(type="tool_use", input={"num_employees": 4})
    fake_client = SimpleNamespace(messages=SimpleNamespace(
        create=lambda **kwargs: SimpleNamespace(content=[fake_block])))
    chat = [{"role": "assistant", "content": "How many employees?"},
            {"role": "user", "content": "four"}]
    assert extract_fields(fake_client, chat) == {"num_employees": 4}
