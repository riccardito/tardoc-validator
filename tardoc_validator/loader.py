"""Build a full rule set from the official catalogue, Anhang A2 "Katalog des TARDOC".

The catalogue is published by OAAT AG as an Excel file. It is not part of this
repository: download it, then run `tardoc-validator load <file.xlsx>`. The output
lands in `rules/official/`, which git ignores.

The sheet `Tarifpositionen` has one row per position; the column "Regeln bezogen
auf die Tarifmechanik" holds the rules as lines of German text. The parser reads
the line shapes below and counts everything else, so the coverage is stated, not
assumed:

  <= N Mal pro Sitzung | Tag | Patient       a quantity limit
  <= N Mal pro D Tage                        a rolling-window limit
  <= N Sitzung pro D Tage                    a sessions-in-window limit
  Nicht kumulierbar mit <code>               a cumulation ban
  Nur kumulierbar mit <code>                 an only-with list
  Zuschlag zu <code>                         an add-on and its parents
  weiblich | männlich                        a sex condition
  <= | < | >= | > N Jahre|Monate|Wochen (±T Tage)   an age condition
  Bei dieser Leistung muss die Seite angegeben werden.   side required
  Für alle Tarifpositionen des (Unter-) Kapitels X gilt: <rule>   the same, inherited

"Kumulierbar mit ..." lines permit rather than restrict and are counted as
informational. Rules over Leistungsgruppen (LG-...) and per-side, per-sample and
similar units are counted as unsupported.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .rules import AgeRule, Limit, Position, RuleSet

CODE = r"([A-Z]{2}(?:\.\d{2}(?:\.\d{4})?)?)(?=\s|$)"
PATTERNS = [
    ("limit-session", re.compile(r"^<= (\d+) Mal pro Sitzung$")),
    ("limit-day", re.compile(r"^<= (\d+) Mal pro Tag$")),
    ("limit-patient", re.compile(r"^<= (\d+) Mal pro Patient$")),
    ("limit-days", re.compile(r"^<= (\d+) Mal pro (\d+) Tage$")),
    ("limit-sessions", re.compile(r"^<= (\d+) Sitzung(?:en)? pro (\d+) Tage$")),
    ("not-with", re.compile(r"^Nicht kumulierbar mit " + CODE)),
    ("only-with", re.compile(r"^Nur kumulierbar mit " + CODE)),
    ("addon", re.compile(r"^Zuschlag zu " + CODE)),
    ("sex", re.compile(r"^(weiblich|männlich)$")),
    ("age", re.compile(r"^(<=|<|&ge;|>=|>) (\d+) (Jahre|Monate?|Wochen?|Tage)(?: \(([+-]\d+) Tage\))?$")),
    ("side", re.compile(r"^Bei dieser Leistung muss die Seite angegeben werden\.$")),
]
INHERITED = re.compile(r"^Für alle Tarifpositionen des \(Unter-\) Kapitels \S+ gilt: (.*)$")
INFORMATIONAL = re.compile(r"^Kumulierbar mit ")
UNIT = {"Jahre": "years", "Monat": "months", "Monate": "months", "Woche": "weeks", "Wochen": "weeks",
        "Tage": "days"}


@dataclass
class LoadStats:
    positions: int = 0
    lines: int = 0
    parsed: Counter = field(default_factory=Counter)
    informational: int = 0
    unsupported: Counter = field(default_factory=Counter)

    def summary(self) -> str:
        p = sum(self.parsed.values())
        u = sum(self.unsupported.values())
        out = [f"{self.positions} positions, {self.lines} rule lines",
               f"  parsed into rules   {p:6} ({p / self.lines:.1%})",
               f"  informational       {self.informational:6} ({self.informational / self.lines:.1%})",
               f"  unsupported         {u:6} ({u / self.lines:.1%})",
               "  parsed by kind: " + ", ".join(f"{k} {v}" for k, v in self.parsed.most_common())]
        out.append("  unsupported shapes: " + ", ".join(f"{k} {v}" for k, v in self.unsupported.most_common(6)))
        return "\n".join(out)


def shape(line: str) -> str:
    s = re.sub(r"LG-\d+.*$", "LG-…", line)
    s = re.sub(r"[A-Z]{2}(\.\d{2}(\.\d{4})?)? .*$", "<code> …", s)
    return re.sub(r"\d+", "N", s)[:60]


def apply(pos: Position, line: str, stats: LoadStats) -> None:
    m = INHERITED.match(line)
    if m:
        line = m.group(1)
    if "LG-" in line:
        stats.unsupported[shape(line)] += 1
        return
    if INFORMATIONAL.match(line):
        stats.informational += 1
        return
    for kind, pat in PATTERNS:
        m = pat.match(line)
        if not m:
            continue
        stats.parsed[kind] += 1
        g = m.groups()
        if kind.startswith("limit-"):
            per = kind[len("limit-"):]
            lim = Limit(int(g[0]), per, int(g[1]) if per in ("days", "sessions") else None)
            if lim not in pos.limits:
                pos.limits.append(lim)
        elif kind in ("not-with", "only-with", "addon"):
            target = {"not-with": pos.not_with, "only-with": pos.only_with, "addon": pos.addon_to}[kind]
            if g[0] not in target:
                target.append(g[0])
        elif kind == "sex":
            pos.sex = "female" if g[0] == "weiblich" else "male"
        elif kind == "age":
            op = ">=" if g[0] == "&ge;" else g[0]
            rule = AgeRule(op, int(g[1]), UNIT[g[2]], int(g[3]) if g[3] else 0)
            if rule not in pos.age:
                pos.age.append(rule)
        else:
            pos.side_required = True
        return
    stats.unsupported[shape(line)] += 1


def parse_rules_text(code: str, text: str, title: str = "", stats: LoadStats | None = None) -> Position:
    stats = stats if stats is not None else LoadStats()
    pos = Position(code=code, title=title)
    for raw in text.replace("_x000D_", "").splitlines():
        line = raw.strip()
        if line:
            stats.lines += 1
            apply(pos, line, stats)
    return pos


def load_catalogue(path: Path, sheet: str = "Tarifpositionen") -> tuple[RuleSet, LoadStats]:
    try:
        import openpyxl
    except ImportError as e:  # pragma: no cover - the extra is documented
        raise SystemExit("the loader needs openpyxl: pip install 'tardoc-validator[excel]'") from e
    wb = openpyxl.load_workbook(path, read_only=True)
    rows = wb[sheet].iter_rows(values_only=True)
    header = None
    for row in rows:
        if row and "L-Nummer" in [str(c).strip() if c else "" for c in row]:
            header = [str(c).strip() if c else "" for c in row]
            break
    if header is None:
        raise ValueError(f"no 'L-Nummer' header row in sheet {sheet!r}")
    i_code = header.index("L-Nummer")
    i_title = next(i for i, h in enumerate(header) if h.startswith("Bezeichnung"))
    i_rules = next(i for i, h in enumerate(header) if h.startswith("Regeln"))
    stats, positions = LoadStats(), {}
    for row in rows:
        code = row[i_code] if len(row) > i_code else None
        if not code:
            continue
        code = str(code).strip()
        positions[code] = parse_rules_text(code, str(row[i_rules] or ""), str(row[i_title] or "").strip(), stats)
    stats.positions = len(positions)
    return RuleSet(f"official-{path.stem}", positions), stats
