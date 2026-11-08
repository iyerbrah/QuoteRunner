"""
Tests for the intake agent. llm.chat is replaced by a fake that returns
scripted extractions, so these test our logic, not the model.
"""

import pytest

from app.agent.intake import (DONE_MESSAGE, EXTRACT_CONTEXT, build_submission, grounded,
                              handle_message, new_state)
from app.agent.schema import Submission
from fakes import ROOF_DETAILS, ROOFER, ROOFER_MESSAGE, ext, run


# --- Extraction ---------------------------------------------------------------
def test_one_sentence_fills_several_fields(monkeypatch):
    run(monkeypatch, ext({"description": "roofing crew", "industry_category": "roofing",
                          "state": "Texas", "num_employees": 8}))
    state = new_state()

    handle_message(state, "We're an 8-person roofing crew in Texas")

    assert state.answers == {"description": "roofing crew", "industry_category": "roofing",
                             "state": "TX", "num_employees": 8}
    assert state.last_asked == "annual_revenue_usd"
    assert state.questions_asked == 1


def test_no_idea_about_revenue_leaves_it_empty_and_never_asks_again(monkeypatch):
    run(monkeypatch,
        ext({"description": "roofing", "industry_category": "roofing", "state": "TX", "num_employees": 8}),
        ext(unknown=["annual_revenue_usd"]))
    state = new_state()
    handle_message(state, "8-person roofing crew in Texas")

    handle_message(state, "no idea, my accountant handles that")

    assert "annual_revenue_usd" not in state.answers
    assert state.unknown == ["annual_revenue_usd"]
    assert state.last_asked == "num_vehicles"  # moved on
    sub = build_submission(state)
    assert sub.exposures.annual_revenue_usd is None
    assert sub.unknown_by_customer == ["annual_revenue_usd"]


def test_unknown_wins_over_a_value_for_the_same_field(monkeypatch):
    run(monkeypatch, ext({"num_employees": 10}, unknown=["num_employees"]))
    state = new_state()
    handle_message(state, "not sure how many, maybe 10?")
    assert "num_employees" not in state.answers


def test_uncertain_value_is_held_until_confirmed(monkeypatch):
    run(monkeypatch,
        ext({"num_employees": 12}, uncertain=["num_employees"]),
        ext(confirmed=["num_employees"]))
    state = new_state()

    reply = handle_message(state, "about a dozen people")
    assert "num_employees" not in state.answers
    assert state.pending == {"num_employees": 12}
    assert state.last_asked == "pending"
    assert reply.startswith("Just to check, I have Employees: 12.")  # written in code

    handle_message(state, "yes, 12 is right")
    assert state.answers["num_employees"] == 12
    assert state.pending == {}


def test_uncertain_value_can_be_corrected(monkeypatch):
    run(monkeypatch,
        ext({"num_employees": 12}, uncertain=["num_employees"]),
        ext({"num_employees": 14}))
    state = new_state()
    handle_message(state, "about a dozen")
    handle_message(state, "actually it's 14")
    assert state.answers["num_employees"] == 14
    assert state.pending == {}


def test_invalid_value_is_rejected_and_asked_again(monkeypatch):
    fake = run(monkeypatch, ext({"description": "bakery", "industry_category": "other", "state": "Narnia"}))
    state = new_state()

    handle_message(state, "a bakery in Narnia")

    assert "state" not in state.answers
    assert state.last_asked == "state"
    assert "wasn't valid" in fake.reply_calls[0]["messages"][0]["content"]


def test_fields_from_another_industry_are_ignored(monkeypatch):
    run(monkeypatch, ext({"industry_category": "roofing", "serves_alcohol": True, "favourite_colour": "blue"}))
    state = new_state()
    handle_message(state, "roofers, and we serve beer at lunch")
    assert state.answers == {"industry_category": "roofing"}


def test_unreadable_tool_output_records_nothing_and_asks_again(monkeypatch):
    run(monkeypatch, "{not json")
    state = new_state()
    handle_message(state, "hello")
    assert state.answers == {}
    assert state.last_asked == "description"


def test_empty_reply_falls_back_to_our_wording(monkeypatch):
    run(monkeypatch, ext(), reply="")
    state = new_state()
    assert handle_message(state, "hi") == "What does your business do?"


def test_llm_sees_only_recent_messages(monkeypatch):
    fake = run(monkeypatch, *[ext() for _ in range(5)])
    state = new_state()
    for i in range(5):
        handle_message(state, f"message {i}")
    extraction_messages = fake.calls[-2]["messages"]
    assert len(extraction_messages) == 1 + EXTRACT_CONTEXT  # system prompt + last question and answer
    assert extraction_messages[-1]["content"] == "message 4"
    assert "Form so far" not in extraction_messages[0]["content"]  # nothing to echo back


def test_dont_know_is_ignored_for_questions_we_did_not_ask(monkeypatch):
    run(monkeypatch,
        ext({"description": "daycare", "industry_category": "daycare", "state": "FL", "num_employees": 4}),
        ext({"annual_revenue_usd": 300_000}, unknown=["num_vehicles", "has_pool"]))
    state = new_state()
    handle_message(state, "home daycare in Florida, 4 staff")  # next we ask revenue

    handle_message(state, "300k")

    assert state.unknown == []
    assert state.last_asked == "num_vehicles"  # still asked, not skipped


def test_echoed_answers_are_not_sent_back_for_confirmation(monkeypatch):
    run(monkeypatch,
        ext({"description": "daycare", "industry_category": "daycare", "state": "FL", "num_employees": 4}),
        ext({"state": "FL", "num_employees": 4, "annual_revenue_usd": 300_000}, uncertain=["state", "num_employees"]))
    state = new_state()
    handle_message(state, "home daycare in Florida, 4 staff")

    handle_message(state, "300k")

    assert state.pending == {}
    assert state.answers["num_employees"] == 4




def test_repeating_the_same_rough_number_counts_as_confirming(monkeypatch):
    run(monkeypatch,
        ext({"annual_revenue_usd": 900_000}, uncertain=["annual_revenue_usd"]),
        ext({"annual_revenue_usd": 900_000}, uncertain=["annual_revenue_usd"]))
    state = new_state()
    handle_message(state, "around 900k")
    handle_message(state, "yeah around 900k")  # asked to check, gave the same number
    assert state.answers["annual_revenue_usd"] == 900_000
    assert state.pending == {}


def test_uncertain_only_applies_to_numbers(monkeypatch):
    run(monkeypatch, ext({"description": "roofing outfit", "industry_category": "roofing", "num_employees": 12},
                         uncertain=["description", "num_employees"]))
    state = new_state()
    handle_message(state, "roofing outfit, about a dozen guys")
    assert state.answers["description"] == "roofing outfit"
    assert state.pending == {"num_employees": 12}


def test_answers_we_did_not_ask_about_are_not_overwritten(monkeypatch):
    run(monkeypatch,
        ext({"description": "roofing outfit", "industry_category": "roofing", "state": "TX"}),
        ext({"num_employees": 8, "description": "3 trucks", "industry_category": "other"}))
    state = new_state()
    handle_message(state, "roofing outfit in Texas")   # next we ask employees

    handle_message(state, "8 guys and 3 trucks")

    assert state.answers["num_employees"] == 8
    assert state.answers["description"] == "roofing outfit"
    assert state.answers["industry_category"] == "roofing"


def test_the_answer_we_asked_about_can_be_changed(monkeypatch):
    run(monkeypatch,
        ext({"num_employees": 8}),
        ext({"num_employees": 9}))
    state = new_state()
    state.answers = {"num_employees": 5}
    state.last_asked = "num_employees"
    handle_message(state, "8")
    assert state.answers["num_employees"] == 8


def test_text_answers_must_come_from_what_the_customer_typed(monkeypatch):
    run(monkeypatch, ext({"description": "not known (a broker will follow up)", "business_name": "Acme Roofing"}))
    state = new_state()
    handle_message(state, "it's Acme Roofing")
    assert "description" not in state.answers
    assert state.answers["business_name"] == "Acme Roofing"


@pytest.mark.parametrize("value, message, ok", [
    ("roofing outfit", "we are a roofing outfit in texas", True),
    ("small home daycare", "I run a small home daycare in Florida", True),
    ("not known (a broker will follow up)", "actually we have 14 guys now", False),
    ("commercial bakery", "we sell cars", False),
])
def test_grounded(value, message, ok):
    assert grounded(value, message) is ok


# --- State machine ------------------------------------------------------------
def complete_roofer(monkeypatch, *later):
    """A roofer who gives everything at once, then the scripted later turns."""
    fake = run(monkeypatch, ext({**ROOFER, **ROOF_DETAILS}), *later)
    state = new_state()
    handle_message(state, ROOFER_MESSAGE)
    return state, fake


def test_complete_form_moves_to_confirming_with_a_code_built_summary(monkeypatch):
    state, fake = complete_roofer(monkeypatch)
    reply = state.messages[-1]["content"]
    assert state.status == "confirming"
    assert "Here's what I have" in reply
    assert "- State: TX" in reply
    assert "- Annual revenue: $1,200,000" in reply
    assert fake.reply_calls == []  # the summary is not written by the LLM


def test_approval_moves_to_done(monkeypatch):
    state, _ = complete_roofer(monkeypatch, ext(approve=True))
    assert handle_message(state, "yes, all correct") == DONE_MESSAGE
    assert state.status == "done"


def test_messages_after_done_cost_nothing(monkeypatch):
    state, fake = complete_roofer(monkeypatch, ext(approve=True))
    handle_message(state, "yes")
    calls = len(fake.calls)
    handle_message(state, "thanks!")
    assert len(fake.calls) == calls


def test_correction_while_confirming_updates_and_reshows_summary(monkeypatch):
    state, _ = complete_roofer(monkeypatch, ext({"num_employees": 9}), ext(approve=True))

    reply = handle_message(state, "actually we have 9 employees")
    assert state.status == "confirming"
    assert state.answers["num_employees"] == 9
    assert "- Employees: 9" in reply

    handle_message(state, "yes now it's right")
    assert state.status == "done"


def test_approval_with_a_change_is_not_done_yet(monkeypatch):
    state, _ = complete_roofer(monkeypatch, ext({"num_employees": 9}, approve=True))
    handle_message(state, "yes, except 9 employees")
    assert state.status == "confirming"


def test_unclear_answer_while_confirming_shows_summary_again(monkeypatch):
    state, _ = complete_roofer(monkeypatch, ext())
    reply = handle_message(state, "hmm")
    assert state.status == "confirming"
    assert "Is everything correct?" in reply


def test_changing_industry_at_the_summary_drops_the_old_industry_answers(monkeypatch):
    state, _ = complete_roofer(monkeypatch, ext({"industry_category": "daycare"}))
    handle_message(state, "sorry, I meant my daycare")
    assert "roof_max_height_ft" not in state.answers
    assert state.status == "collecting"
    assert state.last_asked == "licensed_capacity"


def test_correction_that_opens_a_new_question_goes_back_to_collecting(monkeypatch):
    state, _ = complete_roofer(monkeypatch, ext({"uses_subcontractors": True}))
    handle_message(state, "oh wait, we do use subs")
    assert state.status == "collecting"
    assert state.last_asked == "subcontracted_pct"


# --- The full conversation (the plan's "done when") ---------------------------
def test_scripted_roofing_conversation_reaches_done_with_valid_submission(monkeypatch):
    run(monkeypatch,
        ext({"description": "roofing crew", "industry_category": "roofing", "state": "Texas",
             "num_employees": 8}),
        ext(unknown=["annual_revenue_usd"]),                         # "no idea"
        ext({"num_vehicles": 3}),
        ext({"prior_claims_3yr": 0}),
        ext({"years_in_business": 6}),
        ext({"business_name": "Lone Star Roofing"}),
        ext({"roof_max_height_ft": 45}),
        ext({"roofing_work_type": "both"}),
        ext({"uses_torch_down": True}),
        ext({"uses_subcontractors": True}),
        ext({"subcontracted_pct": 20}, uncertain=["subcontracted_pct"]),  # "maybe 20%"
        ext(confirmed=["subcontracted_pct"]),
        ext(approve=True))
    state = new_state()
    for message in ["8-person roofing crew in Texas", "no idea", "3 trucks", "none", "6 years",
                    "Lone Star Roofing", "45 feet", "both", "yes", "yes", "maybe 20%", "yes", "yes"]:
        handle_message(state, message)

    assert state.status == "done"
    sub = build_submission(state)
    assert Submission.model_validate(sub.model_dump()) == sub  # round-trips as valid JSON
    assert sub.applicant.state == "TX"
    assert sub.exposures.annual_revenue_usd is None
    assert sub.unknown_by_customer == ["annual_revenue_usd"]
    assert sub.industry_details == {"roof_max_height_ft": 45, "roofing_work_type": "both",
                                    "uses_torch_down": True, "uses_subcontractors": True,
                                    "subcontracted_pct": 20}
    assert any("40 ft" in f for f in sub.flags) and any("Torch-down" in f for f in sub.flags)
    assert "Commercial Auto" in [r.policy for r in sub.recommended_coverage]
    assert state.questions_asked == 12  # 13 customer messages; the last one finished it
