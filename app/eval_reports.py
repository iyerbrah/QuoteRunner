"""
Reads the eval reports (evals/reports/run-N.json) for the public /evals page.

The committed report files are the source, not the local database: they
travel with `git pull`, so the live page shows exactly the runs in the repo,
and anyone can check the page against the JSON on GitHub.
"""

import json
import re
from pathlib import Path
from typing import Optional

REPORTS_DIR = Path(__file__).resolve().parent.parent / "evals" / "reports"
FULL_RUN = 50  # a run must cover this many personas to be the headline
SAMPLE_INDUSTRIES = ["roofing", "daycare", "restaurant"]


def _run_id(path: Path) -> int:
    return int(re.fullmatch(r"run-(\d+)\.json", path.name).group(1))


def all_reports() -> list[dict]:
    """Every report, oldest first."""
    paths = [p for p in REPORTS_DIR.glob("run-*.json") if re.fullmatch(r"run-\d+\.json", p.name)]
    return [json.loads(p.read_text()) for p in sorted(paths, key=_run_id)]


def load_report(run_id: int) -> Optional[dict]:
    path = REPORTS_DIR / f"run-{int(run_id)}.json"  # int(): the URL can't name any other file
    return json.loads(path.read_text()) if path.is_file() else None


def latest_full_run(reports: list[dict]) -> Optional[dict]:
    """The newest run that covered all the personas. Quick partial runs never become the headline."""
    full = [r for r in reports if len(r["config"]["personas"]) >= FULL_RUN]
    return full[-1] if full else None


def find_conversation(report: dict, persona_id: int) -> Optional[dict]:
    return next((c for c in report["conversations"] if c["persona"]["id"] == persona_id), None)


def samples(report: dict) -> list[dict]:
    """One example conversation per main industry: finished, most accurate, lowest id."""
    picks = []
    for industry in SAMPLE_INDUSTRIES:
        candidates = [c for c in report["conversations"] if c["persona"]["category"] == industry]
        if candidates:
            picks.append(min(candidates, key=lambda c: (not c["result"]["completed"],
                                                        -c["result"]["accuracy"], c["persona"]["id"])))
    return picks
