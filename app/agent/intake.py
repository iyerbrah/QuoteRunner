"""
The intake agent: one customer message in, one reply out.

Each turn, in handle_message():
  1. extract()           LLM call 1: pull facts out of the customer's latest
                         message, as a forced tool call (structured JSON)
  2. apply_extraction()  check every value in code (Pydantic) and update the state
  3. next_reply()        plain rules decide what happens next:
                           - check any uncertain values with the customer
                           - else ask next_question()
                           - else show the summary and ask for approval
     write_question()    LLM call 2: phrase the question naturally.
                         Summaries and the final message are built in code,
                         so they can't misstate what was recorded.

States: collecting -> confirming -> done.
No web code here, so all of it can be tested with the LLM faked.

Try it:  .venv/bin/python -m app.agent.intake
"""

import json
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ValidationError

from app import llm
from app.agent.questions import (BASE_QUESTIONS, INDUSTRY_QUESTIONS, PRIORITY,
                                 Question, applicable_industry_questions,
                                 industry_categories, next_question, validate_answer)
from app.agent.rules import recommend_coverage, risk_flags
from app.agent.schema import BASE_FIELD_GROUP, GROUPS, US_STATES, Submission, validate_base_field

GREETING = ("Hi! I'll help you put together a business insurance application. "
            "Tell me about your business in a sentence or two: what you do, where, and how big.")
DONE_MESSAGE = ("Thanks, your application is complete. A licensed broker will review it "
                "and follow up with quotes.")
# How many recent chat messages each LLM call sees. Extraction sees only the
# last question and the customer's answer: with more, the model re-extracts
# the whole conversation and can resurrect or overwrite old answers.
EXTRACT_CONTEXT = 2
REPLY_CONTEXT = 4


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
class IntakeState(BaseModel):
    """Everything about one conversation. Saved as JSON between messages."""

    status: Literal["collecting", "confirming", "done"] = "collecting"
    answers: dict[str, Any] = {}      # field -> validated value (base and industry)
    pending: dict[str, Any] = {}      # uncertain values waiting for the customer to confirm
    unknown: list[str] = []           # fields the customer said they don't know
    last_asked: Optional[str] = None  # a field name, "pending" or "summary"
    questions_asked: int = 0
    messages: list[dict] = []         # [{"role": "user" | "assistant", "content": ...}]


def new_state() -> IntakeState:
    return IntakeState(messages=[{"role": "assistant", "content": GREETING}])


class Extraction(BaseModel):
    """What LLM call 1 returns. Parsed by Pydantic, so a malformed reply can't sneak in."""

    values: dict[str, Any] = {}
    uncertain_fields: list[str] = []
    unknown_fields: list[str] = []
    confirmed_fields: list[str] = []
    confirms_summary: bool = False


# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------
def all_fields() -> list[str]:
    """Every field the form knows about, across all industries."""
    return PRIORITY + [q.field for qs in INDUSTRY_QUESTIONS.values() for q in qs]


def find_question(field: str) -> Optional[Question]:
    if field in BASE_QUESTIONS:
        return BASE_QUESTIONS[field]
    return next((q for qs in INDUSTRY_QUESTIONS.values() for q in qs if q.field == field), None)


def belongs_to_business(field: str, answers: dict) -> bool:
    """Base fields always; industry fields only for this business's industry."""
    industry = INDUSTRY_QUESTIONS.get(answers.get("industry_category"), [])
    return field in BASE_FIELD_GROUP or any(q.field == field for q in industry)


def validate_field(field: str, value, answers: dict):
    """Check one value in code. Raises ValueError if it's invalid."""
    if field in BASE_FIELD_GROUP:
        return validate_base_field(field, value)
    question = next(q for q in INDUSTRY_QUESTIONS[answers["industry_category"]] if q.field == field)
    return validate_answer(question, value)


# ---------------------------------------------------------------------------
# LLM call 1: extraction
# ---------------------------------------------------------------------------
EXTRACT_PROMPT = """You extract facts for a US commercial insurance application.
You see the assistant's last message and the customer's reply. Record only
what the customer says in that reply; the assistant's message is context
(which question they are answering, or the summary they are checking).

Rules:
- Record only facts the customer actually stated. Never guess, assume or fill
  in typical values. If they didn't say it, leave it out.
- If a value is vague or approximate ("about a dozen", "maybe 2 trucks",
  "around a million", "25-30 ft", "roughly 10"), record your best reading in
  values AND list the field in uncertain_fields. Words like about, around,
  roughly, maybe, approximately, or a range always mean uncertain.
- If the customer says they don't know or can't say ("no idea", "not sure",
  "my accountant would know"), list the field in unknown_fields and leave it
  out of values.
- A plain "no", "none" or "zero" IS an answer (false or 0), not unknown.
- If the customer corrects an earlier answer, record the new value.
- Numbers are plain integers: "1.2M" -> 1200000, "a dozen" -> 12.
- industry_category: the closest category for what the business does, or
  "other" if none fits.
- confirmed_fields: fields from "Waiting for confirmation" that the customer
  just confirmed as correct.
- confirms_summary: true only if the customer approved the summary and asked
  for no changes.

Waiting for confirmation: {pending}
Last thing the assistant asked about: {last_asked}"""


def extraction_tool() -> dict:
    """The record_facts tool. Field types come from the Pydantic models and Questions."""
    properties = {field: BASE_FIELD_GROUP[field].model_json_schema()["properties"][field]
                  for field in PRIORITY}
    properties["industry_category"] = {"type": "string", "enum": industry_categories(),
                                       "description": ("Pick a specific industry for any kind of that business "
                                                       "(a Thai restaurant or pizza place is 'restaurant', a "
                                                       "preschool is 'daycare'). Use 'other' only if none fits.")}
    for category, questions in INDUSTRY_QUESTIONS.items():
        for q in questions:
            schema = q.json_schema()
            properties[q.field] = {**schema, "description": f"({category} only) {schema['description']}"}

    field_list = {"type": "array", "items": {"type": "string", "enum": all_fields()}}
    return {"type": "function", "function": {
        "name": "record_facts",
        "description": "Record facts the customer stated in their latest message.",
        "parameters": {
            "type": "object",
            "properties": {
                "values": {"type": "object", "properties": properties, "additionalProperties": False,
                           "description": "Facts stated in the latest message"},
                "uncertain_fields": {**field_list, "description": "Fields in values that were vague or approximate"},
                "unknown_fields": {**field_list, "description": "Fields the customer said they don't know"},
                "confirmed_fields": {**field_list, "description": "Waiting fields the customer confirmed"},
                "confirms_summary": {"type": "boolean",
                                     "description": "Customer approved the summary with no changes"},
            },
            "required": ["values", "uncertain_fields", "unknown_fields", "confirmed_fields", "confirms_summary"],
        },
    }}


def describe_last_asked(state: IntakeState) -> str:
    if state.last_asked == "summary":
        return "the full summary, asking the customer to approve it or correct it"
    if state.last_asked == "pending":
        return "checking the values waiting for confirmation"
    if state.last_asked:
        return f"{state.last_asked}: {find_question(state.last_asked).ask}"
    return "nothing yet (this is the customer's first message)"


def extract(state: IntakeState, session_id: Optional[str]) -> Extraction:
    system = EXTRACT_PROMPT.format(pending=json.dumps(state.pending), last_asked=describe_last_asked(state))
    message = llm.chat([{"role": "system", "content": system}, *state.messages[-EXTRACT_CONTEXT:]],
                       tools=[extraction_tool()],
                       tool_choice={"type": "function", "function": {"name": "record_facts"}},
                       model_role="agent", session_id=session_id)
    try:
        return Extraction.model_validate(json.loads(message.tool_calls[0].function.arguments))
    except (TypeError, IndexError, AttributeError, json.JSONDecodeError, ValidationError):
        return Extraction()  # unreadable output: record nothing, the same question gets asked again


# ---------------------------------------------------------------------------
# Updating the state (all in code)
# ---------------------------------------------------------------------------
def apply_extraction(state: IntakeState, ext: Extraction) -> list[str]:
    """Merge one extraction into the state. Returns fields whose values were invalid."""
    # 1. "I don't know": mark it, keep the field empty, never ask again.
    #    It's an answer to a question, so we only accept it for what we just
    #    asked; otherwise the model could skip questions by marking them unknown.
    for field in ext.unknown_fields:
        if field in just_asked(state) and field not in state.unknown:
            state.unknown.append(field)
            state.answers.pop(field, None)
            state.pending.pop(field, None)

    # 2. Uncertain values the customer has now confirmed.
    for field in ext.confirmed_fields:
        if field in state.pending:
            state.answers[field] = state.pending.pop(field)

    # 3. New values. Industry first, so we know which industry fields belong.
    #    Empty fields can be filled from anything the customer says, but a
    #    saved answer only changes when we asked about it (the question, a
    #    confirmation, or the summary). This stops the model overwriting
    #    answers by misreading a reply to some other question.
    can_change = set(all_fields()) if state.last_asked == "summary" else just_asked(state)
    latest_message = state.messages[-1]["content"]
    rejected = []
    for field in sorted(ext.values, key=lambda f: f != "industry_category"):
        value = ext.values[field]
        if value is None or field in ext.unknown_fields or not belongs_to_business(field, state.answers):
            continue
        try:
            value = validate_field(field, value, state.answers)
        except ValueError:
            rejected.append(field)
            continue
        if field in TEXT_FIELDS and not grounded(value, latest_message):
            continue  # text the customer didn't type (e.g. copied from our summary)
        if state.answers.get(field) == value:
            continue  # an echo of what we already have: nothing to do
        if field in state.answers and field not in can_change:
            continue  # would overwrite an answer we didn't ask about
        if field in state.unknown:
            state.unknown.remove(field)
        is_number = isinstance(value, int) and not isinstance(value, bool)
        # Asked to check a value and they gave the same number again
        # ("yeah, around 900k"): that's a confirmation, or we'd ask forever.
        repeated = state.last_asked == "pending" and state.pending.get(field) == value
        if field in ext.uncertain_fields and is_number and not repeated:
            state.pending[field] = value       # held back until the customer confirms
        else:
            state.answers[field] = value
            state.pending.pop(field, None)

    drop_inapplicable(state)
    return rejected


TEXT_FIELDS = {"description", "business_name"}


def grounded(value: str, message: str) -> bool:
    """A text answer counts only if at least half its words are in the customer's message."""
    words = re.findall(r"[a-z0-9]+", value.lower())
    said = set(re.findall(r"[a-z0-9]+", message.lower()))
    return bool(words) and 2 * sum(word in said for word in words) >= len(words)


def just_asked(state: IntakeState) -> set[str]:
    """The fields our last message asked about."""
    if state.last_asked == "pending":
        return set(state.pending)
    if state.last_asked in (None, "summary"):
        return set()
    return {state.last_asked}


def drop_inapplicable(state: IntakeState) -> None:
    """Forget industry answers that no longer apply (industry changed, or an only_if turned false)."""
    allowed = set(PRIORITY) | {q.field for q in applicable_industry_questions(state.answers)}
    for store in (state.answers, state.pending):
        for field in [f for f in store if f not in allowed]:
            del store[field]
    state.unknown = [f for f in state.unknown if f in allowed]


# ---------------------------------------------------------------------------
# Deciding and writing the reply
# ---------------------------------------------------------------------------
def handle_message(state: IntakeState, text: str, session_id: Optional[str] = None) -> str:
    """Process one customer message, update state in place, return the agent's reply."""
    state.messages.append({"role": "user", "content": text})
    if state.status == "done":
        reply = DONE_MESSAGE
    else:
        before = snapshot(state)
        ext = extract(state, session_id)
        rejected = apply_extraction(state, ext)
        reply = next_reply(state, ext, rejected, changed=snapshot(state) != before, session_id=session_id)
        if state.status != "done":
            state.questions_asked += 1
    state.messages.append({"role": "assistant", "content": reply})
    return reply


def snapshot(state: IntakeState) -> tuple:
    return dict(state.answers), dict(state.pending), list(state.unknown)


def next_reply(state: IntakeState, ext: Extraction, rejected: list[str], changed: bool,
               session_id: Optional[str]) -> str:
    # Confirming and nothing changed: either they approved, or show the summary again.
    if state.status == "confirming" and not changed:
        if ext.confirms_summary and not rejected:
            state.status, state.last_asked = "done", None
            return DONE_MESSAGE
        return summary_message(state, rejected)

    # Uncertain values come first: check them before moving on. Written in
    # code, so it always names exactly the values being checked.
    if state.pending:
        state.status, state.last_asked = "collecting", "pending"
        values = ", ".join(f"{label(f)}: {format_value(f, v)}" for f, v in state.pending.items())
        sorry = f"Sorry, I couldn't use that value for {', '.join(label(f) for f in rejected)}. " if rejected else ""
        return f"{sorry}Just to check, I have {values}. Is that right? If not, what's the exact figure?"

    question = next_question(state.answers, state.unknown)
    if question is None:  # nothing left to ask
        state.status, state.last_asked = "confirming", "summary"
        return summary_message(state, rejected)

    state.status, state.last_asked = "collecting", question.field
    goal = f'Ask this question in your own words: "{question.ask}"'
    return write_question(state, goal, rejected, question.ask, session_id)


REPLY_PROMPT = """You are a friendly intake assistant for a US commercial insurance
broker serving small businesses. Write your next message to the customer:
under 40 words, plain language, no jargon.

Your task: {goal}
{retry}
Ask only that one thing. Don't state facts about their business and never
promise prices or coverage."""


def write_question(state: IntakeState, goal: str, rejected: list[str], fallback: str,
                   session_id: Optional[str]) -> str:
    """LLM call 2: phrase the question. Falls back to our own wording if the reply is empty."""
    retry = (f"Their answer for {', '.join(label(f) for f in rejected)} wasn't valid; "
             "politely say so before asking." if rejected else "")
    system = REPLY_PROMPT.format(goal=goal, retry=retry)
    message = llm.chat([{"role": "system", "content": system}, *state.messages[-REPLY_CONTEXT:]],
                       model_role="agent", session_id=session_id)
    return (message.content or "").strip() or fallback


def label(field: str) -> str:
    question = find_question(field)
    return question.label if question else field


def format_value(field: str, value) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if field == "annual_revenue_usd":
        return f"${value:,}"
    if field == "state":
        return f"{US_STATES[value]} ({value})"  # "Oregon (OR)": customers recognise the name
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def summary_message(state: IntakeState, rejected: list[str]) -> str:
    """Built in code from the validated answers, so it can't misstate anything."""
    lines = []
    if rejected:
        lines.append(f"Sorry, I couldn't use that value for {', '.join(label(f) for f in rejected)}.")
    lines.append("Here's what I have:")
    fields = PRIORITY + [q.field for q in applicable_industry_questions(state.answers)]
    for field in fields:
        if field == "industry_category" and state.answers.get(field) == "other":
            continue  # an internal label; "Industry: other" just confuses a bakery owner
        if field in state.answers:
            lines.append(f"- {label(field)}: {format_value(field, state.answers[field])}")
        elif field in state.unknown:
            lines.append(f"- {label(field)}: not known (a broker will follow up)")
    lines.append("")
    lines.append("Is everything correct? Reply yes, or tell me what to change.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The finished application
# ---------------------------------------------------------------------------
def build_submission(state: IntakeState) -> Submission:
    """Assemble the ACORD-style Submission. Pydantic validates it all again here."""
    a = state.answers
    groups = {name: model(**{f: a[f] for f in model.model_fields if f in a})
              for name, model in GROUPS.items()}
    industry = {q.field: a[q.field] for q in applicable_industry_questions(a) if q.field in a}
    return Submission(**groups, industry_details=industry, unknown_by_customer=list(state.unknown),
                      recommended_coverage=recommend_coverage(a), flags=risk_flags(a))


if __name__ == "__main__":
    import uuid

    state, session = new_state(), f"cli-{uuid.uuid4().hex[:8]}"
    print("Agent:", GREETING)
    while state.status != "done":
        try:
            text = input("You: ")
        except EOFError:
            break
        print("Agent:", handle_message(state, text, session_id=session))
    print(build_submission(state).model_dump_json(indent=2))
