from pathlib import Path

import openpyxl
import pytest

from tardoc_validator.loader import LoadStats, load_catalogue, parse_rules_text
from tardoc_validator.rules import AgeRule, Limit, RuleSet

TEXT = "\n".join([
    "<= 1 Mal pro Sitzung_x000D_",
    "<= 2 Mal pro Tag",
    "<= 60 Mal pro 90 Tage",
    "<= 2 Sitzung pro 360 Tage",
    "<= 1 Mal pro Patient",
    "Nicht kumulierbar mit AA.00.0030 Besuch, erste 5 Min.",
    "Nicht kumulierbar mit CA Hausarztmedizin",
    "Nur kumulierbar mit AK.00.0010 Blutentnahme",
    "Zuschlag zu AA.00.0010 Konsultation, erste 5 Min.",
    "weiblich",
    "<= 16 Jahre (+0 Tage)",
    "&ge; 3 Monate (-3 Tage)",
    "Bei dieser Leistung muss die Seite angegeben werden.",
    "Für alle Tarifpositionen des (Unter-) Kapitels AA gilt: Nicht kumulierbar mit WA.00.0010 Evaluation",
    "Kumulierbar mit AR.00.0020 Wechselzeit",
    "Für alle Tarifpositionen der Leistungsgruppe LG-053 gilt: Kumulierbar mit CA.00 Grundleistungen",
    "<= 1 Mal pro Seite",
])


def test_every_supported_shape_becomes_a_rule():
    stats = LoadStats()
    p = parse_rules_text("AA.00.0020", TEXT, stats=stats)
    assert p.limits == [Limit(1, "session"), Limit(2, "day"), Limit(60, "days", 90),
                        Limit(2, "sessions", 360), Limit(1, "patient")]
    assert p.not_with == ["AA.00.0030", "CA", "WA.00.0010"]
    assert p.only_with == ["AK.00.0010"] and p.addon_to == ["AA.00.0010"]
    assert p.sex == "female" and p.side_required
    assert p.age == [AgeRule("<=", 16, "years", 0), AgeRule(">=", 3, "months", -3)]
    assert stats.lines == 17 and sum(stats.parsed.values()) == 14
    assert stats.informational == 1 and sum(stats.unsupported.values()) == 2


def test_the_parsed_position_validates_as_a_rule_set_round_trip():
    p = parse_rules_text("AA.00.0020", TEXT)
    rs = RuleSet("t", {p.code: p})
    again = RuleSet.from_dict(rs.to_dict())
    assert again.positions["AA.00.0020"] == p


def test_load_catalogue_reads_the_sheet_by_its_header(tmp_path: Path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tarifpositionen"
    ws.append([None, "TARDOC test"])
    ws.append([None, "L-Nummer", "Bezeichnung\n", "Typ", "Regeln bezogen auf die Tarifmechanik"])
    ws.append([None, "AA.00.0010", "Konsultation", "H", "<= 1 Mal pro Sitzung"])
    ws.append([None, None, None, None, None])
    ws.append([None, "AA.00.0020", "+ Konsultation", "Z", "Zuschlag zu AA.00.0010 Konsultation"])
    path = tmp_path / "catalogue.xlsx"
    wb.save(path)
    rs, stats = load_catalogue(path)
    assert sorted(rs.positions) == ["AA.00.0010", "AA.00.0020"]
    assert rs.positions["AA.00.0020"].addon_to == ["AA.00.0010"]
    assert stats.positions == 2 and "parsed into rules" in stats.summary()


def test_a_sheet_without_the_header_is_refused(tmp_path: Path):
    wb = openpyxl.Workbook()
    wb.active.title = "Tarifpositionen"
    path = tmp_path / "empty.xlsx"
    wb.save(path)
    with pytest.raises(ValueError, match="L-Nummer"):
        load_catalogue(path)
