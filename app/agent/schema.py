"""
The application, grouped like ACORD 125 (the standard US commercial
insurance application). Every value is checked here by Pydantic; nothing
the LLM returns is trusted until it passes these models.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent.questions import industry_categories

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}
STATE_CODES_BY_NAME = {name.lower(): code for code, name in US_STATES.items()}


def normalize_state(value: str) -> str:
    """'tx', 'TX' and 'Texas' all become 'TX'. Anything else is an error."""
    text = value.strip()
    if text.upper() in US_STATES:
        return text.upper()
    if text.lower() in STATE_CODES_BY_NAME:
        return STATE_CODES_BY_NAME[text.lower()]
    raise ValueError(f"{value!r} is not a US state")


class Group(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class Applicant(Group):
    business_name: Optional[str] = Field(None, min_length=1, max_length=200,
                                         description="Legal or trading name of the business")
    state: Optional[str] = Field(None, description="US state where the business operates, as a two-letter code (e.g. TX)")
    years_in_business: Optional[int] = Field(None, ge=0, le=200, description="Whole years the business has operated")

    @field_validator("state")
    @classmethod
    def check_state(cls, v):
        return None if v is None else normalize_state(v)


class Operations(Group):
    description: Optional[str] = Field(None, min_length=2, max_length=500,
                                       description="What the business does, as a short phrase in the customer's words")
    industry_category: Optional[str] = Field(None, description="Closest industry category for what the business does")

    @field_validator("industry_category")
    @classmethod
    def check_category(cls, v):
        if v is None:
            return v
        v = v.strip().lower()
        if v not in industry_categories():
            raise ValueError(f"{v!r} is not one of {industry_categories()}")
        return v


class Exposures(Group):
    num_employees: Optional[int] = Field(None, ge=0, le=100_000, description="Employees, not counting the owner")
    annual_revenue_usd: Optional[int] = Field(None, ge=0, le=10_000_000_000, description="Yearly revenue in US dollars")
    num_vehicles: Optional[int] = Field(None, ge=0, le=10_000, description="Vehicles used for the business")


class LossHistory(Group):
    prior_claims_3yr: Optional[int] = Field(None, ge=0, le=1_000,
                                            description="Insurance claims filed in the last 3 years")


# Which group each base field lives in, e.g. "state" -> Applicant.
GROUPS = {"applicant": Applicant, "operations": Operations,
          "exposures": Exposures, "loss_history": LossHistory}
BASE_FIELD_GROUP = {field: model for model in GROUPS.values() for field in model.model_fields}


def validate_base_field(field: str, value):
    """Check one base value with its group's model. Raises ValueError if invalid."""
    model = BASE_FIELD_GROUP[field]
    return getattr(model.model_validate({field: value}), field)


class Recommendation(BaseModel):
    policy: str
    why: str


class Submission(BaseModel):
    """The finished application a broker receives."""

    applicant: Applicant
    operations: Operations
    exposures: Exposures
    loss_history: LossHistory
    industry_details: dict[str, int | bool | str] = {}
    unknown_by_customer: list[str] = []         # fields the customer said they don't know
    recommended_coverage: list[Recommendation] = []
    flags: list[str] = []                       # hard-to-place risks for the broker
