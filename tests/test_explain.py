"""The explanation layer: the engine decides, the model only explains -- and is checked."""
import json
import subprocess
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tardoc_validator import RuleSet, validate
from tardoc_validator.cli import main
from tardoc_validator.explain import (
    CODE,
    PROMPT_VERSION,
    Explainer,
    check,
    live_client,
    load_recordings,
    record,
    recording_key,
    tour,
)
from tardoc_validator.model import load_invoices
from tardoc_validator.rules import matches
from tardoc_validator.synth import Generator
from tardoc_validator.web import App

ROOT = Path(__file__).resolve().parents[1]
RULES = RuleSet.load(ROOT / "rules" / "sample.yaml")
RECORDED = load_recordings(ROOT / "rules" / "sample.explanations.json")


def findings_by_rule() -> dict:
    """Every error finding the recordings must hold for: the rule tour, the examples, both seeds."""
    out = defaultdict(list)
    found = tour(RULES) + [f for inv in load_invoices(ROOT / "examples" / "invoices.json")
                           for f in validate(inv, RULES)]
    for seed, n in ((7, 3000), (99, 8000)):
        found += [f for c in Generator(RULES, seed).cases(n) for f in validate(c.invoice, RULES)]
    for f in found:
        if f.severity == "error":
            out[recording_key(f)].append(f)
    return out


BY_RULE = findings_by_rule()


def rule_keys(rules: RuleSet) -> set[str]:
    """Every rule of a set, read from the rule data -- independently of the engine and the tour."""
    keys = set()
    for c, p in rules.positions.items():
        keys |= {f"{c}/{lim.kind}" for lim in p.limits}
        keys |= {f"{c}/{k}" for k, on in (("sex", p.sex), ("age", p.age), ("side-missing", p.side_required),
                                          ("addon-without-parent", p.addon_to), ("only-with", p.only_with)) if on}
        for o in rules.positions:
            if o != c and any(matches(o, ref) for ref in p.not_with):
                lo, hi = sorted((c, o))
                keys.add(f"{lo}/not-with|{hi}")
    return keys


def test_the_tour_reaches_every_rule_of_the_set():
    assert {recording_key(f) for f in tour(RULES) if f.severity == "error"} == rule_keys(RULES)


def test_every_rule_has_a_recording():
    assert set(BY_RULE) == set(RECORDED["entries"]) == rule_keys(RULES)


@pytest.mark.parametrize("key", sorted(RECORDED["entries"]))
def test_each_recording_names_the_rule_and_invents_nothing_for_every_invoice_it_covers(key):
    text = RECORDED["entries"][key]["text"]
    for f in BY_RULE[key]:
        assert f.rule_id in text
        assert set(CODE.findall(text)) <= {f.code, f.other} | set(CODE.findall(f.message))
        assert check(text, f, RULES) == []


def test_a_fresh_seed_is_covered_by_the_recordings():
    ex = Explainer(RULES, RECORDED)
    notes = [ex.explain(f) for c in Generator(RULES, 2026).cases(2000) for f in validate(c.invoice, RULES)
             if f.severity == "error"]
    assert notes and {n.source for n in notes} == {"recorded"}


# --- the guard can fail: planted faults ------------------------------------------------------------

F = validate(load_invoices(ROOT / "examples" / "invoices.json")[1], RULES)[0]  # AA.00.0010/not-with
GOOD = RECORDED["entries"][recording_key(F)]["text"]


def test_the_guard_passes_the_recorded_text():
    assert check(GOOD, F, RULES) == []


@pytest.mark.parametrize("bad,reason", [
    (GOOD.replace(F.rule_id, "this rule"), "does not name the rule id"),
    (GOOD + " Bill CA.00.0010 instead.", "names a position not in the finding: CA.00.0010"),
    (GOOD + " The limit is 4 per day.", "states a number not in the finding: 4"),
])
def test_the_guard_withholds_a_planted_fault(bad, reason):
    assert any(reason in p for p in check(bad, F, RULES))
    ex = Explainer(RULES, {"entries": {recording_key(F): {"text": bad, "model": "m"}}})
    note = ex.explain(F)
    assert note.source == "withheld" and note.text is None


def test_a_number_from_the_findings_own_title_is_allowed():
    # "Medical consultation, first 5 min": the model was given the title.
    assert check(GOOD + " It covers the first 5 min.", F, RULES) == []


def test_warnings_get_no_explanation():
    w = replace(F, severity="warning")
    assert Explainer(RULES, RECORDED).explain(w).source == "none"


# --- the live path, against a fake client ----------------------------------------------------------

class FakeClient:
    def __init__(self, text: str, stop: str = "end_turn"):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.text, self.stop = text, stop

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason=self.stop, model=kw["model"],
                               content=[SimpleNamespace(type="text", text=self.text)])


def test_live_asks_only_where_nothing_is_recorded_and_still_checks():
    client = FakeClient(GOOD)
    note = Explainer(RULES, {"entries": {}}, client).explain(F)
    assert note.source == "live" and note.text == GOOD
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert F.rule_id in call["messages"][0]["content"]
    Explainer(RULES, RECORDED, client).explain(F)
    assert len(client.calls) == 1  # the recording answered


def test_live_output_that_invents_a_position_is_withheld():
    note = Explainer(RULES, {"entries": {}}, FakeClient(GOOD + " Or bill CA.00.0010.")).explain(F)
    assert note.source == "withheld" and "CA.00.0010" in note.problems[0]


def test_a_refusal_is_withheld():
    note = Explainer(RULES, {"entries": {}}, FakeClient("", stop="refusal")).explain(F)
    assert note.source == "withheld" and note.problems == ["the model declined"]


# --- the surfaces ----------------------------------------------------------------------------------

def test_replay_needs_no_sdk():
    code = ("import sys; sys.modules['anthropic'] = None\n"
            "from pathlib import Path\n"
            "from tardoc_validator.cli import main\n"
            "sys.exit(main(['validate', 'examples/invoices.json', '--explain']) != 1)")
    assert subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True).returncode == 0


def test_the_cli_prints_the_explanation(capsys):
    main(["validate", str(ROOT / "examples" / "invoices.json"), "--explain", "--json"])
    rows = json.loads(capsys.readouterr().out)[1]["findings"]
    assert all(r["explanation"]["source"] == "recorded" for r in rows)


def test_the_web_page_carries_the_explanation():
    app = App(RULES, seed=1, explainer=Explainer(RULES, RECORDED))
    inv = load_invoices(ROOT / "examples" / "invoices.json")[1]
    payload = {"patient": {"birth_date": inv.patient.birth_date.isoformat(), "sex": inv.patient.sex},
               "services": [{"code": s.code, "date": s.date.isoformat(), "session": s.session,
                             "quantity": s.quantity, "side": s.side} for s in inv.services]}
    out = app.check(payload)
    assert all(f["explanation"]["text"] for f in out["findings"])


# --- recording -------------------------------------------------------------------------------------

class ScriptedClient(FakeClient):
    """Answers in turn: the first reply can be made to fail the guard."""

    def __init__(self, replies):
        super().__init__("")
        self.replies = list(replies)

    def _create(self, **kw):
        self.text = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return super()._create(**kw)


def test_record_retries_an_answer_the_guard_rejects(tmp_path):
    out = tmp_path / "x.explanations.json"
    client = ScriptedClient([GOOD + " Bill CA.00.0010.", GOOD])
    added, failed = record([F, F], RULES, client, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert (added, failed, len(client.calls)) == (1, [], 2)
    assert data["entries"][recording_key(F)]["text"] == GOOD and data["prompt_version"] == PROMPT_VERSION
    assert record([F], RULES, client, out) == (0, [])  # already recorded: no call
    assert len(client.calls) == 2


def test_record_reports_a_rule_it_cannot_record(tmp_path):
    added, failed = record([F], RULES, FakeClient("no rule id here"), tmp_path / "y.json", tries=2)
    assert (added, failed) == (0, [recording_key(F)])


def test_the_record_command_writes_beside_the_rule_set(tmp_path, monkeypatch, capsys):
    import tardoc_validator.explain as ex
    rules = tmp_path / "r.yaml"
    rules.write_text((ROOT / "rules" / "sample.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(ex, "live_client", lambda key_file=None: KeyedFake())
    assert main(["explain-record", "--rules", str(rules), "-n", "200"]) == 0
    assert "failed the guard: []" in capsys.readouterr().out
    assert json.loads((tmp_path / "r.explanations.json").read_text(encoding="utf-8"))["entries"]


class KeyedFake(FakeClient):
    """Answers with the rule id it is asked about -- enough to pass the guard."""

    def __init__(self):
        super().__init__("")

    def _create(self, **kw):
        self.text = kw["messages"][0]["content"].splitlines()[0].replace("Rule id: ", "Rule ") + " applies."
        return super()._create(**kw)


def test_live_client_reads_a_key_file(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    key = tmp_path / "k"
    key.write_text("sk-ant-test\n", encoding="utf-8")
    assert live_client(str(key)).api_key == "sk-ant-test"
