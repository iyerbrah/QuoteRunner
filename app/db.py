"""
The SQLite database: a log of every LLM call, and the web chat sessions.
Step 4 adds eval runs here.
"""

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


_engine = None


def get_engine():
    """Open the database on first use and create any missing tables."""
    global _engine
    if _engine is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{config.DB_PATH}")
        SQLModel.metadata.create_all(_engine)
    return _engine
