"""
The web app: one page and a small JSON API around the intake agent.

  GET  /                                     the chat page
  GET  /healthz                              liveness check for systemd/uptime monitors
  POST /api/chat                             a customer message in; reply + form state out
  GET  /api/sessions/{id}/application.json   the finished application (once done)
  POST /api/sessions/{id}/quotes             submit it to the carriers, get quotes back
  GET  /how-it-works                         how the agent works, with the latest eval numbers
  GET  /evals                                eval results (latest full run, or ?run=N)
  GET  /evals/runs/{run}/{persona}           one simulated conversation, scored
  GET  /portals/{carrier}                    a FICTIONAL carrier's quote form (see app/carriers.py)
  POST /portals/{carrier}                    the carrier validates, decides and answers

Run locally:  .venv/bin/uvicorn app.main:app --reload
"""

import json
import logging
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlmodel import Session

from app import config, eval_reports
from app.carriers import CARRIERS, validate_form
from app.agent.intake import (GREETING, IntakeState, belongs_to_business, build_submission,
                              format_value, handle_message, label, new_state, validate_field)
from app.agent.questions import PRIORITY, applicable_industry_questions
from app.agent import submit
from app.agent.rules import recommend_coverage, risk_flags
from app.db import ChatSession, PortalSubmission, get_engine, utc_now
from app.llm import BudgetExceeded

log = logging.getLogger("quoterunner")
HERE = Path(__file__).parent

# No auto-generated /docs or /openapi.json: a public demo doesn't need an API browser.
app = FastAPI(title="QuoteRunner", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["pct"] = lambda x, digits=0: f"{x * 100:.{digits}f}%"
templates.env.globals["label"] = label
templates.env.filters["label"] = label

EXAMPLES = [
    {"label": "Roofing crew in Texas",
     "message": "hey we're a roofing crew out of Houston TX, about 8 guys and 3 trucks, been at it 6 years"},
    {"label": "Daycare in Florida",
     "message": "I run a home daycare in Orlando, Florida with 4 staff. We're licensed for 40 kids."},
    {"label": "Restaurant with a bar",
     "message": "We own a Mexican restaurant with a full bar in Austin, Texas. 22 employees, "
                "around $1.8M in sales last year."},
]


# ---------------------------------------------------------------------------
# What the right-hand panel shows
# ---------------------------------------------------------------------------
def form_view(state: IntakeState) -> dict:
    """The form state the page needs: every field with its status, progress, coverage, flags."""
    a = state.answers
    fields = []
    for field in PRIORITY + [q.field for q in applicable_industry_questions(a)]:
        if field in a:
            status, value = "filled", format_value(field, a[field])
        elif field in state.pending:
            status, value = "checking", format_value(field, state.pending[field])
        elif field in state.unknown:
            status, value = "unknown", "Not known"
        else:
            status, value = "open", ""
        fields.append({"field": field, "label": label(field), "value": value, "status": status})

    answered = sum(f["status"] in ("filled", "unknown") for f in fields)
    return {
        "status": state.status,
        "progress": round(100 * answered / len(fields)),
        "fields": fields,
        "coverage": [r.model_dump() for r in recommend_coverage(a)],
        "flags": risk_flags(a),
        "questions_asked": state.questions_asked,
    }


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
# Timestamps of recent requests per IP. Kept in memory, so each server
# worker counts separately and a restart resets it: fine for a demo.
_recent_requests: dict[str, deque] = defaultdict(deque)


def rate_limited(ip: str) -> bool:
    """True if this IP already sent RATE_LIMIT_PER_MINUTE requests in the last 60 seconds."""
    now = time.monotonic()
    recent = _recent_requests[ip]
    while recent and now - recent[0] > 60:
        recent.popleft()
    if len(recent) >= config.RATE_LIMIT_PER_MINUTE:
        return True
    recent.append(now)
    return False


def error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/healthz")
def healthz():
    """For uptime monitors and deploy.sh: the app is up and can reach its database. No LLM call."""
    with Session(get_engine()) as db:
        db.exec(text("SELECT 1"))
    return {"status": "ok"}


def latest_summary() -> tuple[Optional[dict], Optional[str]]:
    """Headline numbers and date of the latest full eval run, if there is one."""
    report = eval_reports.latest_full_run(eval_reports.all_reports())
    return (report["summary"], report.get("created_at", "")) if report else (None, None)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "greeting": GREETING, "examples": EXAMPLES, "proof": latest_summary()[0],
        "quotes": (eval_reports.latest_quotes_report() or {}).get("summary"),
        "max_chars": config.MAX_MESSAGE_CHARS, "initial_form": form_view(new_state()),
    })


@app.get("/how-it-works", response_class=HTMLResponse)
def how_it_works(request: Request):
    summary, created_at = latest_summary()
    return templates.TemplateResponse(request, "how_it_works.html", {
        "active": "how", "summary": summary, "created_at": created_at,
        "quotes": (eval_reports.latest_quotes_report() or {}).get("summary"),
    })


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    session_id: Optional[str] = None  # None on the first message
    message: str = Field(min_length=1, max_length=config.MAX_MESSAGE_CHARS)


@app.post("/api/chat")
def chat(body: ChatRequest, request: Request):
    # request.client.host is the real visitor IP once uvicorn runs with
    # --proxy-headers behind nginx (Step 6).
    if rate_limited(request.client.host if request.client else "unknown"):
        return error(429, "You're sending messages too quickly. Please wait a minute and try again.")

    with Session(get_engine()) as db:
        row = db.get(ChatSession, body.session_id) if body.session_id else None
        if body.session_id and row is None:
            return error(404, "This conversation has expired. Please start over.")
        state = IntakeState.model_validate_json(row.state_json) if row else new_state()
        session_id = row.id if row else uuid.uuid4().hex

        customer_messages = sum(m["role"] == "user" for m in state.messages)
        if customer_messages >= config.MAX_MESSAGES_PER_SESSION:
            return error(429, "This conversation has reached its message limit. Please start over.")

        try:
            reply = handle_message(state, body.message, session_id=session_id)
        except BudgetExceeded as e:
            log.warning("Budget exceeded for session %s: %s", session_id, e)
            return error(503, "This demo has reached its spending limit for now. Please try again later.")
        except Exception:
            # Nothing is saved, so the conversation stays as it was before this message.
            log.exception("Agent failed for session %s", session_id)
            return error(502, "Sorry, something went wrong on our side. Please send that again.")

        row = row or ChatSession(id=session_id, state_json="")
        row.state_json = state.model_dump_json()
        row.updated_at = utc_now()
        db.add(row)
        db.commit()

    return {"session_id": session_id, "reply": reply, "form": form_view(state)}


@app.get("/api/sessions/{session_id}/application.json")
def download_application(session_id: str):
    with Session(get_engine()) as db:
        row = db.get(ChatSession, session_id)
    if row is None:
        return error(404, "No such conversation.")
    state = IntakeState.model_validate_json(row.state_json)
    if state.status != "done":
        return error(409, "The application isn't finished yet.")
    return Response(build_submission(state).model_dump_json(indent=2), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="application.json"'})


class QuotesRequest(BaseModel):
    # Facts a carrier asked for that the customer has now looked up, e.g.
    # {"annual_revenue_usd": "900000"}. Checked by the same validation as the chat.
    answers: dict[str, str] = {}


def clean_number(text: str) -> str:
    return text.replace(",", "").replace("$", "").strip()


@app.post("/api/sessions/{session_id}/quotes")
def get_quotes(session_id: str, request: Request, body: Optional[QuotesRequest] = None):
    """The submission agent: take the finished application to every carrier portal."""
    if rate_limited(request.client.host if request.client else "unknown"):
        return error(429, "You're sending requests too quickly. Please wait a minute and try again.")
    with Session(get_engine()) as db:
        row = db.get(ChatSession, session_id)
        if row is None:
            return error(404, "This conversation has expired. Please start over.")
        state = IntakeState.model_validate_json(row.state_json)
        if state.status != "done":
            return error(409, "Finish and approve the application first.")
        for field, text in (body.answers if body else {}).items():
            if not belongs_to_business(field, state.answers):
                return error(422, "That isn't a question on this application.")
            try:
                try:
                    state.answers[field] = validate_field(field, text, state.answers)
                except ValueError:  # "900,000" or "$900000" for a number
                    state.answers[field] = validate_field(field, clean_number(text), state.answers)
            except ValueError:
                return error(422, f"That doesn't look like a valid {label(field).lower()}. Try again.")
            state.unknown = [f for f in state.unknown if f != field]
        try:
            results = submit.get_quotes(state, session_id=session_id)
        except BudgetExceeded as e:
            log.warning("Budget exceeded for quotes in session %s: %s", session_id, e)
            return error(503, "This demo has reached its spending limit for now. Please try again later.")
        except Exception:
            log.exception("Quotes failed for session %s", session_id)
            return error(502, "Couldn't reach the carriers just now. Please try again.")
        state.quotes = [r.model_dump() for r in results]
        row.state_json = state.model_dump_json()
        row.updated_at = utc_now()
        db.add(row)
        db.commit()
    return {"quotes": state.quotes, "form": form_view(state)}  # the form may have new answers


# ---------------------------------------------------------------------------
# Public eval results (read-only, from the committed report files)
# ---------------------------------------------------------------------------
RESULT_ORDER = {"invented": 0, "wrong": 1, "missed": 2, "correct": 3}


def not_found(request: Request, message: str) -> HTMLResponse:
    return templates.TemplateResponse(request, "not_found.html", {"message": message}, status_code=404)


@app.get("/evals", response_class=HTMLResponse)
def evals_page(request: Request, run: Optional[int] = None):
    reports = eval_reports.all_reports()
    report = eval_reports.load_report(run) if run is not None else eval_reports.latest_full_run(reports)
    if run is not None and report is None:
        return not_found(request, f"There is no eval run {run}.")
    per_field = []
    if report:
        per_field = sorted(({"field": f, **counts} for f, counts in report["summary"]["per_field"].items()),
                           key=lambda row: (row["accuracy"], row["field"]))  # weakest fields first
    return templates.TemplateResponse(request, "evals.html", {
        "active": "evals", "report": report, "is_latest": run is None, "history": reports,
        "quotes": eval_reports.latest_quotes_report(),
        "full_run": eval_reports.FULL_RUN,
        "samples": eval_reports.samples(report) if report else [], "per_field": per_field,
    })


def result_rows(convo: dict) -> list[dict]:
    """One row per scored field: what was true, what the agent recorded, and the verdict."""
    persona, result, answers = convo["persona"], convo["result"], convo["answers"]
    rows = []
    for field, verdict in result["fields"].items():
        if field in persona["unknown"]:
            truth = "doesn't know"
        elif field in persona["truth"]:
            truth = format_value(field, persona["truth"][field])
        else:
            truth = "doesn't apply"
        recorded = format_value(field, answers[field]) if field in answers else "—"
        rows.append({"label": label(field), "truth": truth, "recorded": recorded, "result": verdict})
    return sorted(rows, key=lambda row: RESULT_ORDER[row["result"]])


@app.get("/evals/runs/{run_id}/{persona_id}", response_class=HTMLResponse)
def transcript_page(request: Request, run_id: int, persona_id: int):
    report = eval_reports.load_report(run_id)
    convo = eval_reports.find_conversation(report, persona_id) if report else None
    if convo is None:
        return not_found(request, "That conversation doesn't exist.")
    return templates.TemplateResponse(request, "transcript.html", {
        "active": "evals", "report": report, "convo": convo, "rows": result_rows(convo),
    })


# ---------------------------------------------------------------------------
# Fictional carrier portals: the carriers' side of the integration
# ---------------------------------------------------------------------------
@app.get("/portals/{slug}", response_class=HTMLResponse)
def portal_form(request: Request, slug: str):
    carrier = CARRIERS.get(slug)
    if carrier is None:
        return not_found(request, "There is no such carrier portal.")
    return templates.TemplateResponse(request, "portal.html", {"carrier": carrier, "result": None})


@app.post("/portals/{slug}", response_class=HTMLResponse)
async def portal_submit(request: Request, slug: str):
    carrier = CARRIERS.get(slug)
    if carrier is None:
        return not_found(request, "There is no such carrier portal.")
    form = {k: str(v) for k, v in (await request.form()).items()}
    data, errors = validate_form(carrier, form)
    reference = f"{carrier.prefix}-{uuid.uuid4().hex[:6].upper()}"
    if errors:
        result = {"status": "invalid", "errors": errors, "premium": None, "reason": "; ".join(errors)}
    else:
        decision = carrier.decide(data)
        result = {"status": decision.status, "premium": decision.premium, "reason": decision.reason, "errors": []}
    result["reference"] = reference
    with Session(get_engine()) as db:
        db.add(PortalSubmission(carrier=slug, reference=reference, received_json=json.dumps(form),
                                status=result["status"], premium=result["premium"], reason=result["reason"]))
        db.commit()
    return templates.TemplateResponse(request, "portal.html", {"carrier": carrier, "result": result},
                                      status_code=422 if errors else 200)
