"""Tests for the submission agent. The LLM is faked; the portals are real, over HTTP in-process."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import config, db, llm, main
from app.agent import submit
from app.agent.intake import IntakeState
from app.carriers import CARRIERS, PortalField
from evals.reference_mappings import as_tool_answer
from fakes import FakeLLM

ROOFER = {"description": "roofing crew", "industry_category": "roofing", "state": "TX", "num_employees": 8,
          "annual_revenue_usd": 900_000, "num_vehicles": 3, "prior_claims_3yr": 0, "years_in_business": 6,
          "business_name": "Bayou City Roofing", "roof_max_height_ft": 45, "roofing_work_type": "both",
          "uses_torch_down": True, "uses_subcontractors": True, "subcontracted_pct": 30}
CAFE = {"description": "Mexican restaurant", "industry_category": "restaurant", "state": "TX", "num_employees": 22,
        "annual_revenue_usd": 1_800_000, "num_vehicles": 0, "prior_claims_3yr": 1, "years_in_business": 11,
        "business_name": "Rosa's Cantina", "seating_capacity": 90, "serves_alcohol": True,
        "alcohol_sales_pct": 30, "has_deep_fryers": True, "offers_delivery": False}


@pytest.fixture(autouse=True)
def wiring(tmp_path, monkeypatch):
    """Fresh database; the agent's HTTP client talks to the app in-process; the LLM is faked."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(submit, "portal_client", lambda: TestClient(main.app))
    fake = FakeLLM()
    monkeypatch.setattr(llm, "chat", fake)
    return fake


def state(answers, unknown=()):
    return IntakeState(status="done", answers=dict(answers), unknown=list(unknown))


def by_carrier(results):
    return {r.carrier: r for r in results}


# --- Reading forms --------------------------------------------------------------
def test_reads_a_portal_form_like_any_website():
    fields = submit.read_form(TestClient(main.app).get("/portals/granite").text)
    assert [f.name for f in fields] == [f.name for f in CARRIERS["granite"].fields]
    sales = next(f for f in fields if f.name == "gross_sales_thousands")
    assert sales.required and sales.type == "number" and "thousands" in sales.hint
    assert "Roofing contractor" in next(f for f in fields if f.name == "business_class").options


# --- End to end -----------------------------------------------------------------
def test_roofer_gets_a_quote_a_decline_and_a_not_offered():
    results = by_carrier(submit.get_quotes(state(ROOFER)))
    assert results["harborline"].status == "quoted" and results["harborline"].premium > 0
    assert results["granite"].status == "declined" and "40 ft" in results["granite"].reason
    assert results["lakeside"].status == "not_offered"
    assert results["lakeside"].sent == {} or "business_type" not in results["lakeside"].sent


def test_values_are_converted_by_the_forms_own_units():
    results = by_carrier(submit.get_quotes(state(CAFE)))
    assert results["granite"].sent["gross_sales_thousands"] == "1800"   # Granite asks in thousands
    assert results["lakeside"].sent["revenue"] == "1800000"             # Lakeside asks in dollars
    assert results["granite"].sent["business_class"] == "Restaurant"
    assert all(r.status == "quoted" for r in results.values())


def test_quotes_come_back_cheapest_first():
    results = submit.get_quotes(state(CAFE))
    premiums = [r.premium for r in results if r.status == "quoted"]
    assert premiums == sorted(premiums)


def test_unknown_required_fact_means_needs_info_and_nothing_is_sent(wiring):
    answers = {k: v for k, v in CAFE.items() if k != "annual_revenue_usd"}
    results = submit.get_quotes(state(answers, unknown=["annual_revenue_usd"]))
    for r in results:
        assert r.status == "needs_info" and "Annual revenue" in r.missing
    with Session(db.get_engine()) as s:
        assert s.exec(select(db.PortalSubmission)).all() == []


def test_every_value_sent_comes_from_the_application():
    for r in submit.get_quotes(state(ROOFER)):
        for portal_field, value in r.sent.items():
            source = r.sources[portal_field]
            assert source in ROOFER, (r.carrier, portal_field)


# --- What code does with the model's mapping ------------------------------------
def test_mappings_are_cached_per_form(wiring):
    submit.get_quotes(state(CAFE))
    calls = len(wiring.calls)
    submit.get_quotes(state(ROOFER))
    assert len(wiring.calls) == calls == 3 * submit.MAPPING_SAMPLES  # learned once per portal, then cached


def test_a_changed_form_is_mapped_again(wiring, monkeypatch):
    submit.get_quotes(state(CAFE))
    renamed = [f.model_copy(update={"label": "Staff count"}) if f.name == "employees" else f
               for f in CARRIERS["lakeside"].fields]
    monkeypatch.setitem(CARRIERS, "lakeside", CARRIERS["lakeside"].model_copy(update={"fields": renamed}))
    calls = len(wiring.calls)
    submit.submit_to_carrier("lakeside", state(CAFE))
    assert len(wiring.calls) == calls + submit.MAPPING_SAMPLES


FIELDS = [PortalField(name="sales_k", label="Sales", type="number", hint="in thousands"),
          PortalField(name="sales", label="Sales ($)", type="number"),
          PortalField(name="kind", label="Kind", type="select", options=["Diner", "Other"]),
          PortalField(name="pool", label="Pool", type="select", options=["yes", "no"])]


def test_dollar_units_follow_the_forms_words_not_the_model():
    maps = [submit.FieldMap(portal_field="sales_k", source="annual_revenue_usd", transform="as_is"),
            submit.FieldMap(portal_field="sales", source="annual_revenue_usd", transform="thousands")]
    kept = submit.check_mapping(maps, FIELDS)
    assert kept["sales_k"].transform == "thousands" and kept["sales"].transform == "as_is"


def test_invalid_mappings_are_dropped():
    maps = [submit.FieldMap(portal_field="nope", source="state"),                      # not on the form
            submit.FieldMap(portal_field="sales", source="favourite_colour"),          # not our field
            submit.FieldMap(portal_field="pool", source="num_employees", transform="yes_no"),  # not yes/no
            submit.FieldMap(portal_field="kind", source="industry_category", transform="choice",
                            option_map={"restaurant": "Bistro"})]                     # option not on form
    assert submit.check_mapping(maps, FIELDS) == {}


def test_a_backwards_dropdown_map_is_flipped_only_where_unambiguous():
    flipped = submit._orient({"Diner": "restaurant", "Other": "other", "Cafe": "other"},
                             ["restaurant", "other", "roofing"], ["Diner", "Other", "Cafe"])
    assert flipped == {"restaurant": "Diner"}   # "other" had two options: left out, not guessed


def test_required_fields_left_unmapped_get_one_focused_retry(monkeypatch):
    def mapper(portal_fields):
        answer = as_tool_answer(portal_fields)
        if len(portal_fields) > 1:       # every full-form call forgets "headcount"
            for f in answer["fields"]:
                if f["portal_field"] == "headcount":
                    f["source"] = None
        return answer

    fake = FakeLLM(mapper=mapper)
    monkeypatch.setattr(llm, "chat", fake)
    result = submit.submit_to_carrier("harborline", state(ROOFER))
    assert result.status == "quoted" and result.sent["headcount"] == "8"
    assert len(fake.calls) == 2 * submit.MAPPING_SAMPLES   # the full form, then just "headcount"


def test_consensus_keeps_only_what_most_samples_agree_on():
    """The real failure that motivated this: one sample mapped roofing to "Restaurant"."""
    good = {"restaurant": "Restaurant", "daycare": "Daycare"}
    samples = [
        {"business_type": submit.FieldMap(portal_field="business_type", source="industry_category",
                                          transform="choice", option_map={"roofing": "Restaurant", "daycare": "Daycare"})},
        {"business_type": submit.FieldMap(portal_field="business_type", source="industry_category",
                                          transform="choice", option_map=good)},
        {"business_type": submit.FieldMap(portal_field="business_type", source="industry_category",
                                          transform="choice", option_map=good)},
    ]
    agreed = submit.consensus(samples)
    assert agreed["business_type"].option_map == good          # roofing -> Restaurant was outvoted


def test_consensus_drops_a_field_the_samples_disagree_on():
    samples = [{"x": submit.FieldMap(portal_field="x", source=src)} for src in ("num_employees", "num_vehicles", None)]
    assert submit.consensus(samples) == {}


def test_a_broken_portal_does_not_stop_the_others(monkeypatch):
    real = submit.read_form
    monkeypatch.setattr(submit, "read_form", lambda html: [] if "Granite" in html else real(html))
    results = by_carrier(submit.get_quotes(state(CAFE)))
    assert results["lakeside"].status == "quoted"
    assert results["granite"].status in ("error", "needs_info")
