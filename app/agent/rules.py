"""
Coverage suggestions and hard-to-place flags: plain Python rules, no LLM.

They must be predictable and explainable, and a licensed broker makes the
final call. Both functions take the flat answers dict.
"""

from app.agent.schema import Recommendation

PROFESSIONAL_KEYWORDS = ("consult", "account", "design", "software", "marketing",
                         "legal", "architect", "engineer", "agency", "it services")


def recommend_coverage(a: dict) -> list[Recommendation]:
    category = a.get("industry_category")
    recs = [Recommendation(policy="General Liability",
                           why="Covers injury or property damage to other people. Almost every business needs it.")]

    if a.get("num_employees"):
        if a.get("state") == "TX":
            why = "Texas lets most private employers opt out, but going without it is risky."
        else:
            why = "Most states require it once you have employees (thresholds vary by state)."
        recs.append(Recommendation(policy="Workers' Compensation", why=why))

    if a.get("num_vehicles") or a.get("transports_children") is True:
        recs.append(Recommendation(policy="Commercial Auto",
                                   why="Personal auto policies usually don't cover vehicles used for business."))

    description = (a.get("description") or "").lower()
    if any(word in description for word in PROFESSIONAL_KEYWORDS):
        recs.append(Recommendation(policy="Professional Liability (E&O)",
                                   why="Covers claims that your advice or work caused a client financial loss."))

    revenue = a.get("annual_revenue_usd")
    if revenue is not None and revenue < 5_000_000 and category != "roofing":
        recs.append(Recommendation(policy="Business Owner's Policy (BOP)",
                                   why="Bundles liability and property cover, often cheaper for small businesses."))

    if category == "daycare":
        recs.append(Recommendation(policy="Abuse & Molestation",
                                   why="General liability for childcare usually excludes it; it's sold separately."))

    if a.get("serves_alcohol") is True:
        recs.append(Recommendation(policy="Liquor Liability",
                                   why="Covers harm caused by customers you served alcohol to; GL excludes it."))
    return recs


def risk_flags(a: dict) -> list[str]:
    """Facts that make a business hard to place with standard insurers."""
    flags = []
    if (a.get("prior_claims_3yr") or 0) >= 2:
        flags.append("Multiple claims in the last 3 years: standard insurers may decline; "
                     "may need specialty (E&S) markets.")
    if (a.get("roof_max_height_ft") or 0) > 40:
        flags.append("Roof work above 40 ft (about 3 stories): many standard roofing markets decline.")
    if a.get("uses_torch_down") is True:
        flags.append("Torch-down / hot work: often excluded or surcharged; confirm the carrier accepts it.")
    if (a.get("subcontracted_pct") or 0) > 50:
        flags.append("Over half the work subcontracted: carrier will want certificates of insurance from subs.")
    if a.get("has_pool") is True:
        flags.append("Pool at a daycare: drowning exposure; many childcare carriers decline.")
    if (a.get("alcohol_sales_pct") or 0) > 50:
        flags.append("Alcohol over 50% of sales: rated as a bar; needs a liquor-liability specialist market.")
    return flags
