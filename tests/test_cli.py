import json
from pathlib import Path

from tardoc_validator.cli import main
from tardoc_validator.model import load_invoices

EX = Path(__file__).resolve().parents[1] / "examples"


def test_validate_json_reports_and_exits_1_on_errors(capsys):
    assert main(["validate", str(EX / "invoices.json")]) == 1
    out = capsys.readouterr().out
    assert "A-1001: OK" in out and "AA.00.0040/addon-without-parent" in out and "GK.10.0050/sex" in out


def test_validate_csv_groups_rows_into_invoices(capsys):
    assert main(["validate", str(EX / "invoices.csv"), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert [f["kind"] for f in data[0]["findings"]] == ["max-per-180-days"]


def test_a_clean_file_exits_0(tmp_path, capsys):
    one = json.loads((EX / "invoices.json").read_text(encoding="utf-8"))[0]
    p = tmp_path / "one.json"
    p.write_text(json.dumps(one), encoding="utf-8")
    assert main(["validate", str(p)]) == 0


def test_generate_writes_loadable_invoices_with_their_ground_truth(tmp_path, capsys):
    out = tmp_path / "cases.json"
    assert main(["generate", str(out), "-n", "40", "--seed", "2"]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data) == 40 and {"label", "injected"} <= data[0].keys()
    assert len(load_invoices(out)) == 40


def test_eval_prints_the_table(capsys):
    assert main(["eval", "-n", "100"]) == 0
    assert "recall" in capsys.readouterr().out


def test_load_writes_a_rule_set(tmp_path, capsys):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tarifpositionen"
    ws.append([None, "L-Nummer", "Bezeichnung", "Regeln bezogen auf die Tarifmechanik"])
    ws.append([None, "AA.00.0010", "Konsultation", "<= 1 Mal pro Sitzung"])
    xlsx, out = tmp_path / "c.xlsx", tmp_path / "rules.yaml"
    wb.save(xlsx)
    assert main(["load", str(xlsx), "-o", str(out)]) == 0
    assert "AA.00.0010" in out.read_text(encoding="utf-8")
