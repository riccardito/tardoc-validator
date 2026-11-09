"""tardoc-validator -- validate, generate, evaluate, load.

  tardoc-validator validate <invoices.json|csv> [--rules R] [--json]
  tardoc-validator generate <out.json> [-n N] [--seed S] [--rules R]
  tardoc-validator eval [-n N] [--seed S] [--rules R]
  tardoc-validator load <Anhang_A2_Katalog_des_TARDOC.xlsx> [-o rules/official/tardoc-1.4c.yaml]
  tardoc-validator serve [--rules R] [--port 8000]   a local web page for live demos

Exit code of `validate`: 1 when any invoice has an error finding, else 0.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import validate
from .model import load_invoices
from .rules import RuleSet

SAMPLE = Path(__file__).resolve().parents[1] / "rules" / "sample.yaml"


def _rules(path: str | None) -> RuleSet:
    return RuleSet.load(Path(path) if path else SAMPLE)


def cmd_validate(a) -> int:
    rules, bad = _rules(a.rules), 0
    out = []
    for inv in load_invoices(Path(a.invoices)):
        findings = validate(inv, rules)
        bad += any(f.severity == "error" for f in findings)
        if a.json:
            out.append({"invoice_id": inv.invoice_id, "findings": [f.to_dict() for f in findings]})
        else:
            print(f"{inv.invoice_id}: {'OK' if not findings else f'{len(findings)} finding(s)'}")
            for f in findings:
                print(f"  [{f.severity}] {f.rule_id}: {f.message}")
    if a.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    return 1 if bad else 0


def cmd_generate(a) -> int:
    from .synth import Generator
    cases = Generator(_rules(a.rules), a.seed).cases(a.n)
    data = [{"invoice_id": c.invoice.invoice_id, "label": c.label,
             "injected": [vars(f) for f in c.faults],
             "patient": {"birth_date": c.invoice.patient.birth_date.isoformat(), "sex": c.invoice.patient.sex},
             "services": [{"code": s.code, "date": s.date.isoformat(), "session": s.session,
                           "quantity": s.quantity, **({"side": s.side} if s.side else {})}
                          for s in c.invoice.services]} for c in cases]
    Path(a.out).write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"{len(data)} invoices -> {a.out}")
    return 0


def cmd_eval(a) -> int:
    from .evaluate import report
    print(report(_rules(a.rules), a.n, a.seed))
    return 0


def cmd_load(a) -> int:
    from .loader import load_catalogue
    rules, stats = load_catalogue(Path(a.xlsx))
    rules.dump(Path(a.out))
    print(stats.summary())
    print(f"-> {a.out}")
    return 0


def cmd_serve(a) -> int:
    from .web import default_rules, serve
    serve(RuleSet.load(Path(a.rules)) if a.rules else default_rules(), a.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tardoc-validator", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("invoices")
    v.add_argument("--rules")
    v.add_argument("--json", action="store_true")
    g = sub.add_parser("generate")
    g.add_argument("out")
    for p in (g, sub.add_parser("eval")):
        p.add_argument("-n", type=int, default=1000)
        p.add_argument("--seed", type=int, default=7)
        p.add_argument("--rules")
    lo = sub.add_parser("load")
    lo.add_argument("xlsx")
    lo.add_argument("-o", "--out", default="rules/official/tardoc-1.4c.yaml")
    se = sub.add_parser("serve")
    se.add_argument("--rules", help="default: rules/official/tardoc-1.4c.yaml if loaded, else the sample")
    se.add_argument("--port", type=int, default=8000)
    a = ap.parse_args(argv)
    return {"validate": cmd_validate, "generate": cmd_generate, "eval": cmd_eval, "load": cmd_load,
            "serve": cmd_serve}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
