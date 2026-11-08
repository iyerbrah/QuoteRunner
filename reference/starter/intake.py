"""
Core logic for the insurance intake agent.

Three parts, kept separate on purpose:
  1. BusinessApplication  - the form we want to fill (Pydantic = validation)
  2. extract_fields()     - LLM call #1: pull facts out of the chat into the form
     next_reply()         - LLM call #2: write the next friendly question
  3. recommend_coverage() - plain Python rules (no LLM) that suggest policies

The UI lives in app.py. This file has no Streamlit code, so it can be tested alone.
"""

import json
import os
from typing import Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

MODEL = os.getenv("INTAKE_MODEL", "claude-haiku-5-5")

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}


# ---------------------------------------------------------------------------
# 1. The form
# ---------------------------------------------------------------------------
class BusinessApplication(BaseModel):
    """Everything a broker needs for a first quote. All fields start empty."""

    business_name: Optional[str] = Field(None, description="Legal or trading name of the business")
    industry: Optional[str] = Field(None, description="What the business does, e.g. 'roofing contractor', 'daycare'")
    state: Optional[str] = Field(None, description="Two-letter US state code where the business operates, e.g. 'TX'")
    years_in_business: Optional[int] = Field(None, ge=0, le=200, description="Whole years operating")
    num_employees: Optional[int] = Field(None, ge=0, description="Number of employees, not counting the owner")
    annual_revenue_usd: Optional[int] = Field(None, ge=0, description="Yearly revenue in US dollars")
    num_vehicles: Optional[int] = Field(None, ge=0, description="Vehicles used for business")
    prior_claims_3yr: Optional[int] = Field(None, ge=0, description="Insurance claims filed in the last 3 years")

    @field_validator("state")
    @classmethod
    def check_state(cls, v):
        if v is None:
            return v
        v = v.strip().upper()
        if v not in US_STATES:
            raise ValueError(f"'{v}' is not a US state code")
        return v

    def missing_fields(self) -> list[str]:
        return [name for name, value in self.model_dump().items() if value is None]

    def progress(self) -> float:
        total = len(type(self).model_fields)
        return (total - len(self.missing_fields())) / total


def merge_updates(current: BusinessApplication, updates: dict) -> tuple[BusinessApplication, list[str]]:
    """
    Merge new values into the form. Values that fail validation are dropped
    (not saved) and their field names are returned, so the bot can re-ask.
    """
    updates = {k: v for k, v in updates.items() if v is not None and k in type(current).model_fields}
    rejected: list[str] = []
    while True:
        try:
            merged = BusinessApplication.model_validate({**current.model_dump(), **updates})
            return merged, rejected
        except ValidationError as e:
            bad = {err["loc"][0] for err in e.errors() if err["loc"]}
            bad &= set(updates)
            if not bad:  # error isn't from the new values; keep the old form
                return current, list(updates)
            for field in bad:
                updates.pop(field)
                rejected.append(field)


# ---------------------------------------------------------------------------
# 2. The two LLM calls
# ---------------------------------------------------------------------------
def _api_messages(chat: list[dict]) -> list[dict]:
    """The API needs the conversation to start with the user, so drop a leading greeting."""
    msgs = [{"role": m["role"], "content": m["content"]} for m in chat]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs


EXTRACT_TOOL = {
    "name": "record_fields",
    "description": "Record business facts the USER has clearly stated. Leave out anything not stated.",
    "input_schema": BusinessApplication.model_json_schema(),
}


def extract_fields(client, chat: list[dict]) -> dict:
    """LLM call #1: read the chat and return only the facts the user gave."""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=(
            "You extract data for a commercial insurance application. "
            "Only record facts the user actually said. Never guess. "
            "Convert numbers to plain integers (e.g. '1.2M' -> 1200000). "
            "Convert US state names to two-letter codes."
        ),
        messages=_api_messages(chat),
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "record_fields"},  # force structured output
    )
    for block in resp.content:
        if block.type == "tool_use":
            return dict(block.input)
    return {}


def next_reply(client, chat: list[dict], app: BusinessApplication, rejected: list[str]) -> str:
    """LLM call #2: write the next short, friendly message to the customer."""
    missing = app.missing_fields()
    if missing:
        goal = f"Ask for the next missing item: '{missing[0]}'. Ask only ONE question."
    else:
        goal = ("All details are collected. Thank them, give a two-line summary, "
                "and say a licensed broker will follow up with quotes.")
    retry = f"These answers were invalid, politely ask again: {rejected}. " if rejected else ""

    resp = client.messages.create(
        model=MODEL,
        max_tokens=300,
        system=(
            "You are a friendly intake assistant for a US commercial insurance broker "
            "serving small businesses. Keep replies under 50 words, plain language, no jargon. "
            "Never promise prices or coverage; a licensed broker does that.\n"
            f"Form so far: {json.dumps(app.model_dump())}\n{retry}{goal}"
        ),
        messages=_api_messages(chat),
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


# ---------------------------------------------------------------------------
# 3. Coverage suggestions: rules, not the LLM
# ---------------------------------------------------------------------------
PROFESSIONAL_KEYWORDS = ("consult", "account", "design", "software", "marketing",
                         "legal", "architect", "engineer", "agency", "it services")


def recommend_coverage(app: BusinessApplication) -> list[dict]:
    """Deterministic, explainable suggestions. A licensed broker makes the final call."""
    recs = [{"policy": "General Liability",
             "why": "Covers injury or property damage to other people. Almost every business needs it."}]

    if app.num_employees:
        if app.state == "TX":
            why = "Texas lets most private employers opt out, but going without it is risky."
        else:
            why = "Most states require it once you have employees (thresholds vary by state)."
        recs.append({"policy": "Workers' Compensation", "why": why})

    if app.num_vehicles:
        recs.append({"policy": "Commercial Auto",
                     "why": "Personal auto policies usually don't cover vehicles used for business."})

    industry = (app.industry or "").lower()
    if any(word in industry for word in PROFESSIONAL_KEYWORDS):
        recs.append({"policy": "Professional Liability (E&O)",
                     "why": "Covers claims that your advice or work caused a client financial loss."})

    if app.annual_revenue_usd is not None and app.annual_revenue_usd < 5_000_000:
        recs.append({"policy": "Business Owner's Policy (BOP)",
                     "why": "Bundles liability and property cover, often cheaper for small businesses."})

    if app.prior_claims_3yr is not None and app.prior_claims_3yr >= 2:
        recs.append({"policy": "Flag: hard-to-place risk",
                     "why": "Multiple recent claims; standard insurers may decline, so the broker may "
                            "need specialty (E&S) markets."})
    return recs
