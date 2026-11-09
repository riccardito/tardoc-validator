"""An explanation layer over the engine: the engine decides, the model only explains.

Each finding can carry two or three sentences in billing language, written by a
language model, that say why the rule exists and what a biller does about it.
The model never decides anything. It sees one finding the engine has already made,
and what it writes is checked before anyone reads it:

- it must name the finding's rule id, so the reader can look the rule up;
- every position code it names must be in the finding;
- every number it states must be in the finding (its message, or the titles of its positions).

An explanation that fails any check is withheld and the engine's own message stands.

Explanations are recorded once per rule (the pair, for a cumulation ban), not per
invoice: the prompt asks for the rule's reason and the fix, without the case's
dates and quantities, so one answer holds for every invoice that breaks the rule.
The recordings ship with the repository; replaying them needs no key and no
network. `tardoc-validator explain-record` makes them, `--live` asks the model.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .model import Finding
from .rules import RuleSet

DEFAULT_MODEL = "claude-opus-5-5"
PROMPT_VERSION = 2
CODE = re.compile(r"\b[A-Z]{2}\.\d{2}\.\d{4}\b")
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")

KIND_MEANING = {
    "not-with": "a cumulation ban (Kumulationsverbot): the two positions may not be billed in the same session",
    "only-with": "a restriction: the position may be billed only together with the positions it names",
    "addon-without-parent": "an add-on (Zuschlagsleistung) billed without the main service it adds to",
    "max-per-session": "a quantity limit per session",
    "max-per-day": "a quantity limit per day",
    "max-per-patient": "a quantity limit per patient",
    "sex": "a condition on the patient's sex",
    "age": "a condition on the patient's age on the day of the service",
    "side-missing": "the side (left, right or both) must be stated for this position",
}

SYSTEM = (
    "You explain findings of a TARDOC tariff validator to people who prepare Swiss outpatient "
    "invoices, in at most 60 words. A rule engine has already decided that the invoice breaks "
    "the rule; you do not decide anything and you do not doubt the finding. Write two or three plain sentences in "
    "English: what the rule means in billing terms, why a tariff has such a rule, and what the "
    "biller should check or correct. Name the rule id exactly as given. Name no tariff position "
    "other than the ones given. Do not state dates, quantities, limits, ages or session numbers "
    "-- the finding shows them, and your text must hold for every invoice that breaks this rule. "
    "No headings, no lists, no markdown."
)


def recording_key(f: Finding) -> str:
    """One explanation per rule; a cumulation ban is a rule over a pair."""
    return f"{f.rule_id}|{f.other}" if f.kind == "not-with" and f.other else f.rule_id


def kind_meaning(kind: str) -> str:
    if kind in KIND_MEANING:
        return KIND_MEANING[kind]
    if kind.startswith("max-"):
        return "a quantity limit over a period"
    return kind


def prompt(f: Finding, rules: RuleSet) -> str:
    def title(code: str | None) -> str:
        pos = rules.positions.get(code or "")
        return f"{code} ({pos.title})" if pos else str(code)

    lines = [f"Rule id: {f.rule_id}", f"Rule kind: {kind_meaning(f.kind)}", f"Position: {title(f.code)}"]
    if f.kind == "not-with" and f.other:
        lines.append(f"Other position: {title(f.other)}")
    lines.append(f"The engine's message, for context only: {f.message}")
    return "\n".join(lines)


def check(text: str, f: Finding, rules: RuleSet | None = None) -> list[str]:
    """The guard. Returns the reasons to withhold the explanation; empty means it may be shown.

    The finding is its message plus the titles of its positions -- the model is given the
    titles, and a title such as "first 5 min" carries numbers of its own.
    """
    problems = []
    if f.rule_id not in text:
        problems.append(f"does not name the rule id {f.rule_id}")
    allowed = {f.code, f.other} | set(CODE.findall(f.message))
    invented = sorted(set(CODE.findall(text)) - allowed)
    if invented:
        problems.append(f"names a position not in the finding: {', '.join(invented)}")
    titles = " ".join(rules.positions[c].title for c in (f.code, f.other)
                      if rules is not None and c in rules.positions)
    finding_numbers = set(NUMBER.findall(CODE.sub(" ", f.message + " " + titles)))
    stray = sorted(set(NUMBER.findall(CODE.sub(" ", text))) - finding_numbers)
    if stray:
        problems.append(f"states a number not in the finding: {', '.join(stray)}")
    return problems


@dataclass
class Explanation:
    text: str | None
    source: str  # recorded | live | withheld | none
    model: str | None = None
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: v for k, v in vars(self).items() if v not in (None, [])}


def load_recordings(path: Path) -> dict:
    if not path.exists():
        return {"entries": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def recordings_path(rules_path: Path) -> Path:
    return rules_path.with_suffix(".explanations.json")


class Explainer:
    def __init__(self, rules: RuleSet, recordings: dict | None = None, client=None,
                 model: str = DEFAULT_MODEL):
        self.rules = rules
        self.recorded = (recordings or {}).get("entries", {})
        self.client = client  # None: replay only
        self.model = model

    def explain(self, f: Finding) -> Explanation:
        if f.severity != "error":
            return Explanation(None, "none")
        entry = self.recorded.get(recording_key(f))
        if entry:
            text, source, model = entry["text"], "recorded", entry.get("model")
        elif self.client is not None:
            text, model = ask(self.client, prompt(f, self.rules), self.model)
            source = "live"
            if text is None:
                return Explanation(None, "withheld", model, ["the model declined"])
        else:
            return Explanation(None, "none")
        problems = check(text, f, self.rules)
        if problems:
            return Explanation(None, "withheld", model, problems)
        return Explanation(text, source, model)


def ask(client, user_prompt: str, model: str = DEFAULT_MODEL) -> tuple[str | None, str]:
    """One request. Server-side fallbacks re-run a declined request on another model."""
    response = client.beta.messages.create(
        model=model,
        max_tokens=2048,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low"},
        system=SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
    )
    if response.stop_reason == "refusal":
        return None, response.model
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return text, response.model


def live_client(key_file: str | None = None):
    """A client from ANTHROPIC_API_KEY, or from a key file. Needs `pip install .[llm]`."""
    import anthropic

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key and key_file:
        key = Path(key_file).read_text(encoding="utf-8-sig").strip()
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()


def record(findings: list[Finding], rules: RuleSet, client, out: Path,
           model: str = DEFAULT_MODEL, tries: int = 3) -> tuple[int, list[str]]:
    """Ask once per rule not yet recorded, keep only answers that pass the guard."""
    data = load_recordings(out)
    entries = data.setdefault("entries", {})
    added, failed = 0, []
    todo = {}
    for f in findings:
        if f.severity == "error" and recording_key(f) not in entries:
            todo.setdefault(recording_key(f), f)
    for key, f in sorted(todo.items()):
        for _ in range(tries):
            text, served = ask(client, prompt(f, rules), model)
            if text is not None and not check(text, f, rules):
                entries[key] = {"text": text, "model": served, "message": f.message}
                added += 1
                break
        else:
            failed.append(key)
    data.update({"prompt_version": PROMPT_VERSION, "requested_model": model, "rule_set": rules.name,
                 "recorded": dt.date.today().isoformat()})
    data["entries"] = dict(sorted(entries.items()))
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return added, failed
