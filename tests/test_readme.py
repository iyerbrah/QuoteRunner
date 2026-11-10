"""The README quotes eval numbers; make sure they match the committed reports."""

import json
import re
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
    for path in re.findall(r"\]\(((?:docs|app|evals|tests|deploy|\.github)[^)#]*|LICENSE|pyproject\.toml)\)", README):
        assert (ROOT / path).exists(), path


def quotes(run_id):
    return json.loads((ROOT / "evals" / "reports" / f"quotes-{run_id}.json").read_text())["summary"]


def quotes_table_rows() -> list[str]:
    """The rows of the README table whose header starts with '| Quotes run |'."""
    lines = README.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("| Quotes run |"))
    rows = []
    for line in lines[start + 2:]:          # skip the header and the |---| line
        if not line.startswith("|"):
            break
        rows.append(line)
    return rows


@pytest.mark.parametrize("run_id", [1, 2, 3])
def test_quotes_history_matches_its_reports(run_id):
    s = quotes(run_id)
    row = next(line for line in quotes_table_rows() if line.startswith(f"| {run_id} |"))
    assert f"{s['values_correct'] * 100:.1f}%".replace("100.0%", "100%") in row
    assert f"| {s['invented_at_carrier']} |" in row
    assert f"{s['decisions_match'] * 100:.0f}%" in row


def test_quotes_headline_matches_the_latest_runs():
    a, b = quotes(2), quotes(3)
    assert a["values_correct"] == b["values_correct"] == 1.0
    assert f"**{a['invented_at_carrier']}** across {a['submissions']} portal submissions" in README
    assert f"of {a['values_checked']:,}" in README
    assert f"**{a['businesses_with_a_quote']} of {a['businesses']}**" in README


def test_test_count_in_the_readme_is_current():
    import subprocess, sys
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"], cwd=ROOT,
                         capture_output=True, text=True).stdout
    count = int(re.search(r"(\d+) tests? collected", out).group(1))
    assert f"{count} tests" in README
