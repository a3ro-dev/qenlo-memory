"""progress report for akshat: research/to_human/index.html, rebuilt from data/results.jsonl and data/qa_*.jsonl.

usage: python -I research/src/report.py
"""

from __future__ import annotations

import html
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA, OUT = ROOT / "data", ROOT / "to_human" / "index.html"
A22 = "snowflake-arctic-embed:22m"

rows = [json.loads(l) for l in (DATA / "results.jsonl").open(encoding="utf-8") if l.strip()]
last = {}  # latest row per (ranker, tag/suite, model)
for r in rows:
    last[(r.get("ranker"), r.get("tag") or r.get("suite") or "", r.get("model", ""))] = r


def table(head, body, note=""):
    th = "".join(f"<th>{html.escape(h)}</th>" for h in head)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in body)
    return f"<table><tr>{th}</tr>{tr}</table>" + (f"<p class=note>{note}</p>" if note else "")


def bars(items, lo, hi, unit=""):
    """horizontal bars as inline svg; items = [(label, value, highlight)]."""
    h, out = 26, []
    for i, (label, v, hl) in enumerate(items):
        w = max(2, 520 * (v - lo) / (hi - lo))
        out.append(f'<text x="0" y="{i * h + 17}" class=l>{html.escape(label)}</text>'
                   f'<rect x="230" y="{i * h + 4}" width="{w:.0f}" height="18" class="{"hl" if hl else "b"}"/>'
                   f'<text x="{236 + w:.0f}" y="{i * h + 17}" class=v>{v:.2f}{unit}</text>')
    return f'<svg width="820" height="{len(items) * h + 6}">{"".join(out)}</svg>'


sections = []

# 1. memscore trajectory (arctic 22m, all three suites)
traj = [("0.2 dense", "dense", ""), ("0.3 shipped", "time_interp", "E3"), ("E3 episode", "episode", "E3"),
        ("E3.1 + diversity floor", "episode_div", "E3.1"), ("E3.2 episode, lift-only", "episode_boost", "E3.2"),
        ("E4 episode + neighbours", "episode_nb", "E3")]
items = []
for label, rk, tag in traj:
    r = last.get((rk, tag, "snowflake-arctic-embed:22m"))
    if rk == "dense":
        r = next((x for x in reversed(rows) if x.get("ranker") == "dense" and "memscore" in x and x.get("model") == A22
                  and not x.get("limit") and all(s in x for s in ("locomo-obs", "locomo-turns", "lme-s"))), None)
    if r and "memscore" in r:
        items.append((label, r["memscore"], rk == "episode_boost"))
if items:
    sections.append("<h2>retrieval: memscore (mean R@5 of locomo-obs, locomo-turns, lme-s)</h2>" + bars(items, 55, 72))

# 2. EdgeMem table
lme = [(r["ranker"], r) for (rk, tag, m), r in last.items() if tag == "lme-all-470" and m == A22]
if lme:
    body = [["<b>EdgeMem</b> (paper)", "81.49", "90.49", "93.83", "92.80", ""], ["MemGAS (paper)", "78.51", "86.83", "94.47", "89.96", ""]]
    body += [[n, f"{r['R_all@3']:.2f}", f"{r['NDCG@3']:.2f}", f"{r['R_all@10']:.2f}", f"{r['NDCG@10']:.2f}", f"{r['p50_ms']:.1f} ms"] for n, r in lme]
    sections.append("<h2>LongMemEval-S retrieval vs EdgeMem (470 Qs, all turns, no LLM)</h2>" +
                    table(["system", "R@3", "NDCG@3", "R@10", "NDCG@10", "p50"], body,
                          "ours: strict recall_all (every gold session in the top k). EdgeMem/MemGAS rows from EdgeMem's Table 4."))

# 3. embedders (stage A: locomo only)
emb = sorted(((m, r) for (rk, tag, m), r in last.items() if tag in ("E2A",) and rk == "time_interp"), key=lambda x: -x[1]["memscore"])
dense = {m: r["memscore"] for (rk, tag, m), r in last.items() if tag == "E2A" and rk == "dense"}
if emb:
    two = lambda r: statistics.mean([r["locomo-obs"]["main"], r["locomo-turns"]["main"]])
    d22 = next(x for x in reversed(rows) if x.get("ranker") == "dense" and x.get("model") == A22 and "locomo-turns" in x and not x.get("limit"))
    base = two(last[("time_interp", "E3", "snowflake-arctic-embed:22m")])
    body = [[m, f"{r['memscore']:.2f}", f"{r['memscore'] - base:+.2f}", f"{dense.get(m, float('nan')):.2f}"] for m, r in emb]
    body.append(["snowflake-arctic-embed:22m (default)", f"{base:.2f}", "0", f"{two(d22):.2f}"])
    sections.append("<h2>embedders, stage A (LoCoMo suites only, 0.3 recipe)</h2>" +
                    table(["model", "hybrid memscore (2 suites)", "vs default", "dense only"], body))

# 4. QA
qa = []
for f in sorted(DATA.glob("qa_*.jsonl")):
    rs = [json.loads(l) for l in f.open(encoding="utf-8") if l.strip()]
    good = [r for r in rs if "error" not in r]
    if not good:
        continue
    by = defaultdict(list)
    for r in good:
        by[r["type"]].append(r["ok"])
    qa.append([f.stem[3:], str(len(good)), f"{100 * statistics.mean(r['ok'] for r in good):.1f}",
               " · ".join(f"{k.replace('single-session-', 'ss-')} {100 * statistics.mean(v):.0f}" for k, v in sorted(by.items()))])
if qa:
    sections.append("<h2>LongMemEval-S QA (reader nemotron-3-ultra no-think, judge minimax-m3.1-flash)</h2>" +
                    table(["config", "n answered", "accuracy %", "by type"], qa,
                          "partial runs show what has been answered so far. published (GPT-4o judge): supermemory 85.2, EdgeMem 60.0 (gpt-4o-mini reader)."))

findings = (ROOT / "findings.md").read_text(encoding="utf-8")
page = f"""<!doctype html><meta charset=utf-8><title>qenlo-memory research</title>
<style>body{{font:15px/1.5 system-ui,Segoe UI,sans-serif;max-width:900px;margin:32px auto;padding:0 16px;color:#1d1d1f}}
h1{{font-size:24px}}h2{{font-size:18px;margin-top:36px}}table{{border-collapse:collapse;margin:8px 0}}
td,th{{padding:4px 10px;border-bottom:1px solid #ddd;text-align:left}}th{{background:#f4f4f5}}
.note{{color:#666;font-size:13px}}.l{{font-size:13px}}.v{{font-size:12px;fill:#444}}.b{{fill:#9ca3af}}.hl{{fill:#2563eb}}
pre{{white-space:pre-wrap;background:#f8f8f8;padding:12px;font-size:13px}}</style>
<h1>qenlo-memory: toward the best local, LLM-free agent memory</h1>
<p class=note>generated {time.strftime('%Y-%m-%d %H:%M')} from research/data. every protocol was committed before its results.</p>
{''.join(sections)}
<h2>findings.md</h2><pre>{html.escape(findings)}</pre>"""
OUT.parent.mkdir(exist_ok=True)
OUT.write_text(page, encoding="utf-8")
print(OUT)
