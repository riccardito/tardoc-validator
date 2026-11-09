"""The rule engine: one invoice and one rule set in, a list of findings out.

The engine decides; nothing else does. Each rule kind is a small function over the
invoice, so a finding can always name the rule that produced it.

Age conditions are read as exact durations from the birth date: `<= 16 years
(+0 days)` holds while the service date is on or before the 16th birthday; the
tolerance moves that boundary by the given days. This is the validator's reading
of the catalogue's notation, stated so it can be argued with.
"""
from __future__ import annotations

import calendar
import datetime as dt
from collections import defaultdict
from dataclasses import replace

from .model import Finding, Invoice, Service
from .rules import Limit, Position, RuleSet, matches

SEX_WORD = {"female": "female", "male": "male"}


def add_months(d: dt.date, months: int) -> dt.date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return dt.date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def boundary(birth: dt.date, n: int, unit: str) -> dt.date:
    if unit == "years":
        return add_months(birth, 12 * n)
    if unit == "months":
        return add_months(birth, n)
    return birth + dt.timedelta(weeks=n) if unit == "weeks" else birth + dt.timedelta(days=n)


def _finding(kind: str, pos: Position, message: str, session: str | None = None,
             other: str | None = None) -> Finding:
    return Finding("error", f"{pos.code}/{kind}", kind, pos.code, message, session, other)


def check_limit(pos: Position, lim: Limit, mine: list[Service]) -> list[Finding]:
    def over(groups: dict, unit: str) -> list[Finding]:
        return [_finding(lim.kind, pos, f"{pos.code} billed {q} times {unit} {key}; the limit is {lim.max}",
                         session=key if lim.per == "session" else None)
                for key, q in groups.items() if q > lim.max]

    if lim.per in ("session", "day"):
        groups: dict = defaultdict(int)
        for s in mine:
            groups[s.session if lim.per == "session" else s.date.isoformat()] += s.quantity
        return over(groups, "in session" if lim.per == "session" else "on")
    if lim.per == "patient":
        total = sum(s.quantity for s in mine)
        return over({"for this patient": total}, "") if total > lim.max else []
    ordered = sorted(mine, key=lambda s: s.date)
    for s in ordered:
        start = s.date - dt.timedelta(days=lim.days - 1)
        window = [x for x in ordered if start <= x.date <= s.date]
        count = len({x.session for x in window}) if lim.per == "sessions" else sum(x.quantity for x in window)
        if count > lim.max:
            what = f"in {count} sessions" if lim.per == "sessions" else f"{count} times"
            return [_finding(lim.kind, pos, f"{pos.code} billed {what} within {lim.days} days "
                             f"up to {s.date.isoformat()}; the limit is {lim.max}")]
    return []


def check_age(pos: Position, invoice: Invoice, mine: list[Service]) -> list[Finding]:
    out = []
    for rule in pos.age:
        edge = boundary(invoice.patient.birth_date, rule.n, rule.unit) + dt.timedelta(days=rule.tolerance_days)
        for s in mine:
            ok = {"<=": s.date <= edge, "<": s.date < edge, ">=": s.date >= edge, ">": s.date > edge}[rule.op]
            if not ok:
                out.append(_finding("age", pos, f"{pos.code} requires age {rule.op} {rule.n} {rule.unit}"
                                    + (f" ({rule.tolerance_days:+d} days)" if rule.tolerance_days else "")
                                    + f"; not met on {s.date.isoformat()}", session=s.session))
                break
    return out


def check_session(pos: Position, session: str, codes: set[str]) -> list[Finding]:
    out = []
    others = codes - {pos.code}
    for other in sorted(others):
        if any(matches(other, ref) for ref in pos.not_with):
            out.append(_finding("not-with", pos, f"{pos.code} may not be billed with {other} in one session",
                                session=session, other=other))
    if pos.only_with:
        for other in sorted(others):
            if not any(matches(other, ref) for ref in pos.only_with):
                out.append(_finding("only-with", pos, f"{pos.code} may only be billed with "
                                    f"{', '.join(pos.only_with)}; {other} shares the session",
                                    session=session, other=other))
    if pos.addon_to and not any(matches(c, ref) for c in others for ref in pos.addon_to):
        out.append(_finding("addon-without-parent", pos, f"{pos.code} is an add-on to "
                            f"{', '.join(pos.addon_to)}, and none is billed in session {session}",
                            session=session))
    return out


def merge_mutual(findings: list[Finding]) -> list[Finding]:
    """One finding per pair and session when two positions exclude each other.

    TARDOC often writes a cumulation ban on both positions. Checked side by side,
    the same broken combination would be reported twice -- once from each rule --
    which a biller reads as two errors. The first finding (the lower code) is kept
    and reworded; the pair's other rule is named in its message.
    """
    seen: dict[tuple, int] = {}
    out: list[Finding] = []
    for f in findings:
        if f.kind != "not-with" or f.other is None:
            out.append(f)
            continue
        key = (f.session, frozenset((f.code, f.other)))
        if key not in seen:
            seen[key] = len(out)
            out.append(f)
            continue
        first = out[seen[key]]
        out[seen[key]] = replace(first, message=(
            f"{first.code} and {first.other} may not be billed in one session; "
            f"each position's rules exclude the other ({first.rule_id}, {f.rule_id})"))
    return out


def validate(invoice: Invoice, rules: RuleSet) -> list[Finding]:
    findings: list[Finding] = []
    by_code: dict[str, list[Service]] = defaultdict(list)
    for s in invoice.services:
        by_code[s.code].append(s)
    for code in sorted(by_code):
        if code not in rules.positions:
            findings.append(Finding("warning", f"{code}/unknown-code", "unknown-code", code,
                                    f"{code} is not a position in rule set {rules.name!r}"))
    for session, services in sorted(invoice.sessions().items()):
        codes = {s.code for s in services}
        for code in sorted(codes & rules.positions.keys()):
            findings += check_session(rules.positions[code], session, codes)
    findings = merge_mutual(findings)
    for code, mine in sorted(by_code.items()):
        pos = rules.positions.get(code)
        if pos is None:
            continue
        for lim in pos.limits:
            findings += check_limit(pos, lim, mine)
        if pos.sex and invoice.patient.sex != pos.sex:
            findings.append(_finding("sex", pos, f"{pos.code} is for {SEX_WORD[pos.sex]} patients only"))
        findings += check_age(pos, invoice, mine)
        if pos.side_required:
            for s in mine:
                if not s.side:
                    findings.append(_finding("side-missing", pos, f"{pos.code} needs the side (L, R or B)",
                                             session=s.session))
                    break
    return findings
