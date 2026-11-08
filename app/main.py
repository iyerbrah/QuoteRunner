"""
The web app: one page and a small JSON API around the intake agent.

  GET  /                                     the chat page
  POST /api/chat                             a customer message in; reply + form state out
  GET  /api/sessions/{id}/application.json   the finished application (once done)

Run locally:  .venv/bin/uvicorn app.main:app --reload
"""

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
from sqlmodel import Session

from app import config
from app.agent.intake import (GREETING, IntakeState, build_submission, format_value,
                              handle_message, label, new_state)
from app.agent.questions import PRIORITY, applicable_industry_questions
from app.agent.rules import recommend_coverage, risk_flags
from app.db import ChatSession, get_engine, utc_now
from app.llm import BudgetExceeded

log = logging.getLogger("quoterunner")
HERE = Path(__file__).parent

app = FastAPI(title="QuoteRunner")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")

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
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "greeting": GREETING, "examples": EXAMPLES,
        "max_chars": config.MAX_MESSAGE_CHARS, "initial_form": form_view(new_state()),
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
