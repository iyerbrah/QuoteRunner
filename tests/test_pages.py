"""Tests for the site's shared layout and the How it works page."""

import html

import pytest
from fastapi.testclient import TestClient

from app import eval_reports, main

client = TestClient(main.app)
PAGES = ["/", "/how-it-works", "/evals", "/evals/runs/4/1", "/evals?run=999"]


@pytest.mark.parametrize("path", PAGES)
def test_every_page_has_the_shared_header_and_footer(path):
    page = client.get(path).text
    assert 'href="/how-it-works"' in page and 'href="/evals"' in page
    assert 'Built by <a href="https://github.com/iyerbrah">Ram Iyer</a>' in page
    assert 'href="https://github.com/iyerbrah/QuoteRunner">Source on GitHub</a>' in page
    assert "portfolio demo" in page


def test_how_it_works_explains_the_steps_and_shows_the_latest_numbers():
    res = client.get("/how-it-works")
    assert res.status_code == 200
    page = html.unescape(res.text)
    assert "The model listens. Code verifies." in page
    assert page.count("<li>") >= 5                 # the five steps
    latest = eval_reports.latest_full_run(eval_reports.all_reports())
    assert f"{latest['summary']['field_accuracy'] * 100:.1f}%" in page
    assert 'aria-current="page"' in page          # its nav link is marked current


def test_how_it_works_without_eval_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(eval_reports, "REPORTS_DIR", tmp_path)
    page = client.get("/how-it-works").text
    assert "Measured, not guessed" not in page   # the stats section is left out, not broken
    assert "Built with" in page


def test_home_page_has_the_hero_input_examples_and_proof_line():
    page = html.unescape(client.get("/").text)
    assert "What does your business do?" in page
    assert 'id="start"' in page and 'id="start-input"' in page
    assert "Tested on" in page and "simulated customers" in page
    assert "Checked by code" in page               # the form stack


def test_favicon_is_served():
    assert client.get("/static/favicon.svg").status_code == 200
