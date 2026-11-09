"""
The correct field mapping for each fictional portal, written by hand.

This is ground truth for the quotes eval (what each carrier SHOULD have
received) and the fake LLM's answer in unit tests. The agent never sees it.
For Lakeside, "roofing" has no matching business type, so it is left out on
purpose: the right outcome is "not offered", not a guess. Our "other" maps to
Lakeside's own "Other" option (which Lakeside then declines).
"""

REFERENCE_MAPPINGS = {
    "granite": {
        "insured_name": ("business_name", "as_is"),
        "risk_state": ("state", "as_is"),
        "business_class": ("industry_category", "choice", {
            "roofing": "Roofing contractor", "daycare": "Child day care center",
            "restaurant": "Restaurant", "other": "Other small business"}),
        "full_time_employees": ("num_employees", "as_is"),
        "gross_sales_thousands": ("annual_revenue_usd", "thousands"),
        "owned_autos": ("num_vehicles", "as_is"),
        "losses_3yr": ("prior_claims_3yr", "as_is"),
        "yrs_in_business": ("years_in_business", "as_is"),
        "max_height_ft": ("roof_max_height_ft", "as_is"),
        "hot_work": ("uses_torch_down", "yes_no"),
    },
    "harborline": {
        "applicant_legal_name": ("business_name", "as_is"),
        "location_state": ("state", "as_is"),
        "operations": ("description", "as_is"),
        "class_code": ("industry_category", "choice", {
            "roofing": "R-100 Roofing", "restaurant": "F-210 Restaurant / bar",
            "daycare": "C-330 Childcare", "other": "G-900 General small business"}),
        "annual_revenue": ("annual_revenue_usd", "as_is"),
        "headcount": ("num_employees", "as_is"),
        "prior_claims": ("prior_claims_3yr", "as_is"),
        "roof_height_ft": ("roof_max_height_ft", "as_is"),
        "torch_down": ("uses_torch_down", "yes_no"),
        "subcontracted_percent": ("subcontracted_pct", "as_is"),
        "liquor_percent": ("alcohol_sales_pct", "as_is"),
    },
    "lakeside": {
        "business_name": ("business_name", "as_is"),
        "state": ("state", "as_is"),
        "business_type": ("industry_category", "choice",
                          {"restaurant": "Restaurant", "daycare": "Daycare", "other": "Other"}),
        "revenue": ("annual_revenue_usd", "as_is"),
        "employees": ("num_employees", "as_is"),
        "prior_losses": ("prior_claims_3yr", "as_is"),
        "seats": ("seating_capacity", "as_is"),
        "alcohol_percent": ("alcohol_sales_pct", "as_is"),
        "children_capacity": ("licensed_capacity", "as_is"),
        "swimming_pool": ("has_pool", "yes_no"),
    },
}


def as_tool_answer(portal_fields: list[str]) -> dict:
    """The reference mapping for these portal fields, in the map_fields tool's format."""
    flat = {name: spec for carrier in REFERENCE_MAPPINGS.values() for name, spec in carrier.items()}
    fields = []
    for name in portal_fields:
        spec = flat.get(name)
        if spec is None:
            fields.append({"portal_field": name, "source": None, "transform": "as_is"})
        else:
            fields.append({"portal_field": name, "source": spec[0], "transform": spec[1],
                           "option_map": spec[2] if len(spec) > 2 else {}})
    return {"fields": fields}
