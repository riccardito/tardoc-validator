"""Synthetic invoices with known faults -- the ground truth `make eval` measures against.

Three kinds of case, from a fixed seed:

  clean     built so that every rule holds: the patient is eligible for every
            position, no two positions in a session exclude each other, every
            quantity sits inside every limit
  boundary  clean, but deliberately AT a limit: a quantity equal to the maximum,
            two sessions exactly one window apart, a service on the last day an
            age rule allows. These are the false-alarm probes
  faulty    a clean invoice with exactly one fault injected, of one of eleven kinds

The generator's knowledge of the rules is written independently of the engine: it
constructs invoices that satisfy or break a rule; it never asks the engine.
"""
from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field

from .model import Invoice, Patient, Service
from .rules import Position, RuleSet

START = dt.date(2026, 1, 5)


# Written apart from the engine on purpose: a shared helper would hide a shared bug.
def matches(code: str, ref: str) -> bool:
    return code.split(".")[: len(ref.split("."))] == ref.split(".")


def boundary(birth: dt.date, n: int, unit: str) -> dt.date:
    if unit in ("weeks", "days"):
        return birth + dt.timedelta(days=n * (7 if unit == "weeks" else 1))
    months = n * 12 if unit == "years" else n
    year, month = birth.year + (birth.month - 1 + months) // 12, (birth.month - 1 + months) % 12 + 1
    day = birth.day
    while True:
        try:
            return dt.date(year, month, day)
        except ValueError:
            day -= 1


@dataclass(frozen=True)
class Fault:
    kind: str
    code: str
    other: str | None = None

    def hit_by(self, f) -> bool:
        if f.kind != self.kind:
            return False
        if self.other is None:
            return f.code == self.code
        return {f.code, f.other} == {self.code, self.other}


@dataclass
class Case:
    label: str  # clean | boundary | faulty
    invoice: Invoice
    faults: list[Fault] = field(default_factory=list)


class Generator:
    def __init__(self, rules: RuleSet, seed: int = 7):
        self.rules = rules
        self.rnd = random.Random(seed)
        self.p = rules.positions
        self.n = 0

    # --- what the generator knows about the rules -------------------------------
    def conflicts(self, a: str, b: str) -> bool:
        pa, pb = self.p[a], self.p[b]
        if any(matches(b, r) for r in pa.not_with) or any(matches(a, r) for r in pb.not_with):
            return True
        if pa.only_with and not any(matches(b, r) for r in pa.only_with):
            return True
        return bool(pb.only_with) and not any(matches(a, r) for r in pb.only_with)

    def age_ok(self, pos: Position, birth: dt.date, day: dt.date) -> bool:
        for a in pos.age:
            edge = boundary(birth, a.n, a.unit) + dt.timedelta(days=a.tolerance_days)
            if not {"<=": day <= edge, "<": day < edge, ">=": day >= edge, ">": day > edge}[a.op]:
                return False
        return True

    def eligible(self, code: str, patient: Patient, day: dt.date) -> bool:
        pos = self.p[code]
        return (pos.sex in (None, patient.sex)) and self.age_ok(pos, patient.birth_date, day)

    def cap(self, pos: Position) -> int:
        """The largest quantity a single clean service may carry."""
        return min([lim.max for lim in pos.limits if lim.per != "sessions"] + [6])

    def side(self, pos: Position) -> str | None:
        return self.rnd.choice("LRB") if pos.side_required else None

    def fits(self, code: str, session_codes: set[str]) -> bool:
        return all(not self.conflicts(code, c) for c in session_codes)

    # --- clean invoices ---------------------------------------------------------
    def patient(self) -> Patient:
        band = self.rnd.choice(["newborn", "child", "teen", "adult", "adult"])
        days = {"newborn": (2, 35), "child": (400, 4000), "teen": (4400, 6000), "adult": (7700, 29000)}[band]
        return Patient(START - dt.timedelta(days=self.rnd.randint(*days)), self.rnd.choice(["female", "male"]))

    def clean(self) -> tuple[Invoice, set[str]]:
        self.n += 1
        patient, used, services = self.patient(), set(), []
        mains = [c for c, pos in self.p.items() if not pos.addon_to]
        day = START
        for k in range(self.rnd.randint(1, 3)):
            day += dt.timedelta(days=self.rnd.randint(1, 20))
            sid, codes = f"S{k + 1}", set()
            for code in self.rnd.sample(mains, len(mains)):
                if len(codes) >= self.rnd.randint(1, 3):
                    break
                if code not in used and self.eligible(code, patient, day) and self.fits(code, codes):
                    codes.add(code)
            for code in list(codes):
                for addon, pos in self.p.items():
                    if (any(matches(code, r) for r in pos.addon_to) and addon not in used | codes
                            and self.eligible(addon, patient, day) and self.fits(addon, codes)
                            and self.rnd.random() < 0.5):
                        codes.add(addon)
            for code in sorted(codes):
                pos = self.p[code]
                services.append(Service(code, day, sid, self.rnd.randint(1, self.cap(pos)), self.side(pos)))
            used |= codes
        return Invoice(f"INV-{self.n:05d}", patient, services), used

    # --- boundary probes (clean, at the edge) ----------------------------------
    def probe(self) -> Case | None:
        inv, used = self.clean()
        kind = self.rnd.choice(["quantity", "window", "age"])
        if kind == "quantity":
            for i, s in enumerate(inv.services):
                pos = self.p[s.code]
                lims = [lim.max for lim in pos.limits if lim.per != "sessions"]
                if lims:
                    inv.services[i] = Service(s.code, s.date, s.session, min(lims), s.side)
                    return Case("boundary", inv)
        if kind == "window":
            for code, pos in self.rnd.sample(sorted(self.p.items()), len(self.p)):
                win = [lim for lim in pos.limits if lim.per in ("days", "sessions")]
                if not win or code in used or pos.addon_to or pos.only_with:
                    continue
                lim, d1 = win[0], START + dt.timedelta(days=40)
                d2 = d1 + dt.timedelta(days=lim.days)
                if not (self.eligible(code, inv.patient, d1) and self.eligible(code, inv.patient, d2)):
                    continue
                q = 1 if lim.per == "sessions" else min(lim.max, self.cap(pos))
                for d, sid in ((d1, "W1"), (d2, "W2")):
                    inv.services.append(Service(code, d, sid, q, self.side(pos)))
                return Case("boundary", inv)
        for code, pos in sorted(self.p.items()):
            if pos.age and code not in used and not pos.only_with and not pos.addon_to \
                    and pos.sex in (None, inv.patient.sex):
                a = pos.age[0]
                edge = boundary(inv.patient.birth_date, a.n, a.unit) + dt.timedelta(days=a.tolerance_days)
                day = {"<=": edge, "<": edge - dt.timedelta(days=1)}.get(a.op)
                if day and day >= inv.patient.birth_date and self.age_ok(pos, inv.patient.birth_date, day):
                    inv.services.append(Service(code, day, "E1", 1, self.side(pos)))
                    return Case("boundary", inv)
        return None

    # --- one injected fault -----------------------------------------------------
    def faulty(self) -> Case | None:
        inv, used = self.clean()
        kind = self.rnd.choice(["session", "day", "window", "sessions", "patient", "not-with",
                                "only-with", "addon", "sex", "age", "side"])
        return getattr(self, "_f_" + kind.replace("-", "_"))(inv, used)

    def _new_single(self, inv: Invoice, code: str, day: dt.date, sid: str, q: int = 1) -> None:
        inv.services.append(Service(code, day, sid, q, self.side(self.p[code])))

    def _f_session(self, inv, used):
        for i, s in enumerate(inv.services):
            pos = self.p[s.code]
            lim = next((x for x in pos.limits if x.per == "session"), None)
            others = [x.max for x in pos.limits if x.per in ("day", "patient", "days")]
            if lim and all(m > lim.max for m in others):
                inv.services[i] = Service(s.code, s.date, s.session, lim.max + 1, s.side)
                return Case("faulty", inv, [Fault(lim.kind, s.code)])
        return None

    def _f_day(self, inv, used):
        for code in self._unused_mains(used):
            pos = self.p[code]
            lim = next((x for x in pos.limits if x.per == "day"), None)
            if not lim or any(x.per in ("days", "patient") for x in pos.limits):
                continue
            per_session = next((x.max for x in pos.limits if x.per == "session"), lim.max + 1)
            day, left, k = START + dt.timedelta(days=60), lim.max + 1, 0
            if not self.eligible(code, inv.patient, day):
                continue
            while left > 0:
                q = min(per_session, left)
                self._new_single(inv, code, day, f"D{k}", q)
                left, k = left - q, k + 1
            return Case("faulty", inv, [Fault(lim.kind, code)])
        return None

    def _f_window(self, inv, used):
        for code in self._unused_mains(used):
            pos = self.p[code]
            lim = next((x for x in pos.limits if x.per == "days"), None)
            if not lim or any(x.per in ("session", "day", "patient") for x in pos.limits):
                continue
            d1 = START + dt.timedelta(days=60)
            d2 = d1 + dt.timedelta(days=self.rnd.randint(1, lim.days - 1))
            if not (self.eligible(code, inv.patient, d1) and self.eligible(code, inv.patient, d2)):
                continue
            q1 = self.rnd.randint(1, lim.max)
            self._new_single(inv, code, d1, "P1", q1)
            self._new_single(inv, code, d2, "P2", lim.max + 1 - q1)
            return Case("faulty", inv, [Fault(lim.kind, code)])
        return None

    def _f_sessions(self, inv, used):
        for code in self._unused_mains(used):
            pos = self.p[code]
            lim = next((x for x in pos.limits if x.per == "sessions"), None)
            if not lim or any(x.per in ("days", "patient") for x in pos.limits):
                continue
            days = [START + dt.timedelta(days=60 + i * max(1, (lim.days - 2) // (lim.max + 1)))
                    for i in range(lim.max + 1)]
            if not all(self.eligible(code, inv.patient, d) for d in days):
                continue
            for i, d in enumerate(days):
                self._new_single(inv, code, d, f"R{i}")
            return Case("faulty", inv, [Fault(lim.kind, code)])
        return None

    def _f_patient(self, inv, used):
        for code in self._unused_mains(used, allow_only_with=True):
            pos = self.p[code]
            lim = next((x for x in pos.limits if x.per == "patient"), None)
            if not lim:
                continue
            day = inv.patient.birth_date + dt.timedelta(days=3)
            days = [day + dt.timedelta(days=i) for i in range(lim.max + 1)]
            if not all(self.eligible(code, inv.patient, d) for d in days):
                continue
            for i, d in enumerate(days):
                self._new_single(inv, code, d, f"T{i}")
            return Case("faulty", inv, [Fault(lim.kind, code)])
        return None

    def _f_not_with(self, inv, used):
        for sid, services in inv.sessions().items():
            codes = {s.code for s in services}
            for a in sorted(codes):
                if self.p[a].only_with:
                    continue
                for b in self._unused_mains(used | codes):
                    pb = self.p[b]
                    pair = any(matches(b, r) for r in self.p[a].not_with) or any(
                        matches(a, r) for r in pb.not_with)
                    rest = codes - {a}
                    if pair and not pb.only_with and self.eligible(b, inv.patient, services[0].date) \
                            and self.fits(b, rest):
                        self._new_single(inv, b, services[0].date, sid)
                        return Case("faulty", inv, [Fault("not-with", a, b)])
        return None

    def _f_only_with(self, inv, used):
        for code in self._unused_mains(used, allow_only_with=True):
            pos = self.p[code]
            if not pos.only_with:
                continue
            day = inv.patient.birth_date + dt.timedelta(days=5)
            if not self.eligible(code, inv.patient, day):
                continue
            for b in self._unused_mains(used | {code}):
                pb = self.p[b]
                if any(matches(b, r) for r in pos.only_with) or pb.only_with:
                    continue
                if any(matches(b, r) for r in pos.not_with) or any(matches(code, r) for r in pb.not_with):
                    continue
                if self.eligible(b, inv.patient, day) and not pb.side_required:
                    self._new_single(inv, code, day, "O1")
                    self._new_single(inv, b, day, "O1")
                    return Case("faulty", inv, [Fault("only-with", code, b)])
        return None

    def _f_addon(self, inv, used):
        addons = [c for c, pos in sorted(self.p.items()) if pos.addon_to and c not in used]
        for sid, services in inv.sessions().items():
            codes = {s.code for s in services}
            for a in addons:
                pa = self.p[a]
                if not any(matches(c, r) for c in codes for r in pa.addon_to) and self.fits(a, codes):
                    self._new_single(inv, a, services[0].date, sid)
                    return Case("faulty", inv, [Fault("addon-without-parent", a)])
        return None

    def _f_sex(self, inv, used):
        return self._add_ineligible(inv, used, lambda pos: pos.sex and pos.sex != inv.patient.sex, "sex")

    def _f_age(self, inv, used):
        return self._add_ineligible(inv, used, lambda pos: pos.age and pos.sex in (None, inv.patient.sex), "age")

    def _add_ineligible(self, inv, used, wanted, kind):
        for sid, services in inv.sessions().items():
            codes, day = {s.code for s in services}, services[0].date
            for c in self._unused_mains(used | codes):
                pos = self.p[c]
                if not wanted(pos) or not self.fits(c, codes):
                    continue
                if kind == "age" and self.age_ok(pos, inv.patient.birth_date, day):
                    continue
                if kind == "sex" and not self.age_ok(pos, inv.patient.birth_date, day):
                    continue
                self._new_single(inv, c, day, sid)
                return Case("faulty", inv, [Fault(kind, c)])
        return None

    def _f_side(self, inv, used):
        for sid, services in inv.sessions().items():
            codes, day = {s.code for s in services}, services[0].date
            for c in self._unused_mains(used | codes):
                pos = self.p[c]
                if pos.side_required and self.eligible(c, inv.patient, day) and self.fits(c, codes):
                    inv.services.append(Service(c, day, sid, 1, None))
                    return Case("faulty", inv, [Fault("side-missing", c)])
        return None

    def _unused_mains(self, used: set[str], allow_only_with: bool = False) -> list[str]:
        out = [c for c, pos in self.p.items() if c not in used and not pos.addon_to
               and (allow_only_with or not pos.only_with)]
        self.rnd.shuffle(out)
        return out

    # --- the suite --------------------------------------------------------------
    def cases(self, n: int = 1000, clean_share: float = 0.4, boundary_share: float = 0.1) -> list[Case]:
        out: list[Case] = []
        while len(out) < n:
            r = self.rnd.random()
            if r < clean_share:
                out.append(Case("clean", self.clean()[0]))
            elif r < clean_share + boundary_share:
                case = self.probe()
                if case:
                    out.append(case)
            else:
                case = self.faulty()
                if case:
                    out.append(case)
        return out
