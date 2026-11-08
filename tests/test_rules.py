"""Tests for coverage suggestions and hard-to-place flags."""

from app.agent.rules import recommend_coverage, risk_flags


def policies(answers):
    return [r.policy for r in recommend_coverage(answers)]


def test_everyone_gets_general_liability():
    assert policies({}) == ["General Liability"]


def test_employees_vehicles_and_small_revenue():
    p = policies({"state": "FL", "num_employees": 5, "num_vehicles": 2, "annual_revenue_usd": 800_000})
    assert {"Workers' Compensation", "Commercial Auto", "Business Owner's Policy (BOP)"} <= set(p)


def test_texas_workers_comp_note():
    wc = next(r for r in recommend_coverage({"state": "TX", "num_employees": 3})
              if r.policy == "Workers' Compensation")
    assert "Texas" in wc.why


def test_no_bop_for_roofers_or_big_revenue():
    assert "Business Owner's Policy (BOP)" not in policies({"industry_category": "roofing", "annual_revenue_usd": 900_000})
    assert "Business Owner's Policy (BOP)" not in policies({"annual_revenue_usd": 8_000_000})


def test_professional_liability_for_consultants():
    assert "Professional Liability (E&O)" in policies({"description": "Marketing consultant"})


def test_daycare_coverages():
    p = policies({"industry_category": "daycare", "transports_children": True})
    assert "Abuse & Molestation" in p
    assert "Commercial Auto" in p


def test_liquor_liability_only_when_serving_alcohol():
    assert "Liquor Liability" in policies({"serves_alcohol": True})
    assert "Liquor Liability" not in policies({"serves_alcohol": False})


def test_no_flags_for_an_ordinary_business():
    assert risk_flags({"prior_claims_3yr": 1, "roof_max_height_ft": 30, "uses_torch_down": False}) == []


def test_each_flag():
    cases = [
        ({"prior_claims_3yr": 2}, "claims"),
        ({"roof_max_height_ft": 45}, "40 ft"),
        ({"uses_torch_down": True}, "Torch-down"),
        ({"subcontracted_pct": 60}, "subcontracted"),
        ({"has_pool": True}, "Pool"),
        ({"alcohol_sales_pct": 70}, "Alcohol"),
    ]
    for answers, words in cases:
        [flag] = risk_flags(answers)
        assert words in flag
