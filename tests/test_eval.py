"""The evaluation, and the proof that it can fail.

A perfect score is only evidence if a broken engine would not get one. Each test
below plants one bug in the engine and asserts that a number moves.
"""
from pathlib import Path

import pytest

from tardoc_validator import engine, evaluate
from tardoc_validator.rules import AgeRule, Limit, RuleSet
from tardoc_validator.synth import Generator, boundary

RULES = RuleSet.load(Path(__file__).resolve().parents[1] / "rules" / "sample.yaml")


@pytest.fixture(scope="module")
def cases():
    return Generator(RULES, seed=7).cases(600)


def test_the_suite_has_every_label_and_fault_kind(cases):
    labels = {c.label for c in cases}
    kinds = {f.kind for c in cases for f in c.faults}
    assert labels == {"clean", "boundary", "faulty"}
    assert {"max-per-session", "max-per-day", "max-per-patient", "not-with", "only-with",
            "addon-without-parent", "sex", "age", "side-missing"} <= kinds
    assert any(k.startswith("max-sessions-per-") for k in kinds)


def test_the_engine_finds_every_fault_and_raises_no_false_alarm(cases):
    s = evaluate.score("engine", cases, RULES)
    assert s.recall == 1.0 and s.false_alarm_rate == 0.0 and s.precision == 1.0


def test_the_baseline_misses_most_faults(cases):
    s = evaluate.score("baseline", cases, RULES, evaluate.baseline_validate)
    assert s.recall < 0.2


def test_the_generator_is_deterministic():
    a = [c.invoice for c in Generator(RULES, 3).cases(50)]
    b = [c.invoice for c in Generator(RULES, 3).cases(50)]
    assert a == b


def test_the_generator_date_arithmetic_agrees_with_the_engine_on_hard_dates():
    import datetime as dt
    for birth in (dt.date(2008, 2, 29), dt.date(2025, 1, 31), dt.date(2020, 12, 31)):
        for n, unit in ((1, "months"), (18, "years"), (6, "weeks"), (20, "days")):
            assert boundary(birth, n, unit) == engine.boundary(birth, n, unit)


# --- positive controls: a planted bug must move a number -----------------------

def test_an_off_by_one_on_the_age_edge_raises_false_alarms(cases, monkeypatch):
    real = engine.check_age

    def strict(pos, inv, mine):
        pos.age = [AgeRule("<" if a.op == "<=" else a.op, a.n, a.unit, a.tolerance_days) for a in pos.age]
        return real(pos, inv, mine)

    rules = RuleSet.load(Path(__file__).resolve().parents[1] / "rules" / "sample.yaml")
    monkeypatch.setattr(engine, "check_age", strict)
    assert evaluate.score("mutant", cases, rules).false_alarm_rate > 0


def test_a_window_one_day_too_wide_raises_false_alarms(cases, monkeypatch):
    real = engine.check_limit

    def wide(pos, lim, mine):
        if lim.per in ("days", "sessions"):
            out = real(pos, Limit(lim.max, lim.per, lim.days + 1), mine)
            return [type(f)(f.severity, f.rule_id, lim.kind, f.code, f.message, f.session, f.other) for f in out]
        return real(pos, lim, mine)

    monkeypatch.setattr(engine, "check_limit", wide)
    assert evaluate.score("mutant", cases, RULES).false_alarm_rate > 0


@pytest.mark.parametrize("kind", ["not-with", "addon-without-parent", "only-with"])
def test_a_dropped_check_loses_recall(cases, monkeypatch, kind):
    real = engine.check_session
    monkeypatch.setattr(engine, "check_session",
                        lambda pos, s, codes: [f for f in real(pos, s, codes) if f.kind != kind])
    assert evaluate.score("mutant", cases, RULES).recall < 1.0


def test_the_report_names_both_rows():
    text = evaluate.report(RULES, n=120, seed=1)
    assert "engine" in text and "baseline: quantity per session" in text and "µs" in text
