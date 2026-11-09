"""
Three FICTIONAL insurance carriers, each with its own quote portal.

Real carriers only give portal access to licensed, appointed brokers, so
these stand in for them. They are built to differ the way real portals do:
different field names and labels, revenue in dollars on one and in thousands
on another, their own class names, and their own appetite and pricing rules.

The submission agent (app/agent/submit.py) knows none of this: it reads each
portal's HTML form like any other website. This module is the carriers' side:
what their forms ask for, and how they decide.
"""

from typing import Literal, Optional

from pydantic import BaseModel

from app.agent.schema import US_STATES

STATE_CODES = list(US_STATES)


class PortalField(BaseModel):
    name: str
    label: str
    type: Literal["text", "number", "select"] = "text"
    options: list[str] = []
    required: bool = False
    hint: str = ""


class Decision(BaseModel):
    status: Literal["quoted", "declined"]
    premium: Optional[int] = None
    reason: str = ""


def _yes_no(name, label, hint="") -> PortalField:
    return PortalField(name=name, label=label, type="select", options=["yes", "no"], hint=hint)


def _premium(revenue: int, rate_per_1000: float, factors: float, per_employee: int,
             employees: int, minimum: int) -> int:
    """premium = revenue/1000 x rate x factors + per-employee charge, never below the minimum."""
    raw = revenue / 1000 * rate_per_1000 * factors + per_employee * employees
    return max(minimum, round(raw / 10) * 10)


STATE_FACTORS = {"CA": 1.25, "NY": 1.3, "FL": 1.15, "TX": 1.1, "NJ": 1.15, "IL": 1.05}


# ---------------------------------------------------------------------------
# Granite Mutual: a standard-market carrier. Cheap, picky.
# ---------------------------------------------------------------------------
GRANITE_CLASSES = ["Roofing contractor", "Restaurant", "Child day care center", "Other small business"]

GRANITE_FIELDS = [
    PortalField(name="insured_name", label="Named insured", required=True),
    PortalField(name="risk_state", label="State of operations", type="select", options=STATE_CODES, required=True),
    PortalField(name="business_class", label="Business class", type="select", options=GRANITE_CLASSES, required=True),
    PortalField(name="full_time_employees", label="Number of employees", type="number", required=True),
    PortalField(name="gross_sales_thousands", label="Annual gross sales", type="number", required=True,
                hint="In thousands of US dollars: enter 1250 for $1,250,000"),
    PortalField(name="owned_autos", label="Owned autos", type="number"),
    PortalField(name="losses_3yr", label="Losses in the past 3 years", type="number", required=True),
    PortalField(name="yrs_in_business", label="Years in business", type="number"),
    PortalField(name="max_height_ft", label="Maximum working height (feet)", type="number",
                hint="Roofing contractors only"),
    _yes_no("hot_work", "Torch-down or other hot work?", "Roofing contractors only"),
]
GRANITE_RATES = {"Roofing contractor": 9.5, "Restaurant": 4.2, "Child day care center": 5.0,
                 "Other small business": 2.4}


def granite_decide(d: dict) -> Decision:
    if d["business_class"] == "Roofing contractor" and (d.get("max_height_ft") or 0) > 40:
        return Decision(status="declined", reason="Working heights above 40 ft are outside our appetite.")
    if d.get("hot_work") == "yes":
        return Decision(status="declined", reason="We don't write torch-down or other hot work.")
    if d["losses_3yr"] >= 2:
        return Decision(status="declined", reason="Two or more losses in 3 years are outside our appetite.")
    premium = _premium(d["gross_sales_thousands"] * 1000, GRANITE_RATES[d["business_class"]],
                       STATE_FACTORS.get(d["risk_state"], 1.0), 85, d["full_time_employees"], 750)
    return Decision(status="quoted", premium=premium)


# ---------------------------------------------------------------------------
# Harborline Specialty: an excess & surplus (E&S) carrier. Takes hard risks, charges for them.
# ---------------------------------------------------------------------------
HARBORLINE_CLASSES = ["R-100 Roofing", "F-210 Restaurant / bar", "C-330 Childcare", "G-900 General small business"]

HARBORLINE_FIELDS = [
    PortalField(name="applicant_legal_name", label="Applicant legal name", required=True),
    PortalField(name="location_state", label="Location state", required=True, hint="Two-letter code, e.g. TX"),
    PortalField(name="operations", label="Description of operations", required=True),
    PortalField(name="class_code", label="Class code", type="select", options=HARBORLINE_CLASSES, required=True),
    PortalField(name="annual_revenue", label="Annual revenue (USD)", type="number", required=True),
    PortalField(name="headcount", label="Headcount", type="number", required=True),
    PortalField(name="prior_claims", label="Claims in the last 3 years", type="number", required=True),
    PortalField(name="roof_height_ft", label="Roof height (ft)", type="number"),
    _yes_no("torch_down", "Torch-down work"),
    PortalField(name="subcontracted_percent", label="Work subcontracted (%)", type="number"),
    PortalField(name="liquor_percent", label="Liquor share of sales (%)", type="number"),
]
HARBORLINE_RATES = {"R-100 Roofing": 14.0, "F-210 Restaurant / bar": 6.5, "C-330 Childcare": 7.5,
                    "G-900 General small business": 3.5}


def harborline_decide(d: dict) -> Decision:
    factors = 1 + 0.15 * d["prior_claims"]
    if (d.get("roof_height_ft") or 0) > 40:
        factors *= 1.35
    if d.get("torch_down") == "yes":
        factors *= 1.25
    if (d.get("liquor_percent") or 0) > 50:
        factors *= 1.4
    if d.get("location_state", "").upper() not in US_STATES:
        return Decision(status="declined", reason="We only write risks located in the United States.")
    premium = _premium(d["annual_revenue"], HARBORLINE_RATES[d["class_code"]], factors, 110,
                       d["headcount"], 1500)
    return Decision(status="quoted", premium=premium)


# ---------------------------------------------------------------------------
# Lakeside Commercial: a main-street business owner's policy (BOP) carrier.
# ---------------------------------------------------------------------------
LAKESIDE_TYPES = ["Restaurant", "Daycare", "Retail store", "Office", "Other"]

LAKESIDE_FIELDS = [
    PortalField(name="business_name", label="Business name", required=True),
    PortalField(name="state", label="State", type="select", options=STATE_CODES, required=True),
    PortalField(name="business_type", label="Type of business", type="select", options=LAKESIDE_TYPES, required=True),
    PortalField(name="revenue", label="Annual revenue ($)", type="number", required=True),
    PortalField(name="employees", label="Employees", type="number", required=True),
    PortalField(name="prior_losses", label="Prior losses (3 years)", type="number", required=True),
    PortalField(name="seats", label="Seats", type="number", hint="Restaurants only"),
    PortalField(name="alcohol_percent", label="Alcohol as % of sales", type="number", hint="Restaurants only"),
    PortalField(name="children_capacity", label="Licensed capacity", type="number", hint="Daycares only"),
    _yes_no("swimming_pool", "Swimming pool on premises", "Daycares only"),
]
LAKESIDE_RATES = {"Restaurant": 3.6, "Daycare": 4.4, "Retail store": 2.0, "Office": 1.5}


def lakeside_decide(d: dict) -> Decision:
    if d["business_type"] not in LAKESIDE_RATES:
        return Decision(status="declined",
                        reason="We only write restaurants, daycares, retail stores and offices.")
    if (d.get("alcohol_percent") or 0) > 40:
        return Decision(status="declined", reason="Alcohol above 40% of sales is outside our BOP program.")
    if d.get("swimming_pool") == "yes":
        return Decision(status="declined", reason="We don't write daycares with a pool.")
    if d["prior_losses"] >= 3:
        return Decision(status="declined", reason="Three or more losses in 3 years are outside our appetite.")
    if d["revenue"] > 5_000_000:
        return Decision(status="declined", reason="Our BOP program is for businesses under $5M in revenue.")
    premium = _premium(d["revenue"], LAKESIDE_RATES[d["business_type"]],
                       STATE_FACTORS.get(d["state"], 1.0), 60, d["employees"], 600)
    return Decision(status="quoted", premium=premium)


# ---------------------------------------------------------------------------
class Carrier(BaseModel):
    slug: str
    name: str
    kind: str          # one line on the portal page
    color: str         # portal accent colour
    prefix: str        # quote reference prefix
    fields: list[PortalField]

    def decide(self, data: dict) -> Decision:
        return DECIDE[self.slug](data)


CARRIERS = {c.slug: c for c in [
    Carrier(slug="granite", name="Granite Mutual", kind="Standard commercial lines", color="#3b4a6b",
            prefix="GM", fields=GRANITE_FIELDS),
    Carrier(slug="harborline", name="Harborline Specialty", kind="Excess & surplus lines", color="#7a2e3b",
            prefix="HS", fields=HARBORLINE_FIELDS),
    Carrier(slug="lakeside", name="Lakeside Commercial", kind="Business owner's policies", color="#2f6b5a",
            prefix="LC", fields=LAKESIDE_FIELDS),
]}
DECIDE = {"granite": granite_decide, "harborline": harborline_decide, "lakeside": lakeside_decide}


def validate_form(carrier: Carrier, form: dict) -> tuple[dict, list[str]]:
    """What a portal does with a submitted form: type-check it. Returns (clean data, errors)."""
    data, errors = {}, []
    for f in carrier.fields:
        raw = (form.get(f.name) or "").strip()
        if not raw:
            if f.required:
                errors.append(f"{f.label} is required.")
            continue
        if f.type == "number":
            try:
                value = int(raw)
                if value < 0:
                    raise ValueError
            except ValueError:
                errors.append(f"{f.label} must be a whole number.")
                continue
            data[f.name] = value
        elif f.type == "select" and raw not in f.options:
            errors.append(f"{f.label}: {raw!r} is not an option.")
        else:
            data[f.name] = raw
    return data, errors
