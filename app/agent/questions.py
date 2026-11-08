"""
What the agent asks, and in what order.

  - PRIORITY + BASE_QUESTIONS: the fields every business is asked about
  - INDUSTRY_QUESTIONS: typed follow-ups per industry. Adding an industry
    means adding one entry here; no other code changes.
  - next_question(): the first question that is still open and applies

Pure data and pure functions: no LLM, no database.
"""

from typing import Annotated, Callable, Literal, Optional

from pydantic import BaseModel, Field, TypeAdapter


class Question(BaseModel):
    field: str                    # key in the answers dict
    label: str                    # short name, used in the summary
    ask: str                      # what we want to know, in plain words
    type: Literal["int", "bool", "choice", "text"] = "text"
    choices: list[str] = []       # allowed values when type is "choice"
    min: Optional[int] = None     # bounds when type is "int"
    max: Optional[int] = None
    # Ask only when this returns True for the answers so far.
    only_if: Optional[Callable[[dict], bool]] = None

    def json_schema(self) -> dict:
        """How this field is described to the LLM in the extraction tool."""
        if self.type == "int":
            schema = {"type": "integer"}
            if self.min is not None:
                schema["minimum"] = self.min
            if self.max is not None:
                schema["maximum"] = self.max
        elif self.type == "bool":
            schema = {"type": "boolean"}
        elif self.type == "choice":
            schema = {"type": "string", "enum": self.choices}
        else:
            schema = {"type": "string"}
        return {**schema, "description": self.ask}


def validate_answer(question: Question, value):
    """Check one industry answer against its question. Raises ValueError if invalid."""
    if question.type == "int":
        return TypeAdapter(Annotated[int, Field(ge=question.min, le=question.max)]).validate_python(value)
    if question.type == "bool":
        return TypeAdapter(bool).validate_python(value)
    text = TypeAdapter(str).validate_python(value).strip()
    if question.type == "choice":
        text = text.lower()
        if text not in question.choices:
            raise ValueError(f"{value!r} is not one of {question.choices}")
    elif not text:
        raise ValueError("empty answer")
    return text


# ---------------------------------------------------------------------------
# Industry follow-ups
# ---------------------------------------------------------------------------
INDUSTRY_QUESTIONS: dict[str, list[Question]] = {
    "roofing": [
        Question(field="roof_max_height_ft", label="Tallest roof worked on (ft)", type="int", min=0, max=1000,
                 ask="What's the tallest roof height you work on, in feet?"),
        Question(field="roofing_work_type", label="Residential or commercial", type="choice",
                 choices=["residential", "commercial", "both"],
                 ask="Is your work residential, commercial, or both?"),
        Question(field="uses_torch_down", label="Torch-down / hot work", type="bool",
                 ask="Do you do any torch-down or other hot work?"),
        Question(field="uses_subcontractors", label="Uses subcontractors", type="bool",
                 ask="Do you use subcontractors?"),
        Question(field="subcontracted_pct", label="Work subcontracted (%)", type="int", min=0, max=100,
                 ask="Roughly what percent of your work is done by subcontractors?",
                 only_if=lambda a: a.get("uses_subcontractors") is True),
    ],
    "daycare": [
        Question(field="licensed_capacity", label="Licensed capacity (children)", type="int", min=1, max=1000,
                 ask="How many children are you licensed to care for?"),
        Question(field="state_licensed", label="State licensed", type="bool",
                 ask="Is the daycare licensed by the state?"),
        Question(field="transports_children", label="Transports children", type="bool",
                 ask="Do you ever drive children anywhere, like field trips or school pickups?"),
        Question(field="has_pool", label="Pool on premises", type="bool",
                 ask="Is there a swimming pool on the premises?"),
    ],
    "restaurant": [
        Question(field="seating_capacity", label="Seats", type="int", min=0, max=5000,
                 ask="How many seats does the restaurant have?"),
        Question(field="serves_alcohol", label="Serves alcohol", type="bool",
                 ask="Do you serve alcohol?"),
        Question(field="alcohol_sales_pct", label="Alcohol share of sales (%)", type="int", min=0, max=100,
                 ask="What percent of your sales comes from alcohol?",
                 only_if=lambda a: a.get("serves_alcohol") is True),
        Question(field="has_deep_fryers", label="Deep fryers", type="bool",
                 ask="Do you use deep fryers?"),
        Question(field="offers_delivery", label="Offers delivery", type="bool",
                 ask="Do you deliver food with your own drivers?"),
    ],
}


def industry_categories() -> list[str]:
    """Every industry we have follow-ups for, plus 'other'."""
    return [*INDUSTRY_QUESTIONS, "other"]


def applicable_industry_questions(answers: dict) -> list[Question]:
    """This business's industry questions whose only_if condition holds."""
    questions = INDUSTRY_QUESTIONS.get(answers.get("industry_category"), [])
    return [q for q in questions if q.only_if is None or q.only_if(answers)]


# ---------------------------------------------------------------------------
# Base questions, asked of every business
# ---------------------------------------------------------------------------
# Industry first: it decides which follow-ups apply. The business name is
# last because the customer usually gives it anyway and it changes nothing.
PRIORITY = [
    "description", "industry_category", "state", "num_employees",
    "annual_revenue_usd", "num_vehicles", "prior_claims_3yr",
    "years_in_business", "business_name",
]

# Base fields are validated by the models in schema.py, so these only
# need a label and the wording.
BASE_QUESTIONS = {q.field: q for q in [
    Question(field="description", label="What the business does", ask="What does your business do?"),
    Question(field="industry_category", label="Industry",
             ask=f"Which best describes your business: {', '.join(INDUSTRY_QUESTIONS)}, or something else?"),
    Question(field="state", label="State", ask="Which US state is the business in?"),
    Question(field="num_employees", label="Employees", ask="How many employees do you have, not counting yourself?"),
    Question(field="annual_revenue_usd", label="Annual revenue", ask="What's your annual revenue?"),
    Question(field="num_vehicles", label="Business vehicles", ask="How many vehicles do you use for the business?"),
    Question(field="prior_claims_3yr", label="Claims in last 3 years",
             ask="How many insurance claims has the business filed in the last 3 years?"),
    Question(field="years_in_business", label="Years in business", ask="How many years have you been in business?"),
    Question(field="business_name", label="Business name", ask="What's the name of the business?"),
]}


def next_question(answers: dict, unknown: list[str]) -> Optional[Question]:
    """The first open question that applies, or None when the form is complete."""
    def is_open(field):
        return field not in answers and field not in unknown

    for field in PRIORITY:
        if is_open(field):
            return BASE_QUESTIONS[field]
    for question in applicable_industry_questions(answers):
        if is_open(question.field):
            return question
    return None
