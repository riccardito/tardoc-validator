"""`tardoc-validator serve` -- a small local web page for live demos.

Type the positions someone names (or draw a random generated case), press Check,
and see whether the invoice holds and which rule breaks. Standard library only;
binds to 127.0.0.1.

  GET  /                    the page
  GET  /api/info            the loaded rule set
  GET  /api/search?q=AA.00  positions whose code or title matches
  GET  /api/random          a generated invoice; its injected fault is returned separately
  POST /api/validate        {"patient": {...}, "services": [...]} -> findings, each with its explanation

Explanations come from the recordings beside the rule set (see `explain.py`); with
`--live` the model is asked where nothing is recorded. The engine decides either way.
"""
from __future__ import annotations

import json
import random
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .engine import validate
from .model import invoice_from_dict
from .rules import RuleSet
from .synth import Generator

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL = ROOT / "rules" / "official" / "tardoc-1.4c.yaml"
SAMPLE = ROOT / "rules" / "sample.yaml"


def default_rules_path() -> Path:
    return OFFICIAL if OFFICIAL.exists() else SAMPLE


def default_rules() -> RuleSet:
    return RuleSet.load(default_rules_path())


class App:
    def __init__(self, rules: RuleSet, seed: int | None = None, explainer=None):
        self.rules = rules
        self.explainer = explainer
        self.gen = Generator(rules, seed if seed is not None else random.randrange(1 << 30))

    def info(self) -> dict:
        return {"name": self.rules.name, "positions": len(self.rules.positions)}

    def search(self, q: str, limit: int = 20) -> list[dict]:
        q = q.strip().lower()
        hits = [(c, p.title) for c, p in sorted(self.rules.positions.items())
                if q and (c.lower().startswith(q) or q in p.title.lower())]
        return [{"code": c, "title": t} for c, t in hits[:limit]]

    def random_case(self) -> dict:
        case = None
        while case is None:
            r = self.gen.rnd.random()
            case = (self.gen.faulty() if r < 0.6 else self.gen.probe() if r < 0.7
                    else type("C", (), {"label": "clean", "invoice": self.gen.clean()[0], "faults": []})())
        inv = case.invoice
        return {
            "patient": {"birth_date": inv.patient.birth_date.isoformat(), "sex": inv.patient.sex},
            "services": [{"code": s.code, "date": s.date.isoformat(), "session": s.session,
                          "quantity": s.quantity, "side": s.side or ""} for s in inv.services],
            "planted": {"label": case.label, "faults": [{k: v for k, v in vars(f).items() if v}
                                                       for f in case.faults]},
        }

    def check(self, payload: dict) -> dict:
        inv = invoice_from_dict({"invoice_id": "demo", **payload})
        findings = validate(inv, self.rules)
        titles = {c: self.rules.positions[c].title for c in {s.code for s in inv.services}
                  if c in self.rules.positions}
        return {"valid": not any(f.severity == "error" for f in findings),
                "findings": [self._with_explanation(f) for f in findings], "titles": titles}

    def _with_explanation(self, f) -> dict:
        d = f.to_dict()
        if self.explainer is not None:
            note = self.explainer.explain(f)
            if note.source != "none":
                d["explanation"] = note.to_dict()
        return d


def handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if url.path == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif url.path == "/api/info":
                self._json(app.info())
            elif url.path == "/api/search":
                self._json(app.search(q.get("q", [""])[0]))
            elif url.path == "/api/random":
                self._json(app.random_case())
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/api/validate":
                return self._json({"error": "not found"}, 404)
            try:
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                self._json(app.check(payload))
            except (ValueError, KeyError, TypeError) as e:
                self._json({"error": f"{type(e).__name__}: {e}"}, 400)

        def log_message(self, *args) -> None:  # keep the demo terminal quiet
            pass

    return Handler


def serve(rules: RuleSet, port: int = 8000, explainer=None) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), handler(App(rules, explainer=explainer)))
    print(f"tardoc-validator: {len(rules.positions)} positions ({rules.name}) -> http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>tardoc-validator</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2330;--muted:#5d6675;--line:#dfe3ea;--ok:#167a4a;--bad:#b42335;--warn:#9a6700;--accent:#2457c5}
@media (prefers-color-scheme:dark){:root{--bg:#12151b;--card:#1b2029;--ink:#e6e9ef;--muted:#9aa3b2;--line:#2c3340;--ok:#45c08a;--bad:#ff6b7d;--warn:#e3b341;--accent:#7aa2ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,sans-serif}
main{max-width:1000px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}.sub{color:var(--muted);margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
label{font-size:13px;color:var(--muted);display:block;margin-bottom:4px}
input,select,textarea{font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:6px 8px;width:100%}
textarea{min-height:90px;font-family:ui-monospace,monospace}
.row{display:flex;gap:12px;flex-wrap:wrap}.row>div{flex:1;min-width:140px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:4px;border-bottom:1px solid var(--line);font-size:14px}
th{color:var(--muted);font-weight:500}td input{padding:4px 6px}
button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:6px;padding:7px 14px;cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}button.x{padding:2px 8px}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.verdict{font-size:18px;font-weight:600}.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.finding{padding:8px 0;border-bottom:1px solid var(--line)}.finding code{font-weight:600}
.why{margin-top:8px;padding:8px 10px;border-left:3px solid var(--accent);background:var(--bg);border-radius:0 6px 6px 0;font-size:14px}
.why .src{display:block;color:var(--muted);font-size:12px;margin-bottom:2px}.why.withheld{border-color:var(--warn)}
.title{color:var(--muted);font-size:13px}.hint{color:var(--muted);font-size:13px;margin-top:6px}
</style></head><body><main>
<h1>tardoc-validator</h1><p class="sub" id="info">loading rules…</p>

<div class="card"><div class="row">
<div><label for="bd">Patient birth date</label><input id="bd" type="date" value="1980-01-01"></div>
<div><label for="sex">Sex</label><select id="sex"><option value="female">female</option><option value="male">male</option></select></div>
<div><label for="day">Date of service</label><input id="day" type="date"></div></div></div>

<div class="card"><label for="quick">Quick entry — one position per line: <code>CODE [xQTY] [L|R|B] [@SESSION]</code></label>
<textarea id="quick" placeholder="AA.00.0010&#10;AA.00.0020 x10&#10;AG.00.0010 R"></textarea>
<div class="actions"><button id="add">Add to the invoice</button><button id="random">Random case</button><button id="clear">Clear</button></div>
<p class="hint">Random case draws a generated invoice — clean, at a limit, or with one planted fault — and tells you what was planted after the check.</p></div>

<div class="card"><table><thead><tr><th>Code</th><th>Title</th><th>Date</th><th>Session</th><th>Qty</th><th>Side</th><th></th></tr></thead>
<tbody id="rows"></tbody></table><datalist id="codes"></datalist>
<div class="actions"><button id="row">+ Row</button><button class="primary" id="check">Check</button></div></div>

<div class="card" id="result" hidden></div>
</main><script>
const $=s=>document.querySelector(s), rows=$('#rows'); let planted=null; const titles={};
$('#day').value=new Date().toISOString().slice(0,10);
fetch('/api/info').then(r=>r.json()).then(i=>$('#info').textContent=`${i.positions} positions loaded (${i.name}). Nothing leaves this machine.`);
function esc(t){return String(t).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function addRow(s={}){const tr=document.createElement('tr');
 tr.innerHTML=`<td><input list="codes" class="code" value="${esc(s.code||'')}"></td><td class="title t"></td>
 <td><input type="date" class="date" value="${esc(s.date||$('#day').value)}"></td><td><input class="session" value="${esc(s.session||'1')}" size="4"></td>
 <td><input type="number" min="1" class="qty" value="${esc(s.quantity||1)}"></td>
 <td><select class="side"><option></option><option>L</option><option>R</option><option>B</option></select></td><td><button class="x">×</button></td>`;
 tr.querySelector('.side').value=s.side||''; tr.querySelector('.x').onclick=()=>tr.remove();
 const code=tr.querySelector('.code'); code.oninput=()=>lookup(code.value,tr); rows.appendChild(tr); lookup(code.value,tr)}
function lookup(q,tr){if(!q)return; fetch('/api/search?q='+encodeURIComponent(q)).then(r=>r.json()).then(h=>{
 $('#codes').innerHTML=h.map(x=>`<option value="${esc(x.code)}">${esc(x.title)}</option>`).join('');
 const m=h.find(x=>x.code===q.trim()); if(tr)tr.querySelector('.t').textContent=m?m.title:''})}
$('#row').onclick=()=>addRow();
$('#clear').onclick=()=>{rows.innerHTML='';$('#result').hidden=true;planted=null};
$('#add').onclick=()=>{for(const line of $('#quick').value.split('\n')){const p=line.trim().split(/\s+/); if(!p[0])continue;
 const s={code:p[0].toUpperCase()}; for(const t of p.slice(1)){if(/^x\d+$/i.test(t))s.quantity=+t.slice(1);
 else if(/^[LRB]$/i.test(t))s.side=t.toUpperCase(); else if(t[0]==='@')s.session=t.slice(1);
 else if(/^\d{4}-\d{2}-\d{2}$/.test(t))s.date=t} addRow(s)} $('#quick').value=''; planted=null};
$('#random').onclick=()=>fetch('/api/random').then(r=>r.json()).then(c=>{rows.innerHTML='';
 $('#bd').value=c.patient.birth_date;$('#sex').value=c.patient.sex;c.services.forEach(addRow);planted=c.planted;$('#result').hidden=true});
$('#check').onclick=()=>{const services=[...rows.children].map(tr=>({code:tr.querySelector('.code').value.trim().toUpperCase(),
 date:tr.querySelector('.date').value,session:tr.querySelector('.session').value||'1',quantity:+tr.querySelector('.qty').value||1,
 side:tr.querySelector('.side').value||null})).filter(s=>s.code);
 fetch('/api/validate',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({patient:{birth_date:$('#bd').value,sex:$('#sex').value},services})}).then(r=>r.json()).then(show)};
function why(e){if(!e)return '';
 if(e.text)return `<div class="why"><span class="src">Why — explained by ${esc(e.model||'the model')} (${esc(e.source)}); the engine decided</span>${esc(e.text)}</div>`;
 return `<div class="why withheld"><span class="src">Explanation withheld by the guard</span>${esc((e.problems||[]).join('; '))}</div>`}
function show(r){const box=$('#result');box.hidden=false; if(r.error){box.innerHTML=`<p class="bad">${esc(r.error)}</p>`;return}
 let h=`<p class="verdict ${r.valid?'ok':'bad'}">${r.valid?'✓ Valid — no rule broken':'✗ '+r.findings.filter(f=>f.severity==='error').length+' rule(s) broken'}</p>`;
 for(const f of r.findings)h+=`<div class="finding"><span class="${f.severity==='error'?'bad':'warn'}">[${f.severity}]</span> <code>${esc(f.rule_id)}</code><br>${esc(f.message)}
  ${r.titles[f.code]?`<div class="title">${esc(f.code)}: ${esc(r.titles[f.code])}</div>`:''}${why(f.explanation)}</div>`;
 if(planted)h+=`<p class="hint">Planted in this case: <b>${planted.faults.length?planted.faults.map(x=>esc(x.kind+' · '+x.code+(x.other?' + '+x.other:''))).join(', '):esc(planted.label)+', nothing broken on purpose'}</b></p>`;
 box.innerHTML=h}
addRow();
</script></body></html>
"""
