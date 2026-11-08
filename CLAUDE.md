# QuoteRunner

One AI agent that turns a messy, one-line description of a small US business
into a complete, verified commercial insurance application (ACORD 125-style).
Built as my PS-II portfolio project for Soma, an AI-native insurance
brokerage. I must be able to explain every line in an interview without AI
help.

## Scope
One agent only. Do not add features beyond the current step. Depth and
reliability over breadth.

## Stack
- Python 3.12, FastAPI, Jinja2 templates + a little vanilla JS (no frontend
  framework)
- Pydantic v2 for every schema; SQLite (via SQLModel) for sessions, LLM call
  logs, eval runs
- LLM: one module llm.py using the OpenAI SDK; provider (openai | openrouter)
  and model names from env/config
- pytest; deployed as a systemd service behind nginx, HTTPS by certbot (no
  Docker)

## Rules
- Plan first, then implement only the agreed plan.
- The agent never invents a value: anything the customer didn't state stays
  empty or gets asked.
- Validation happens in code (Pydantic), never trusted from the model.
- Coverage suggestions and risk flags are plain Python rules, not LLM output.
- Every module gets tests; LLM calls are mocked in unit tests.
- Never commit secrets; read them from env.
- After each change, explain what changed and why in plain language, as if to
  a junior engineer.

## Layout
- app/ (FastAPI app: chat page, /api/chat, /evals)
- app/agent/ (intake logic, question rules, coverage rules)
- evals/ (personas, runner, reports)
- deploy/ (systemd unit, nginx site config, deploy.sh)
