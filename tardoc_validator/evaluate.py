"""`make eval` -- the engine against synthetic invoices with known faults.

  recall           share of injected faults that some finding names
  false-alarm rate share of invoices that get a finding no injected fault explains
                   (the clean and boundary invoices are where this is earned)
  precision        share of findings that name an injected fault
  runtime          mean time to validate one invoice

The baseline is the check most billing software starts with: quantity per
session against the limit, nothing else.
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass

from .engine import validate
from .model import Finding, Invoice
from .rules import RuleSet
from .synth import Case, Generator


def baseline_validate(invoice: Invoice, rules: RuleSet) -> list[Finding]:
    out = []
    totals: Counter = Counter()
    for s in invoice.services:
        totals[(s.code, s.session)] += s.quantity
    for (code, session), q in sorted(totals.items()):
        pos = rules.positions.get(code)
        for lim in pos.limits if pos else []:
            if lim.per == "session" and q > lim.max:
                out.append(Finding("error", f"{code}/{lim.kind}", lim.kind, code, "", session))
    return out


@dataclass
class Score:
    name: str
    cases: int
    faults: int
    found: int
    false_invoices: int
    findings: int
    true_findings: int
    seconds: float
    missed_by_kind: Counter

    @property
    def recall(self) -> float:
        return self.found / self.faults if self.faults else 1.0

    @property
    def false_alarm_rate(self) -> float:
        return self.false_invoices / self.cases if self.cases else 0.0

    @property
    def precision(self) -> float:
        return self.true_findings / self.findings if self.findings else 1.0

    @property
    def micros_per_invoice(self) -> float:
        return 1e6 * self.seconds / self.cases if self.cases else 0.0


def score(name: str, cases: list[Case], rules: RuleSet, fn=validate) -> Score:
    faults = found = false_inv = n_find = true_find = 0
    missed: Counter = Counter()
    t0 = time.perf_counter()
    results = [fn(c.invoice, rules) for c in cases]
    seconds = time.perf_counter() - t0
    for case, findings in zip(cases, results, strict=True):
        errors = [f for f in findings if f.severity == "error"]
        for fault in case.faults:
            faults += 1
            if any(fault.hit_by(f) for f in errors):
                found += 1
            else:
                missed[fault.kind] += 1
        unexplained = [f for f in errors if not any(fault.hit_by(f) for fault in case.faults)]
        false_inv += bool(unexplained)
        n_find += len(errors)
        true_find += len(errors) - len(unexplained)
    return Score(name, len(cases), faults, found, false_inv, n_find, true_find, seconds, missed)


def run(rules: RuleSet, n: int = 1000, seed: int = 7) -> tuple[list[Case], Score, Score]:
    cases = Generator(rules, seed).cases(n)
    return cases, score("engine", cases, rules), score("baseline: quantity per session", cases, rules,
                                                       baseline_validate)


def report(rules: RuleSet, n: int = 1000, seed: int = 7) -> str:
    cases, eng, base = run(rules, n, seed)
    labels = Counter(c.label for c in cases)
    kinds = Counter(f.kind for c in cases for f in c.faults)
    lines = [f"rule set {rules.name!r}: {len(rules.positions)} positions; {n} invoices, seed {seed}",
             f"  {labels['clean']} clean · {labels['boundary']} boundary probes · {labels['faulty']} with one "
             f"injected fault ({', '.join(f'{k} {v}' for k, v in sorted(kinds.items()))})", ""]
    lines.append(f"{'':34} {'recall':>8} {'false alarms':>13} {'precision':>10} {'per invoice':>12}")
    for s in (eng, base):
        lines.append(f"{s.name:34} {s.recall:8.1%} {s.false_alarm_rate:13.1%} {s.precision:10.1%} "
                     f"{s.micros_per_invoice:9.0f} µs")
    if eng.missed_by_kind:
        lines.append(f"\nmissed by the engine: {dict(eng.missed_by_kind)}")
    return "\n".join(lines)
