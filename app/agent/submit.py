"""
The submission agent: takes a verified application to each carrier's portal
and brings back a quote, a decline, or what's missing.

For each carrier, in parallel:
  1. GET the portal's form over HTTP and read its fields          code
  2. map portal fields to application fields                     LLM, once per form, cached
     (names, labels and types only: the model never sees a value)
  3. fill every value from the application with a fixed transform code
     a required field we don't have -> "needs info", nothing sent
  4. POST the form and read the result page                       code

Because the model only says "their gross_sales_thousands is our
annual_revenue_usd, in thousands", it cannot invent a value: every value a
carrier receives comes from the verified application through code.
"""

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from typing import Literal, Optional

import httpx
from pydantic import BaseModel, ValidationError
from sqlmodel import Session

from app import config, llm
from app.agent.intake import IntakeState, find_question, label
from app.agent.questions import INDUSTRY_QUESTIONS, PRIORITY, industry_categories
from app.agent.schema import BASE_FIELD_GROUP
from app.carriers import CARRIERS, PortalField
from app.db import PortalMapping, get_engine

Transform = Literal["as_is", "thousands", "yes_no", "choice"]


class FieldMap(BaseModel):
    portal_field: str
    source: Optional[str] = None       # our field, or None when we don't hold that fact
    transform: Transform = "as_is"
    option_map: dict[str, str] = {}    # for "choice": our value -> the portal's option


class QuoteResult(BaseModel):
    carrier: str
    carrier_name: str
    status: Literal["quoted", "declined", "needs_info", "not_offered", "error"]
    premium: Optional[int] = None
    reference: str = ""
    reason: str = ""
    missing: list[str] = []            # what the carrier needs that we don't have (labels)
    missing_fields: list[str] = []     # the same, as application field names
    sent: dict[str, str] = {}          # exactly what was submitted, field by field
    sources: dict[str, str] = {}       # which application field each value came from
    seconds: float = 0.0


# ---------------------------------------------------------------------------
# 1. Read a portal's form (plain HTML, like any website)
# ---------------------------------------------------------------------------
class _FormReader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields, self.labels, self.hints = [], {}, {}
        self._label_for = self._hint_id = self._select = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "label":
            self._label_for, self._text = a.get("for"), []
        elif tag == "small" and a.get("id"):
            self._hint_id, self._text = a["id"], []
        elif tag in ("input", "select") and a.get("name"):
            field = {"id": a.get("id"), "name": a["name"], "type": a.get("type", "text") if tag == "input" else "select",
                     "required": "required" in a, "hint_id": a.get("aria-describedby"), "options": []}
            self.fields.append(field)
            if tag == "select":
                self._select = field
        elif tag == "option" and self._select is not None and a.get("value"):
            self._select["options"].append(a["value"])

    def handle_endtag(self, tag):
        if tag == "label" and self._label_for:
            self.labels[self._label_for] = " ".join("".join(self._text).split()).rstrip(" *")
            self._label_for = None
        elif tag == "small" and self._hint_id:
            self.hints[self._hint_id] = " ".join("".join(self._text).split())
            self._hint_id = None
        elif tag == "select":
            self._select = None

    def handle_data(self, data):
        if self._label_for or self._hint_id:
            self._text.append(data)


def read_form(html: str) -> list[PortalField]:
    reader = _FormReader()
    reader.feed(html)
    return [PortalField(name=f["name"], label=reader.labels.get(f["id"], f["name"]),
                        type=f["type"] if f["type"] in ("number", "select") else "text",
                        options=f["options"], required=f["required"],
                        hint=reader.hints.get(f["hint_id"], "")) for f in reader.fields]


def form_hash(fields: list[PortalField]) -> str:
    return hashlib.sha256(json.dumps([f.model_dump() for f in fields], sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------------------
# 2. Map the portal's fields to ours (LLM, cached)
# ---------------------------------------------------------------------------
def our_fields() -> dict[str, dict]:
    """Every application field the model may map to: label and kind, never a value."""
    fields = {}
    for name in PRIORITY:
        annotation = str(BASE_FIELD_GROUP[name].model_fields[name].annotation)
        kind = "integer" if "int" in annotation else "text"
        if name == "industry_category":
            kind = f"one of {industry_categories()}"
            fields[name] = {"label": label(name), "kind": kind, "values": industry_categories()}
            continue
        elif name == "state":
            kind = "two-letter US state code"
        elif name == "annual_revenue_usd":
            kind = "integer, in US dollars"
        fields[name] = {"label": label(name), "kind": kind}
    for questions in INDUSTRY_QUESTIONS.values():
        for q in questions:
            kind = {"int": "integer", "bool": "yes/no", "choice": f"one of {q.choices}"}.get(q.type, "text")
            fields[q.field] = {"label": q.label, "kind": kind, "values": list(q.choices)}
    return fields


MAP_PROMPT = """You connect an insurance application to a carrier's quote form.
For EACH field of the carrier's form, say which application field holds the same fact,
or null if none does. You never see or write any values.

Transforms:
- "as_is": copy the value unchanged.
- "thousands": the form wants dollars in thousands and our field is in dollars.
- "yes_no": our field is yes/no and the form's options are "yes"/"no".
- "choice": the form is a dropdown whose options differ from our values. option_map goes
  FROM each of our possible values TO one of THIS form's options, copied exactly:
  {{"<our value>": "<one of the form's options>", ...}}. If none of the form's options fits
  one of our values, leave that value out.
Forms often name the same fact differently ("Headcount" and "Employees", "Named insured"
and "Business name", "Losses" and "Claims"): map those. A form field about a fact we
don't collect gets source null.

Application fields:
{ours}"""


def _map_tool(fields: list[PortalField]) -> dict:
    ours = list(our_fields())
    return {"type": "function", "function": {
        "name": "map_fields",
        "description": "Map every carrier form field to an application field.",
        "parameters": {"type": "object", "properties": {"fields": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "portal_field": {"type": "string", "enum": [f.name for f in fields]},
                "source": {"type": ["string", "null"], "enum": ours + [None]},
                "transform": {"type": "string", "enum": ["as_is", "thousands", "yes_no", "choice"]},
                "option_map": {"type": "object", "additionalProperties": {"type": "string"},
                               "description": "keys: OUR values; values: the form's options"},
            },
            "required": ["portal_field", "source", "transform"]}}},
            "required": ["fields"]}}}


def ask_for_mapping(fields: list[PortalField], session_id: Optional[str], note: str = "") -> list[FieldMap]:
    """LLM call: one tool call that maps the whole form (or, with a note, just some fields)."""
    ours = "\n".join(f"- {name}: {f['label']} ({f['kind']})" for name, f in our_fields().items())
    form = json.dumps([f.model_dump() for f in fields], indent=1)
    message = llm.chat([{"role": "system", "content": MAP_PROMPT.format(ours=ours)},
                        {"role": "user", "content": f"Carrier form fields:\n{form}{note}"}],
                       tools=[_map_tool(fields)],
                       tool_choice={"type": "function", "function": {"name": "map_fields"}},
                       model_role="agent", session_id=session_id)
    try:
        raw = json.loads(message.tool_calls[0].function.arguments)["fields"]
    except (TypeError, IndexError, AttributeError, KeyError, json.JSONDecodeError):
        return []
    maps = []
    for item in raw:
        try:
            maps.append(FieldMap.model_validate(item))
        except ValidationError:
            continue
    return maps


def check_mapping(maps: list[FieldMap], fields: list[PortalField]) -> dict[str, FieldMap]:
    """Keep only mappings code can trust: real fields on both sides, a transform that fits."""
    by_name, ours, kept = {f.name: f for f in fields}, our_fields(), {}
    for m in maps:
        field = by_name.get(m.portal_field)
        if field is None or (m.source is not None and m.source not in ours):
            continue
        if m.source is not None:
            kind = ours[m.source]["kind"]
            if "US dollars" in kind and m.transform in ("as_is", "thousands"):
                # Units come from the form's own words, not the model: a form that says
                # "thousand" gets dollars / 1000, any other form gets dollars as they are.
                says_thousands = "thousand" in f"{field.name} {field.label} {field.hint}".lower()
                m.transform = "thousands" if says_thousands else "as_is"
            if m.transform == "thousands" and not kind.startswith("integer"):
                continue
            if m.transform == "yes_no" and (kind != "yes/no" or set(field.options) != {"yes", "no"}):
                continue
            if m.transform == "choice":
                if field.type != "select":
                    continue
                m.option_map = _orient(m.option_map, ours[m.source].get("values", []), field.options)
                if not m.option_map:
                    continue
        kept[m.portal_field] = m
    return kept


def _orient(option_map: dict, our_values: list, options: list) -> dict:
    """A dropdown map must go from our values to the form's options. The model sometimes
    writes it backwards; flip it only when that's unambiguous, otherwise drop it."""
    def keep_valid(m):
        return {k: v for k, v in m.items() if k in our_values and v in options}
    forward = keep_valid(option_map)
    # Backwards (option -> our value): keep each of our values that exactly one option points to.
    pointing = {}
    for option, ours in option_map.items():
        pointing.setdefault(ours, []).append(option)
    flipped = keep_valid({ours: opts[0] for ours, opts in pointing.items() if len(opts) == 1})
    return forward if len(forward) >= len(flipped) else flipped


MAPPING_SAMPLES = 3  # independent mapping calls; a field is kept only if most of them agree


def consensus(samples: list[dict[str, FieldMap]]) -> dict[str, FieldMap]:
    """Keep what a majority of independent mappings agree on, nothing else.
    A dropdown keeps only the our-value -> option pairs most samples agree on. Anything
    they disagree on is left out, so it becomes "not offered" or "needs info", not a guess."""
    need = len(samples) // 2 + 1
    agreed = {}
    for name in {n for s in samples for n in s}:
        votes = {}
        for s in samples:
            if name in s:
                key = (s[name].source, s[name].transform)
                votes.setdefault(key, []).append(s[name])
        (source, transform), maps = max(votes.items(), key=lambda kv: len(kv[1]))
        if len(maps) < need:
            continue
        option_map = {}
        if transform == "choice":
            pairs = [pair for m in maps for pair in m.option_map.items()]
            option_map = {ours: opt for ours, opt in set(pairs) if pairs.count((ours, opt)) >= need}
            if not option_map:
                continue
        agreed[name] = FieldMap(portal_field=name, source=source, transform=transform, option_map=option_map)
    return agreed


def learn_mapping(fields: list[PortalField], session_id: Optional[str], note: str = "") -> dict[str, FieldMap]:
    """Ask MAPPING_SAMPLES times in parallel, check each in code, keep what most agree on."""
    with ThreadPoolExecutor(max_workers=MAPPING_SAMPLES) as pool:
        samples = list(pool.map(lambda _: check_mapping(ask_for_mapping(fields, session_id, note), fields),
                                range(MAPPING_SAMPLES)))
    return consensus(samples)


def get_mapping(carrier: str, fields: list[PortalField], session_id: Optional[str] = None) -> dict[str, FieldMap]:
    """The mapping for this exact form: from the cache, or learned once (a few LLM calls)."""
    key = form_hash(fields)
    with Session(get_engine()) as db:
        cached = db.get(PortalMapping, key)
        if cached:
            return {k: FieldMap.model_validate(v) for k, v in json.loads(cached.mapping_json).items()}
    mapping = learn_mapping(fields, session_id)
    # Repair once: required fields left unmapped get a second, focused look.
    unmapped = [f for f in fields if f.required and not (mapping.get(f.name) and mapping[f.name].source)]
    if unmapped:
        note = ("\n\nThese REQUIRED fields were left unmapped: " + ", ".join(f.name for f in unmapped) +
                ". Map each one if any application field holds that fact; otherwise null.")
        retry = learn_mapping(unmapped, session_id, note)
        mapping.update({k: v for k, v in retry.items() if v.source})
    if mapping:
        with Session(get_engine()) as db:
            db.merge(PortalMapping(form_hash=key, carrier=carrier,
                                   mapping_json=json.dumps({k: v.model_dump() for k, v in mapping.items()})))
            db.commit()
    return mapping


# ---------------------------------------------------------------------------
# 3. Fill the form from the application (code only)
# ---------------------------------------------------------------------------
def apply_transform(m: FieldMap, value, field: PortalField) -> Optional[str]:
    """Our validated value -> the portal's format. None means we can't express it."""
    if m.transform == "thousands":
        return str(round(value / 1000))
    if m.transform == "yes_no":
        return "yes" if value else "no"
    if m.transform == "choice":
        return m.option_map.get(str(value))
    text = "yes" if value is True else "no" if value is False else str(value)
    return text if field.type != "select" or text in field.options else None


def build_payload(mapping: dict[str, FieldMap], fields: list[PortalField], state: IntakeState):
    """Returns (payload, sources, missing, unmatched). Every value comes from state.answers.
    missing: required facts we don't have, as (application field or None, label).
    unmatched: required dropdowns with no option for our value (the carrier doesn't offer
    that kind of business)."""
    payload, sources, missing, unmatched = {}, {}, [], []
    for field in fields:
        m = mapping.get(field.name)
        value = state.answers.get(m.source) if m and m.source else None
        text = apply_transform(m, value, field) if value is not None else None
        if text is not None:
            payload[field.name], sources[field.name] = text, m.source
        elif field.required and value is not None and m.transform == "choice":
            unmatched.append(f"“{field.label}” option for {value}")
        elif field.required:
            ours = m.source if m and m.source and find_question(m.source) else None
            missing.append((ours, label(ours) if ours else field.label))
    return payload, sources, missing, unmatched


# ---------------------------------------------------------------------------
# 4. Submit and read the answer
# ---------------------------------------------------------------------------
class _ResultReader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id") == "result":
            self.result = {k[5:]: v for k, v in a.items() if k.startswith("data-")}


def read_result(html: str) -> dict:
    reader = _ResultReader()
    reader.feed(html)
    return reader.result


def portal_client() -> httpx.Client:
    """The HTTP client the agent uses. Tests replace this with one wired to the app in-process."""
    return httpx.Client(base_url=config.PORTAL_BASE_URL, timeout=20)


def submit_to_carrier(slug: str, state: IntakeState, session_id: Optional[str] = None) -> QuoteResult:
    carrier, start = CARRIERS[slug], time.perf_counter()
    result = QuoteResult(carrier=slug, carrier_name=carrier.name, status="error")
    try:
        with portal_client() as client:
            fields = read_form(client.get(f"/portals/{slug}").text)
            mapping = get_mapping(slug, fields, session_id)
            payload, sources, missing, unmatched = build_payload(mapping, fields, state)
            result.sent, result.sources = payload, sources
            if unmatched:
                result.status = "not_offered"
                result.reason = f"{carrier.name}'s form has no {unmatched[0]}, so nothing was sent."
            elif missing:
                result.status = "needs_info"
                result.missing = [text for _, text in missing]
                result.missing_fields = [ours for ours, _ in missing if ours]
                result.reason = f"{carrier.name} needs: {', '.join(result.missing)}."
            else:
                answer = read_result(client.post(f"/portals/{slug}", data=payload).text)
                status = answer.get("status")
                result.status = status if status in ("quoted", "declined") else "error"
                result.premium = int(answer["premium"]) if answer.get("premium") else None
                result.reference = answer.get("reference", "")
                result.reason = answer.get("reason", "")
    except llm.BudgetExceeded:
        raise
    except Exception as e:  # one broken portal must not stop the others
        result.reason = f"The portal didn't respond as expected ({type(e).__name__})."
    result.seconds = round(time.perf_counter() - start, 2)
    return result


def get_quotes(state: IntakeState, session_id: Optional[str] = None) -> list[QuoteResult]:
    """Submit to every carrier at once. Quotes come first, cheapest first."""
    with ThreadPoolExecutor(max_workers=len(CARRIERS)) as pool:
        results = list(pool.map(lambda slug: submit_to_carrier(slug, state, session_id), CARRIERS))
    order = {"quoted": 0, "needs_info": 1, "declined": 2, "not_offered": 3, "error": 4}
    return sorted(results, key=lambda r: (order[r.status], r.premium or 0))
