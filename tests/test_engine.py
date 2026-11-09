import datetime as dt

import pytest

from tardoc_validator import Invoice, Patient, RuleSet, Service, validate

D = dt.date(2026, 3, 2)
ADULT_F = Patient(dt.date(1985, 5, 5), "female")


def rules(positions: dict) -> RuleSet:
    return RuleSet.from_dict({"name": "t", "positions": positions})


def kinds(inv: Invoice, rs: RuleSet) -> list[str]:
    return [f.kind for f in validate(inv, rs)]


def inv(*services, patient=ADULT_F) -> Invoice:
    return Invoice("T", patient, list(services))


@pytest.mark.parametrize("q,expect", [(2, []), (3, ["max-per-session"])])
def test_session_limit_holds_at_the_maximum(q, expect):
    rs = rules({"X.00.0010": {"limits": [{"max": 2, "per": "session"}]}})
    assert kinds(inv(Service("X.00.0010", D, "1", q)), rs) == expect


def test_day_limit_sums_across_sessions():
    rs = rules({"X.00.0010": {"limits": [{"max": 2, "per": "day"}]}})
    two = [Service("X.00.0010", D, "1"), Service("X.00.0010", D, "2")]
    assert kinds(inv(*two), rs) == []
    assert kinds(inv(*two, Service("X.00.0010", D, "3")), rs) == ["max-per-day"]


@pytest.mark.parametrize("gap,expect", [(30, []), (29, ["max-per-30-days"])])
def test_rolling_window_edge(gap, expect):
    rs = rules({"X.00.0010": {"limits": [{"max": 1, "per": "days", "days": 30}]}})
    a, b = Service("X.00.0010", D, "1"), Service("X.00.0010", D + dt.timedelta(days=gap), "2")
    assert kinds(inv(a, b), rs) == expect


def test_sessions_in_window_count_sessions_not_quantity():
    rs = rules({"X.00.0010": {"limits": [{"max": 2, "per": "sessions", "days": 360}]}})
    many = [Service("X.00.0010", D, "1", 5), Service("X.00.0010", D + dt.timedelta(days=10), "2", 5)]
    assert kinds(inv(*many), rs) == []
    third = Service("X.00.0010", D + dt.timedelta(days=20), "3")
    assert kinds(inv(*many, third), rs) == ["max-sessions-per-360-days"]


def test_patient_limit():
    rs = rules({"X.00.0010": {"limits": [{"max": 1, "per": "patient"}]}})
    s = [Service("X.00.0010", D, "1"), Service("X.00.0010", D + dt.timedelta(days=400), "2")]
    assert kinds(inv(*s), rs) == ["max-per-patient"]


def test_cumulation_ban_by_position_and_by_chapter():
    rs = rules({"A.00.0010": {"not_with": ["B"]}, "B.05.0010": {}, "C.00.0010": {}})
    assert kinds(inv(Service("A.00.0010", D, "1"), Service("B.05.0010", D, "1")), rs) == ["not-with"]
    assert kinds(inv(Service("A.00.0010", D, "1"), Service("B.05.0010", D, "2")), rs) == []
    assert kinds(inv(Service("A.00.0010", D, "1"), Service("C.00.0010", D, "1")), rs) == []


def test_a_chapter_prefix_does_not_match_a_longer_chapter_name():
    rs = rules({"A.00.0010": {"not_with": ["B.0"]}, "B.05.0010": {}})
    assert kinds(inv(Service("A.00.0010", D, "1"), Service("B.05.0010", D, "1")), rs) == []


def test_only_with():
    rs = rules({"A.00.0010": {"only_with": ["K.00.0010"]}, "K.00.0010": {}, "Z.00.0010": {}})
    ok = inv(Service("A.00.0010", D, "1"), Service("K.00.0010", D, "1"))
    assert kinds(ok, rs) == []
    assert kinds(inv(Service("A.00.0010", D, "1"), Service("Z.00.0010", D, "1")), rs) == ["only-with"]


def test_addon_needs_its_parent_in_the_same_session():
    rs = rules({"P.00.0010": {}, "P.00.0020": {"addon_to": ["P.00.0010"]}})
    assert kinds(inv(Service("P.00.0010", D, "1"), Service("P.00.0020", D, "1")), rs) == []
    assert kinds(inv(Service("P.00.0010", D, "1"), Service("P.00.0020", D, "2")), rs) == \
        ["addon-without-parent"]


def test_sex():
    rs = rules({"X.00.0010": {"sex": "male"}})
    assert kinds(inv(Service("X.00.0010", D, "1")), rs) == ["sex"]
    assert kinds(inv(Service("X.00.0010", D, "1"), patient=Patient(dt.date(1980, 1, 1), "male")), rs) == []


def test_age_edge_and_tolerance():
    child = Patient(dt.date(2010, 3, 2), "male")  # 16th birthday on 2026-03-02
    rs = rules({"X.00.0010": {"age": [{"op": "<=", "n": 16, "unit": "years"}]},
                "Y.00.0010": {"age": [{"op": "<=", "n": 16, "unit": "years", "tolerance_days": 10}]},
                "Z.00.0010": {"age": [{"op": ">=", "n": 16, "unit": "years", "tolerance_days": -3}]}})
    assert kinds(inv(Service("X.00.0010", D, "1"), patient=child), rs) == []
    day_after = D + dt.timedelta(days=1)
    assert kinds(inv(Service("X.00.0010", day_after, "1"), patient=child), rs) == ["age"]
    assert kinds(inv(Service("Y.00.0010", D + dt.timedelta(days=10), "1"), patient=child), rs) == []
    assert kinds(inv(Service("Z.00.0010", D - dt.timedelta(days=3), "1"), patient=child), rs) == []
    assert kinds(inv(Service("Z.00.0010", D - dt.timedelta(days=4), "1"), patient=child), rs) == ["age"]


def test_a_leap_day_birthday_ends_on_the_28th():
    leap = Patient(dt.date(2008, 2, 29), "female")
    rs = rules({"X.00.0010": {"age": [{"op": "<=", "n": 18, "unit": "years"}]}})
    assert kinds(inv(Service("X.00.0010", dt.date(2026, 2, 28), "1"), patient=leap), rs) == []
    assert kinds(inv(Service("X.00.0010", dt.date(2026, 3, 1), "1"), patient=leap), rs) == ["age"]


def test_side_required_and_unknown_code():
    rs = rules({"X.00.0010": {"side_required": True}})
    assert kinds(inv(Service("X.00.0010", D, "1", side="L")), rs) == []
    assert kinds(inv(Service("X.00.0010", D, "1")), rs) == ["side-missing"]
    findings = validate(inv(Service("Q.99.9999", D, "1")), rs)
    assert [(f.severity, f.kind) for f in findings] == [("warning", "unknown-code")]


def test_bad_rule_data_is_refused():
    with pytest.raises(ValueError):
        rules({"X.00.0010": {"limits": [{"max": 1, "per": "days"}]}})
    with pytest.raises(ValueError):
        rules({"X.00.0010": {"age": [{"op": "=", "n": 1, "unit": "years"}]}})
    with pytest.raises(ValueError):
        Patient(D, "other")
