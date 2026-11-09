"""Tests for the fictional carriers: their rules, and their portals over HTTP."""

import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import config, db, main
from app.carriers import CARRIERS, granite_decide, harborline_decide, lakeside_decide, validate_form

client = TestClient(main.app)

ROOFER = {"business_class": "Roofing contractor", "risk_state": "TX", "full_time_employees": 8,
          "gross_sales_thousands": 900, "losses_3yr": 0, "max_height_ft": 30, "hot_work": "no"}


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "_engine", None)


# --- Rules ----------------------------------------------------------------------
def test_granite_quotes_with_the_published_formula():
    # 900k / 1000 x 9.5 (roofing) x 1.1 (TX) + 85 x 8 employees = 10,085 -> rounded to 10,080
    assert granite_decide(ROOFER).premium == 10_080


@pytest.mark.parametrize("change, reason", [
    ({"max_height_ft": 45}, "40 ft"), ({"hot_work": "yes"}, "hot work"), ({"losses_3yr": 2}, "losses"),
])
def test_granite_declines(change, reason):
    d = granite_decide({**ROOFER, **change})
    assert d.status == "declined" and reason in d.reason


def test_harborline_takes_hard_risks_but_charges_more():
    base = {"class_code": "R-100 Roofing", "location_state": "TX", "annual_revenue": 900_000,
            "headcount": 8, "prior_claims": 0}
    plain = harborline_decide(base).premium
    hard = harborline_decide({**base, "roof_height_ft": 45, "torch_down": "yes"}).premium
    assert harborline_decide({**base, "roof_height_ft": 45}).status == "quoted"
    assert hard > plain


def test_lakeside_writes_main_street_only():
    base = {"business_type": "Restaurant", "state": "TX", "revenue": 1_800_000, "employees": 22,
            "prior_losses": 1}
    assert lakeside_decide(base).status == "quoted"
    assert lakeside_decide({**base, "business_type": "Other"}).status == "declined"
    assert lakeside_decide({**base, "alcohol_percent": 55}).status == "declined"
    assert lakeside_decide({**base, "revenue": 6_000_000}).status == "declined"


def test_portal_validation():
    data, errors = validate_form(CARRIERS["granite"], {"insured_name": "A", "full_time_employees": "-2",
                                                       "business_class": "Spaceships"})
    assert any("whole number" in e for e in errors)
    assert any("not an option" in e for e in errors)
    assert any("required" in e for e in errors)


# --- Portals over HTTP ----------------------------------------------------------
def result_marker(html):
    return dict(re.findall(r'data-(\w+)="([^"]*)"', re.search(r'<div id="result"[^>]*>', html).group(0)))


@pytest.mark.parametrize("slug", list(CARRIERS))
def test_every_portal_serves_a_labelled_form(slug):
    page = client.get(f"/portals/{slug}").text
    assert "Fictional carrier portal" in page
    for f in CARRIERS[slug].fields:
        assert f'name="{f.name}"' in page and f'for="f-{f.name}"' in page


def test_a_submission_is_answered_and_stored():
    form = {"insured_name": "Lone Star Roofing", **{k: str(v) for k, v in ROOFER.items()}}
    res = client.post("/portals/granite", data=form)
    marker = result_marker(res.text)
    assert res.status_code == 200 and marker["status"] == "quoted" and marker["premium"] == "10080"
    with Session(db.get_engine()) as s:
        [saved] = s.exec(select(db.PortalSubmission)).all()
    assert saved.reference == marker["reference"] and json.loads(saved.received_json) == form


def test_an_invalid_submission_gets_errors():
    res = client.post("/portals/harborline", data={"applicant_legal_name": "X"})
    assert res.status_code == 422 and result_marker(res.text)["status"] == "invalid"


def test_unknown_portal_is_404():
    assert client.get("/portals/acme").status_code == 404
