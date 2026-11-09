"""Rules as data. A rule set is a YAML file with one entry per tariff position.

    positions:
      AA.00.0020:
        title: Consultation, each further minute
        addon_to: [AA.00.0010]              # needs one of these in the same session
        limits:
          - {max: 15, per: session}         # per: session | day | patient
          - {max: 60, per: days, days: 90}  # rolling window, quantities summed
          - {max: 2, per: sessions, days: 360}   # distinct sessions in the window
        not_with: [AA.00.0030, CA]          # a position, a chapter or a sub-chapter
        only_with: [AA.25.0050]             # nothing else may share the session
        sex: female
        age: [{op: "<=", n: 16, unit: years, tolerance_days: 0}]
        side_required: true

Positions and chapters are matched by prefix on the dotted code: `CA` covers
`CA.00.0010`, `AA.10` covers `AA.10.0030`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

LIMIT_UNITS = ("session", "day", "patient", "days", "sessions")
AGE_OPS = ("<=", "<", ">=", ">")
AGE_UNITS = ("years", "months", "weeks", "days")


@dataclass(frozen=True)
class Limit:
    max: int
    per: str
    days: int | None = None

    @property
    def kind(self) -> str:
        return {"session": "max-per-session", "day": "max-per-day", "patient": "max-per-patient",
                "days": f"max-per-{self.days}-days", "sessions": f"max-sessions-per-{self.days}-days"}[self.per]


@dataclass(frozen=True)
class AgeRule:
    op: str
    n: int
    unit: str
    tolerance_days: int = 0


@dataclass
class Position:
    code: str
    title: str = ""
    limits: list[Limit] = field(default_factory=list)
    not_with: list[str] = field(default_factory=list)
    only_with: list[str] = field(default_factory=list)
    addon_to: list[str] = field(default_factory=list)
    sex: str | None = None
    age: list[AgeRule] = field(default_factory=list)
    side_required: bool = False


def matches(code: str, ref: str) -> bool:
    """True when `ref` names `code` itself or a chapter that contains it."""
    return code == ref or code.startswith(ref + ".")


@dataclass
class RuleSet:
    name: str
    positions: dict[str, Position]

    @classmethod
    def from_dict(cls, d: dict, name: str = "rules") -> RuleSet:
        positions = {}
        for code, p in (d.get("positions") or {}).items():
            limits = [Limit(int(x["max"]), x["per"], x.get("days")) for x in p.get("limits", [])]
            for lim in limits:
                if lim.per not in LIMIT_UNITS or (lim.per in ("days", "sessions")) != bool(lim.days):
                    raise ValueError(f"{code}: bad limit {lim}")
            ages = [AgeRule(a["op"], int(a["n"]), a["unit"], int(a.get("tolerance_days", 0)))
                    for a in p.get("age", [])]
            for a in ages:
                if a.op not in AGE_OPS or a.unit not in AGE_UNITS:
                    raise ValueError(f"{code}: bad age rule {a}")
            positions[code] = Position(
                code=code, title=p.get("title", ""), limits=limits,
                not_with=list(p.get("not_with", [])), only_with=list(p.get("only_with", [])),
                addon_to=list(p.get("addon_to", [])), sex=p.get("sex"), age=ages,
                side_required=bool(p.get("side_required", False)))
        return cls(d.get("name", name), positions)

    @classmethod
    def load(cls, path: Path) -> RuleSet:
        return cls.from_dict(yaml.safe_load(path.read_text(encoding="utf-8")), name=path.stem)

    def to_dict(self) -> dict:
        out = {}
        for code, p in sorted(self.positions.items()):
            e: dict = {"title": p.title} if p.title else {}
            if p.limits:
                e["limits"] = [{k: v for k, v in vars(x).items() if v is not None} for x in p.limits]
            for key in ("not_with", "only_with", "addon_to"):
                if getattr(p, key):
                    e[key] = getattr(p, key)
            if p.sex:
                e["sex"] = p.sex
            if p.age:
                e["age"] = [vars(a) for a in p.age]
            if p.side_required:
                e["side_required"] = True
            out[code] = e
        return {"name": self.name, "positions": out}

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=False, width=120),
                        encoding="utf-8", newline="\n")
