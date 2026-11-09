"""The README quotes eval numbers; make sure they match the committed reports."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text()


def summary(run_id):
    return json.loads((ROOT / "evals" / "reports" / f"run-{run_id}.json").read_text())["summary"]


@pytest.mark.parametrize("run_id", [2, 3, 4])
def test_each_run_in_the_history_table_matches_its_report(run_id):
    s = summary(run_id)
    row = next(line for line in README.splitlines() if line.startswith(f"| {run_id} |"))
    assert f"{s['field_accuracy'] * 100:.1f}%" in row
    assert f"| {s['invented_values']} |" in row
    assert f"{s['completion_rate'] * 100:.0f}%" in row
    assert f"{s['avg_questions']:.1f}" in row


def test_headline_ranges_match_the_two_latest_full_runs():
    a, b = summary(3), summary(4)
    acc = sorted([a["field_accuracy"], b["field_accuracy"]])
    done = sorted([a["completion_rate"], b["completion_rate"]])
    questions = sorted([a["avg_questions"], b["avg_questions"]])
    invented = sorted([a["invented_values"], b["invented_values"]])
    cost = sorted([a["avg_cost_agent_usd"], b["avg_cost_agent_usd"]])
    assert f"{acc[0] * 100:.1f}–{acc[1] * 100:.1f}%" in README
    assert f"{done[0] * 100:.0f}–{done[1] * 100:.0f}%" in README
    assert f"{questions[0]:.1f}–{questions[1]:.1f}" in README
    assert f"{invented[0]}–{invented[1]}" in README
    assert f"${cost[0]:.4f}–{cost[1]:.4f}" in README


def test_readme_links_point_at_real_files():
    for path in ["docs/screenshot.png", "deploy/README.md", "reference/starter"]:
        assert (ROOT / path).exists(), path
