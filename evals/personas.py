"""
50 simulated business owners, each with ground-truth facts.

Built from a fixed seed, so the list is identical on every run and every
machine. Each persona has:
  - truth:   the real answer for every field that applies to the business
  - unknown: 1-2 facts the owner genuinely doesn't know (not in truth)
  - style:   how they talk (terse, typo-heavy, vague numbers, ...)
  - change:  for the "changes_answer" style, a wrong value they say first

Field names and value types are the agent's own, so scoring is a direct
comparison. Run it to see them:  .venv/bin/python -m evals.personas
"""

import random
from typing import Any, Optional

from pydantic import BaseModel

from app.agent.questions import PRIORITY, applicable_industry_questions
from app.agent.schema import US_STATES

SEED = 2026
COUNT = 50

STYLES = {
    "terse": "Answer in as few words as possible, often just a number, yes or no. No greetings.",
    "rambling": "You ramble: add chit-chat and little stories around your answers. The facts you give stay accurate.",
    "typos": "You type fast on your phone: all lowercase, frequent typos and abbreviations "
             "(like 'emploees', 'abt', 'yrs', 'thx'). Numbers stay correct.",
    "many_facts": "In your FIRST message, give as many of your facts as you can at once. "
                  "After that, answer each question briefly.",
    "vague_numbers": "The first time you give any number, say it loosely ('about a dozen', 'roughly 900k', "
                     "'a couple'). If asked to confirm or for the exact figure, give the exact number from your facts.",
    "state_names": "Always spell out your state's full name (like 'North Carolina'), never the two-letter code, "
                   "and mention your city.",
    "changes_answer": "You talk normally, but you will make one mistake on purpose (see below).",
}

CITIES = {"TX": "Houston", "FL": "Orlando", "CA": "Fresno", "NY": "Buffalo", "NC": "Raleigh", "GA": "Savannah",
          "OH": "Columbus", "IL": "Peoria", "AZ": "Tucson", "CO": "Denver", "WA": "Spokane", "TN": "Nashville"}

NAME_PREFIXES = ["Lone Star", "Bayou", "Summit", "Blue Ridge", "Harbor", "Prairie", "Golden", "Cedar",
                 "Maple", "Riverside", "Sunrise", "Peak", "Coastal", "Pioneer", "Evergreen", "Red Oak"]

OTHER_BUSINESSES = [("bakery", "Bakery"), ("plumbing contractor", "Plumbing"),
                    ("landscaping company", "Landscaping"), ("hair salon", "Salon"),
                    ("auto repair shop", "Auto Repair"), ("marketing consultancy", "Marketing"),
                    ("dog grooming shop", "Pet Grooming"), ("print shop", "Printing")]

CATEGORIES = ["roofing", "daycare", "restaurant", "other"]


class Persona(BaseModel):
    id: int
    category: str                      # the true industry_category
    style: str
    truth: dict[str, Any]              # field -> true value (only facts the owner knows)
    unknown: list[str]                 # fields the owner doesn't know
    change: Optional[dict] = None      # {"field": ..., "first": wrong value said first}

    def expected_fields(self) -> list[str]:
        """Every field that applies to this business, known or not."""
        return PRIORITY + [q.field for q in applicable_industry_questions(self.truth)]


def _round(value: int, step: int) -> int:
    return max(step, round(value / step) * step)


def _industry_facts(category: str, rng: random.Random, employees: int) -> tuple[str, str, dict]:
    """Description, name suffix and industry-specific facts for one business."""
    if category == "roofing":
        description = rng.choice(["residential roofing contractor", "commercial roofing company",
                                  "roof repair and replacement business"])
        subs = rng.random() < 0.5
        facts = {"roof_max_height_ft": rng.choice([20, 25, 30, 35, 45, 60]),
                 "roofing_work_type": rng.choice(["residential", "commercial", "both"]),
                 "uses_torch_down": rng.random() < 0.3, "uses_subcontractors": subs,
                 "num_vehicles": rng.randint(1, max(2, employees // 3))}
        if subs:
            facts["subcontracted_pct"] = rng.choice([10, 15, 20, 25, 30, 40, 60, 75])
        return description, rng.choice(["Roofing", "Roofing Co", "Roof Pros"]), facts

    if category == "daycare":
        description = rng.choice(["home daycare", "childcare center", "preschool and daycare"])
        transports = rng.random() < 0.4
        facts = {"licensed_capacity": rng.choice([6, 12, 24, 40, 60, 90, 120]),
                 "state_licensed": rng.random() < 0.9, "transports_children": transports,
                 "has_pool": rng.random() < 0.15, "num_vehicles": rng.randint(1, 3) if transports else 0}
        return description, rng.choice(["Daycare", "Kids Academy", "Learning Center"]), facts

    if category == "restaurant":
        description = rng.choice(["Mexican restaurant", "pizza place", "family diner", "sushi bar",
                                  "BBQ restaurant", "Thai restaurant"])
        alcohol, delivery = rng.random() < 0.6, rng.random() < 0.3
        facts = {"seating_capacity": rng.choice([24, 40, 60, 80, 120, 180]), "serves_alcohol": alcohol,
                 "has_deep_fryers": rng.random() < 0.7, "offers_delivery": delivery,
                 "num_vehicles": rng.randint(1, 3) if delivery else 0}
        if alcohol:
            facts["alcohol_sales_pct"] = rng.choice([10, 15, 20, 30, 40, 55, 70])
        return description, rng.choice(["Kitchen", "Grill", "Cantina", "Eatery"]), facts

    description, suffix = rng.choice(OTHER_BUSINESSES)
    return description, suffix, {"num_vehicles": rng.randint(0, 4)}


def make_persona(persona_id: int) -> Persona:
    """One persona. Its own seeded generator keeps it stable however many we build."""
    rng = random.Random(SEED + persona_id)
    category = CATEGORIES[persona_id % len(CATEGORIES)]
    style = list(STYLES)[persona_id % len(STYLES)]  # 4 and 7 share no factor, so every mix appears

    employees = rng.randint(2, 40)
    description, suffix, industry = _industry_facts(category, rng, employees)
    truth = {
        "description": description,
        "industry_category": category,
        "state": rng.choice(list(US_STATES)),
        "num_employees": employees,
        "annual_revenue_usd": _round(employees * rng.randint(60_000, 160_000), 10_000),
        "prior_claims_3yr": rng.choice([0, 0, 0, 0, 1, 1, 2, 3]),
        "years_in_business": rng.randint(1, 35),
        "business_name": f"{rng.choice(NAME_PREFIXES)} {suffix}",
        **industry,
    }

    # 1-2 facts an owner plausibly doesn't know.
    could_not_know = [f for f in ["annual_revenue_usd", "prior_claims_3yr", "subcontracted_pct",
                                  "alcohol_sales_pct"] if f in truth]
    unknown = rng.sample(could_not_know, k=rng.choice([1, 2]))
    for field in unknown:
        del truth[field]

    change = None
    if style == "changes_answer":
        field = rng.choice(["num_employees", "num_vehicles", "years_in_business"])
        change = {"field": field, "first": truth[field] + rng.choice([2, 3, 5])}

    return Persona(id=persona_id, category=category, style=style, truth=truth, unknown=unknown, change=change)


def build_personas(count: int = COUNT) -> list[Persona]:
    return [make_persona(i) for i in range(1, count + 1)]


if __name__ == "__main__":
    for p in build_personas():
        print(f"#{p.id:<3}{p.category:<11}{p.style:<15}{p.truth['state']}  unknown={p.unknown}"
              + (f"  change={p.change}" if p.change else ""))
