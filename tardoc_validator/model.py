"""The input (an invoice: a patient and a list of services) and the output (findings)."""
from __future__ import annotations

import csv
import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

SEXES = ("female", "male")


@dataclass(frozen=True)
class Service:
    code: str
    date: dt.date
    session: str
    quantity: int = 1
    side: str | None = None  # "L", "R" or "B" where the tariff asks for a side


@dataclass(frozen=True)
class Patient:
    birth_date: dt.date
    sex: str  # "female" | "male"

    def __post_init__(self) -> None:
        if self.sex not in SEXES:
            raise ValueError(f"sex must be one of {SEXES}, got {self.sex!r}")


@dataclass
class Invoice:
    invoice_id: str
    patient: Patient
    services: list[Service] = field(default_factory=list)

    def sessions(self) -> dict[str, list[Service]]:
        out: dict[str, list[Service]] = {}
        for s in self.services:
            out.setdefault(s.session, []).append(s)
        return out


@dataclass(frozen=True)
class Finding:
    severity: str  # "error" | "warning"
    rule_id: str  # "<code>/<kind>" -- stable, greppable
    kind: str
    code: str
    message: str
    session: str | None = None
    other: str | None = None  # the second position of a cumulation finding

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def _date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def invoice_from_dict(d: dict) -> Invoice:
    p = d["patient"]
    services = [Service(code=s["code"], date=_date(s["date"]), session=str(s["session"]),
                        quantity=int(s.get("quantity", 1)), side=s.get("side") or None)
                for s in d["services"]]
    return Invoice(str(d.get("invoice_id", "1")), Patient(_date(p["birth_date"]), p["sex"]), services)


def load_invoices(path: Path) -> list[Invoice]:
    """JSON (one invoice or a list) or CSV (one service per row, grouped by invoice_id)."""
    if path.suffix.lower() == ".csv":
        groups: dict[str, dict] = {}
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                inv = groups.setdefault(row["invoice_id"], {
                    "invoice_id": row["invoice_id"],
                    "patient": {"birth_date": row["birth_date"], "sex": row["sex"]},
                    "services": []})
                inv["services"].append(row)
        return [invoice_from_dict(g) for g in groups.values()]
    data = json.loads(path.read_text(encoding="utf-8"))
    return [invoice_from_dict(d) for d in (data if isinstance(data, list) else [data])]
