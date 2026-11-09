# QuoteRunner

An AI broker agent: one sentence of chat becomes a verified commercial insurance application
(ACORD 125-style), which is submitted to three carrier portals and comes back as quotes compared side
by side. It asks only for what's missing and never guesses.

**[Live demo](https://quoterunner.ramiyer.tech)** · [How it works](https://quoterunner.ramiyer.tech/how-it-works) ·
[Eval results](https://quoterunner.ramiyer.tech/evals) · 60-second demo video (link coming)

![QuoteRunner home page: "What does your business do?", with a sample application stamped "Checked by code"](docs/screenshot.png)

## Results

Measured on 50 simulated small-business owners talking to the same code the site runs
([every conversation is public](https://quoterunner.ramiyer.tech/evals)). The two latest full runs use
identical code, so the ranges show run-to-run variation, not cherry-picking.

| | Runs 3 and 4 |
|---|---|
| Field accuracy | **99.3–99.8%** |
| Invented values (a field filled that the owner never stated) | **0–1** in 50 conversations |
| Conversations finished | **90–98%** |
| Questions asked | **6.8–7.5**, instead of a 25-field form |
| Cost per conversation | **$0.0026–0.0029** (gpt-5-nano), plus $0.0008 for the simulated customer |

The finished applications were then sent to the three carrier portals
([quotes runs 2 and 3](evals/reports), identical results):

| | Quotes runs 2 and 3 |
|---|---|
| Values received by carriers that were correct | **100%** of 1,285 |
| Invented values that reached a carrier | **0** across 252 portal submissions |
| Carrier decisions matching their rules applied to the true facts | **100%** |
| Businesses quoted | **45 of 45**, after one follow-up for facts the owner didn't know (3 before it) |
| Speed and cost | portal mapping learned once for $0.004, then **6 ms** per business |

![A finished application with three carrier quotes, cheapest first, and the same summary in the chat](docs/quotes.png)

In insurance, a made-up answer can void a policy, so **invented values** is the number that matters
most: every persona has 1–2 facts they genuinely don't know, and any guess shows up in the score.

[![The public eval results page: headline numbers, per-field accuracy and every conversation](docs/evals.png)](https://quoterunner.ramiyer.tech/evals)

## How it works

One customer message, end to end:

```mermaid
flowchart TD
    B["Browser<br/>chat + live application"] -->|POST /api/chat| X["LLM call 1: extract<br/>forced tool call"]
    X --> V["Code: Pydantic validation<br/>and the no-guess rules"]
    V --> N["Code: next_question()<br/>priority order, industry follow-ups"]
    N -->|a question is left| W["LLM call 2:<br/>word the question"]
    N -->|nothing left| S["Code: summary<br/>for the customer to approve"]
    V --> R["Code: coverage<br/>and risk-flag rules"]
    W --> B
    S --> B
    X & W -.-> L[("llm.py: cost per call,<br/>session and daily caps,<br/>SQLite call log")]
```

Then, once the customer approves the application, the submission agent takes it to every carrier at once:

```mermaid
flowchart TD
    A["Verified application"] --> G["GET the carrier's quote form (HTTP)"]
    G --> F["Code: read the form's fields,<br/>labels, options and hints"]
    F --> M["LLM, 3 samples in parallel:<br/>map form fields to application fields<br/>(names only, never values)"]
    M --> K["Code: keep only what most samples agree on,<br/>check transforms and options, cache it"]
    K --> P["Code: fill every value from the application,<br/>units from the form's own words"]
    P -->|a required fact is unknown| Q["Needs info: ask the customer, resubmit"]
    P --> S["POST the form, read the result"]
    S --> C["Quote / decline / not offered,<br/>cheapest first"]
```

The carriers (Granite Mutual, Harborline Specialty, Lakeside Commercial) are **fictional portals built
into the app**, because real carriers only give portal access to licensed, appointed brokers. They
differ the way real ones do: different field names, revenue in dollars on one and in thousands on
another, their own class names, appetite and pricing. The agent knows none of that; it reads each form
like any website.

How the numbers above are produced:

```mermaid
flowchart LR
    P["50 seeded personas<br/>true facts, 1-2 unknowns,<br/>7 speaking styles"] --> C["Simulated customer<br/>(an LLM playing the owner)"]
    C <-->|up to 25 messages| H["handle_message()<br/>the same code as the site"]
    H --> SC["Scorer: correct, wrong,<br/>missed or invented, per field"]
    SC --> J["evals/reports/run-N.json<br/>committed to git"]
    J --> E["/evals page"]
```

## Design decisions

- **Two LLM calls, one job each.** One extracts facts as a forced tool call (structured JSON, no text
  parsing); the other only words the next question. Each prompt is simpler, more reliable and
  separately testable.
- **The model reads; code decides.** Which question comes next, when the form is complete, what the
  summary says, which coverage to suggest: all plain, tested Python. The model never writes anything
  that lands on the application without passing through code.
- **No-guess rules, enforced in code, not just asked for in the prompt.** Extraction sees only the
  last question and answer, so it can't re-fill the form from old messages. "I don't know" counts only
  for the question just asked. A saved answer changes only when we asked about it. A business name or
  description must be made of words the customer actually typed. Each rule came from a real transcript
  where the model invented something.
- **Validation in Pydantic.** Every value is checked before it's saved: real US states ("Texas"
  becomes TX), sane numbers, known industries. Invalid answers are dropped and asked again.
- **Vague numbers are confirmed, not trusted.** "About a dozen" is held back and read back to the
  customer before it's saved.
- **Coverage and flags are rules, not AI.** Suggestions must be predictable and explainable to a
  broker (roofs over 40 ft, torch-down work, alcohol over half of sales).
- **Industries are data.** Each industry is one entry of typed follow-up questions with `only_if`
  conditions; adding one needs no other code change (there's a test for that).
- **Measured with simulated customers.** 50 seeded personas with ground truth, scored field by field.
  The eval runner calls the same `handle_message()` as the website.
- **The model maps, code fills.** For carrier portals the model only says which form field holds which
  application fact ("their `gross_sales_thousands` is our `annual_revenue_usd`"). It never sees or
  writes a value, so it can't invent one; code fills every value from the verified application.
- **Consensus, not one guess.** Each portal is mapped by 3 independent samples in parallel, and only
  what most of them agree on is kept. Disagreement becomes "not offered" or "needs info", never a guess.
  The mapping is cached by a hash of the form, so it's learned once per portal and relearned only when
  the form changes.
- **Units from the form's own words.** A form that says "in thousands" gets dollars / 1000; any other
  gets dollars. A dropdown map the model wrote backwards is flipped only where that's unambiguous.
- **Needs info instead of a guess.** A required fact the owner didn't know stops the submission; the
  customer is asked once, the answer is validated like any other, and the carrier is resubmitted.
- **Cost controls.** Every LLM call is logged with tokens, cost and latency. Per-session and daily caps
  stop the app politely; messages per session and per IP are capped too.

## What the evals caught

| Run | Change | Accuracy | Invented | Finished | Avg questions |
|---|---|---|---|---|---|
| 2 | first full run | 98.9% | 0 | 78% | 10.8 |
| 3 | after fixing what the transcripts showed | 99.8% | 1 | 98% | 6.8 |
| 4 | same code as run 3, run again | 99.3% | 0 | 90% | 7.5 |

Run 2 looked accurate but a fifth of the conversations never finished. Reading them showed summary
deadlocks: a bakery owner saw "Industry: other" and kept asking to change it, and owners who spell out
state names kept rejecting "State: OR". Showing "Oregon (OR)" and leaving out the internal "other"
label fixed most of them. Still open: a hint that rescued a misclassified Thai restaurant now makes
some bakeries and print shops "restaurants", and in one run the simulated customer stated a fact it was
told it didn't know (the scorer counts it against the agent anyway).

The carrier pipeline had its own story:

| Quotes run | Mapping | Values correct at carriers | Invented at carriers | Decisions match |
|---|---|---|---|---|
| 1 | one LLM call per portal | 99.1% | 11 | 88% |
| 2 | 3 samples, keep what most agree on | 100% | 0 | 100% |
| 3 | same as run 2, run again | 100% | 0 | 100% |

Building it, the evals and real runs caught the model mapping Lakeside's "Annual revenue ($)" in
thousands (so Lakeside saw $1,800 instead of $1.8M), writing dropdown maps backwards, and, in quotes
run 1, shifting Lakeside's map by one so roofers were submitted as restaurants. Each became a rule in
code (units from the form's words, safe flipping) or consensus mapping, and runs 2 and 3 came back clean.

## Run it locally

Needs Python 3.12+ and an OpenAI API key.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env                        # then add OPENAI_API_KEY
.venv/bin/pytest -q                         # all tests, no API key needed: the LLM is faked
.venv/bin/uvicorn app.main:app --reload     # http://localhost:8000 (on another port, set PORTAL_BASE_URL)
.venv/bin/python -m evals.run --limit 5     # a quick 5-persona chat eval, about $0.02
.venv/bin/python -m evals.quotes            # send the latest finished applications to the 3 portals
```

Deploying to a server (systemd, nginx, Let's Encrypt): see [deploy/README.md](deploy/README.md).

## Project layout

```
app/
  main.py            FastAPI routes: chat page, /api/chat, /how-it-works, /evals, /healthz
  llm.py             the only LLM client: cost per call, spend caps, call log
  config.py          models, prices, caps (from .env)
  db.py              SQLite tables: LLM calls, chat sessions, eval runs, portal mappings and submissions
  carriers.py        the 3 fictional carriers: their forms, appetite and pricing rules
  agent/
    intake.py        extraction, no-guess rules, state machine (collecting, confirming, done)
    submit.py        the submission agent: read form, consensus mapping, fill, submit, read result
    questions.py     question order, INDUSTRY_QUESTIONS, next_question()
    schema.py        the ACORD 125-style Submission, validated by Pydantic
    rules.py         coverage suggestions and hard-to-place flags
evals/               personas, simulated customer, scorer, runners (chat and quotes), reports
deploy/              systemd unit, nginx site, deploy.sh
tests/               pytest, LLM faked
```

## Limitations

- Simulated customers are easier than real people, and they occasionally leak facts. The transcripts
  are published so they can be read, not just scored.
- Results vary between identical runs (completion moved from 98% to 90%), because the model can't be
  made deterministic. Quote ranges, not the best run.
- gpt-5-nano is OpenAI's cheapest tool-calling model; it occasionally misses vague wording that a
  larger model would catch. The model is one line in `.env`.
- Three industries have follow-up questions (roofing, daycare, restaurant); everything else gets the
  base questions only. The per-IP rate limit is kept in memory.
- The carriers are fictional, and their portals are plain HTML forms. Real carrier portals sit behind
  broker logins and often need a browser (JavaScript, multi-page flows); the same read-map-fill design
  would drive a headless browser instead of HTTP.
- It's a portfolio demo, not an insurance product: it doesn't quote, bind or give advice.

## What's next

- Drive JavaScript-heavy portals with a headless browser, using the same consensus mapping.
- Binding: let the customer pick a quote and generate the documents for the broker to sign off.
- Fix the restaurant over-classification and rerun the evals.
- More industries, and a few real (consented) conversations to check the simulated ones against.

## Credits

Built by [Ram Iyer](https://github.com/iyerbrah), with Claude Code as a pair programmer: every step was
planned, reviewed and tested in turn (see the commit history). The starting prototype is in
`reference/starter/`.
