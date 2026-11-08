# Business Insurance Intake Agent

A chatbot that interviews a small-business owner, fills a validated insurance
application, and suggests which policies they likely need. It does the first
step of a commercial insurance broker's job: collecting the facts needed for a quote.

**Live demo:** _<add your Streamlit Cloud link>_

![screenshot](screenshot.png) <!-- add a screenshot -->

## What it does

1. The bot asks one question at a time (business name, industry, state, employees, revenue, vehicles, past claims).
2. After every answer, an LLM extracts the facts into a structured form.
3. Pydantic validates each value. Bad values (e.g. a state code that doesn't exist, negative employees) are rejected and the bot re-asks.
4. A rule engine suggests coverage (General Liability, Workers' Comp, Commercial Auto, E&O, BOP) and flags hard-to-place risks.
5. The finished application downloads as JSON for a broker.

## How it works

```
User message
    │
    ▼
LLM call 1: extract_fields()    ← forced tool call = structured JSON output
    │
    ▼
merge_updates()                 ← Pydantic validation; invalid fields dropped
    │
    ▼
LLM call 2: next_reply()        ← told which field is missing next
    │
    ▼
recommend_coverage()            ← plain Python rules, no LLM
```

| File | What's in it |
|---|---|
| `intake.py` | Form model, the two LLM calls, coverage rules (no UI code) |
| `app.py` | Streamlit chat UI and sidebar |
| `test_intake.py` | Tests for validation, rules and extraction (with a fake LLM client) |

## Design decisions

- **Two LLM calls instead of one.** One call extracts data, the other writes the reply. Each prompt does a single job, so it's more reliable and easier to debug.
- **Forced tool calling for extraction.** The model must return JSON matching the schema, so there's no fragile text parsing.
- **Validation outside the LLM.** LLMs can hallucinate or mis-format, so Pydantic checks every value before it's saved.
- **Rules, not AI, for coverage suggestions.** Recommendations need to be predictable and explainable, and a licensed broker makes the final decision.
- **Logic separate from UI.** `intake.py` has no Streamlit code, so it's testable on its own.

## Run it locally

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your_key_here      # Windows: set ANTHROPIC_API_KEY=...
streamlit run app.py
pytest                                      # runs tests, no API key needed
```

## Limitations and next steps

- Accept full state names ("Texas") as well as codes
- Upload a PDF of an existing policy and pre-fill the form from it
- Save applications to a SQLite database
- Industry-specific follow-up questions (e.g. roofers: max roof height)
- Measure extraction accuracy on 20 test conversations
