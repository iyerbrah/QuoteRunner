"""
The simulated customer: an LLM role-playing one persona.

It sees the same chat as the agent, but with the roles swapped (the agent's
messages are "user" to it), and a fact card with only that persona's facts.
"""

from typing import Optional

from app import llm
from app.agent.intake import format_value, label
from app.agent.schema import US_STATES
from evals.personas import CITIES, STYLES, Persona

SIM_PROMPT = """You are role-playing a small-business owner in the US who is chatting
with an insurance intake assistant. You are the CUSTOMER, never the assistant.

Your facts, the ONLY things you know about your business:
{facts}

Facts you genuinely do not know: {unknown}.
If asked about them, say you're not sure or don't know. Never guess or estimate them.

How you talk: {style}
{change}
Rules:
- Use only the facts above. Never make up anything else. If asked about
  something not listed, say you're not sure.
- Answer what you are asked. Keep it to one or two sentences unless your
  style says otherwise. Don't volunteer your unknown facts.
- When the assistant shows a summary, check every line against your facts.
  If a value is wrong, say exactly what to fix. If it is all correct, say yes.
  Formatting differences are fine ("TX" vs "Texas", "$900,000" vs "900k"),
  and don't ask for details the form doesn't have (like your city).
- Write only your next message as the customer."""


def fact_card(persona: Persona) -> str:
    lines = []
    for field, value in persona.truth.items():
        if field == "industry_category":
            continue  # the owner describes the business; classifying it is the agent's job
        if field == "state":
            city = CITIES.get(value)
            value = f"{US_STATES[value]} ({value})" + (f", in {city}" if city else "")
        else:
            value = format_value(field, value)
        lines.append(f"- {label(field)}: {value}")
    return "\n".join(lines)


def sim_prompt(persona: Persona) -> str:
    change = ""
    if persona.change:
        field = persona.change["field"]
        change = (f"Your deliberate mistake: the first time you mention {label(field).lower()}, say "
                  f"{persona.change['first']}. A few messages later, correct yourself: the real number "
                  f"is {persona.truth[field]}. If you see the wrong number in a summary, correct it.\n")
    return SIM_PROMPT.format(facts=fact_card(persona), unknown=", ".join(label(f) for f in persona.unknown),
                             style=STYLES[persona.style], change=change)


def next_customer_message(persona: Persona, chat: list[dict], session_id: Optional[str] = None) -> str:
    """The customer's reply to the chat so far (chat is the agent's message list)."""
    swapped = {"assistant": "user", "user": "assistant"}
    messages = [{"role": "system", "content": sim_prompt(persona)},
                *({"role": swapped[m["role"]], "content": m["content"]} for m in chat)]
    reply = llm.chat(messages, model_role="customer_sim", session_id=session_id)
    return (reply.content or "").strip() or "Sorry, could you say that again?"
