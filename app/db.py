"""
The SQLite database: a log of every LLM call, the web chat sessions, and
eval runs with their conversations.
"""

import threading
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel, create_engine

from app import config


def utc_now() -> datetime:
    """Current time in UTC, with the time zone attached (SQLModel requires it)."""
    return datetime.now(timezone.utc)


class LLMCall(SQLModel, table=True):
    """One row per call to the LLM, successful or not."""

    __tablename__ = "llm_calls"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    session_id: Optional[str] = Field(default=None, index=True)
    role: str                      # "agent" or "customer_sim"
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    success: bool
    error: Optional[str] = None


class ChatSession(SQLModel, table=True):
    """One web conversation. The agent's IntakeState is stored as JSON."""

    __tablename__ = "chat_sessions"

    id: str = Field(primary_key=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    state_json: str


class EvalRun(SQLModel, table=True):
    """One run of the eval harness: its settings and headline scores."""

    __tablename__ = "eval_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utc_now)
    note: Optional[str] = None
    config_json: str
    summary_json: str = "{}"


class EvalConversation(SQLModel, table=True):
    """One simulated customer's conversation within an eval run."""

    __tablename__ = "eval_conversations"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="eval_runs.id", index=True)
    persona_id: int
    scores_json: str
    transcript_json: str


class PortalSubmission(SQLModel, table=True):
    """What a carrier portal received, and what it answered (the carrier's side)."""

    __tablename__ = "portal_submissions"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utc_now)
    carrier: str
    reference: str = Field(index=True)
    received_json: str
    status: str                    # quoted, declined or invalid
    premium: Optional[int] = None
    reason: str = ""


class PortalMapping(SQLModel, table=True):
    """The agent's cached map from one portal form to the application (the agent's side).
    Keyed by a hash of the form, so a portal that changes its form is mapped again."""

    __tablename__ = "portal_mappings"

    form_hash: str = Field(primary_key=True)
    carrier: str
    created_at: datetime = Field(default_factory=utc_now)
    mapping_json: str


_engine = None
_engine_lock = threading.Lock()


def get_engine():
    """Open the database on first use and create any missing tables."""
    global _engine
    with _engine_lock:  # two threads starting at once must not both create it
        if _engine is None:
            config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            # The web server and the eval runner use several threads.
            # SQLite's Python driver refuses to share a connection across
            # threads unless told it may; SQLAlchemy's pool handles the rest.
            _engine = create_engine(f"sqlite:///{config.DB_PATH}",
                                    connect_args={"check_same_thread": False})
            SQLModel.metadata.create_all(_engine)
    return _engine
