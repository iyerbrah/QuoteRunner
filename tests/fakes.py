"""A fake llm.chat shared by the agent and web tests."""

import json
from types import SimpleNamespace

from app import llm


class FakeLLM:
    """Extraction calls (the ones with tools) return the next scripted dict;
    reply calls return a fixed question text."""

    def __init__(self, *extractions, reply="(next question)"):
        self.extractions = list(extractions)
        self.reply = reply
        self.calls = []

    def __call__(self, messages, tools=None, tool_choice=None, model_role="agent", session_id=None):
        self.calls.append({"messages": messages, "tools": tools, "session_id": session_id})
        if tools:
            args = self.extractions.pop(0)
            arguments = args if isinstance(args, str) else json.dumps(args)
            return SimpleNamespace(content=None, tool_calls=[
                SimpleNamespace(function=SimpleNamespace(name="record_facts", arguments=arguments))])
        return SimpleNamespace(content=self.reply, tool_calls=None)

    @property
    def reply_calls(self):
        return [c for c in self.calls if not c["tools"]]


def ext(values=None, uncertain=(), unknown=(), confirmed=(), approve=False):
    """One scripted extraction result."""
    return {"values": values or {}, "uncertain_fields": list(uncertain), "unknown_fields": list(unknown),
            "confirmed_fields": list(confirmed), "confirms_summary": approve}


def run(monkeypatch, *extractions, reply="(next question)"):
    """Replace llm.chat with a FakeLLM for this test."""
    fake = FakeLLM(*extractions, reply=reply)
    monkeypatch.setattr(llm, "chat", fake)
    return fake


ROOFER = {"description": "residential roofing", "industry_category": "roofing", "state": "Texas",
          "num_employees": 8, "annual_revenue_usd": 1_200_000, "num_vehicles": 3, "prior_claims_3yr": 0,
          "years_in_business": 6, "business_name": "Lone Star Roofing"}
ROOF_DETAILS = {"roof_max_height_ft": 30, "roofing_work_type": "residential",
                "uses_torch_down": False, "uses_subcontractors": False}
ROOFER_MESSAGE = "Lone Star Roofing: residential roofing in Texas, and everything else at once"
