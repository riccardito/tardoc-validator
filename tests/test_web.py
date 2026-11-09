import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from tardoc_validator.rules import RuleSet
from tardoc_validator.web import App, handler

RULES = RuleSet.load(Path(__file__).resolve().parents[1] / "rules" / "sample.yaml")


@pytest.fixture(scope="module")
def base():
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(App(RULES, seed=11)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.read().decode()


def post(url, data):
    req = urllib.request.Request(url, json.dumps(data).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_the_page_and_the_rule_set_info(base):
    status, page = get(base + "/")
    assert status == 200 and "Random case" in page and "Quick entry" in page
    assert json.loads(get(base + "/api/info")[1]) == {"name": "sample-1.4c", "positions": 23}


def test_search_by_code_prefix_and_by_title(base):
    by_code = json.loads(get(base + "/api/search?q=AA.00")[1])
    assert [h["code"] for h in by_code][:2] == ["AA.00.0010", "AA.00.0020"]
    assert json.loads(get(base + "/api/search?q=ultrasound")[1])[0]["code"].startswith("GK.")


def test_validate_answers_valid_or_names_the_rule(base):
    patient = {"birth_date": "1980-01-01", "sex": "male"}
    ok = post(base + "/api/validate", {"patient": patient, "services": [
        {"code": "AA.00.0010", "date": "2026-02-02", "session": "1"},
        {"code": "AA.00.0020", "date": "2026-02-02", "session": "1", "quantity": 5}]})
    assert ok == (200, {"valid": True, "findings": [], "titles": {
        "AA.00.0010": "Medical consultation, first 5 min",
        "AA.00.0020": "Medical consultation, each further minute (add-on)"}})
    status, bad = post(base + "/api/validate", {"patient": patient, "services": [
        {"code": "GK.10.0050", "date": "2026-02-02", "session": "1"}]})
    assert status == 200 and not bad["valid"] and bad["findings"][0]["rule_id"] == "GK.10.0050/sex"


def test_a_bad_request_is_a_400_not_a_crash(base):
    status, body = post(base + "/api/validate", {"patient": {"birth_date": "x", "sex": "male"}, "services": []})
    assert status == 400 and "error" in body


def test_a_random_case_carries_what_was_planted_and_the_engine_finds_it(base):
    for _ in range(20):
        case = json.loads(get(base + "/api/random")[1])
        status, result = post(base + "/api/validate", {"patient": case["patient"], "services": case["services"]})
        assert status == 200
        planted = case["planted"]["faults"]
        found = {(f["kind"], f["code"]) for f in result["findings"]} | \
                {(f["kind"], f.get("other")) for f in result["findings"]}
        if planted:
            assert all((p["kind"], p["code"]) in found for p in planted)
        else:
            assert result["valid"]
