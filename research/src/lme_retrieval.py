"""LongMemEval-S session-level retrieval, the way EdgeMem's Table 4 reports it: all 470 non-abstention questions,
every turn (user and assistant) indexed, sessions ranked by their best turn. no LLM anywhere.

usage: python -I research/src/lme_retrieval.py <ranker> [<ranker> ...] [--limit N]
metrics: recall_any / recall_all / ndcg_any at k = 3, 5, 10 (LongMemEval's definitions; ndcg with binary gain).
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / "src")]

import rankers  # noqa: E402
from bench import DATA, Cache, Index, sessions  # noqa: E402
from suites import RAW, Haystack, Query, ms  # noqa: E402

from qenlo_memory.store import redact  # noqa: E402


def lme_all_one(q: dict) -> Haystack:
    """one question's haystack, every turn; h.role maps doc id -> 'user' | 'assistant'."""
    h = Haystack(q["question_id"], [], {})
    h.role = {}
    for sid, date, session in zip(q["haystack_session_ids"], q["haystack_dates"], q["haystack_sessions"]):
        when = ms(datetime.strptime(date, "%Y/%m/%d (%a) %H:%M"))
        for i, t in enumerate(session):
            if t["content"].strip():  # the text the Store would keep, so the prewarmed cache hits
                h.docs.append((len(h.docs) + 1, redact(t["content"].strip()), when + i * 1000))
                h.unit[len(h.docs)] = (sid,)
                h.role[len(h.docs)] = t["role"]
    when = ms(datetime.strptime(q["question_date"], "%Y/%m/%d (%a) %H:%M"))
    h.queries.append(Query(q["question"], set(q["answer_session_ids"]), q["question_type"], when))
    return h


def lme_all() -> list[Haystack]:
    return [lme_all_one(q) for q in json.loads((RAW / "longmemeval_s.json").read_text(encoding="utf-8"))
            if not q["question_id"].endswith("_abs")]


def ndcg(ranked: list, gold: set, k: int) -> float:
    dcg = sum(1 / math.log2(i + 2) for i, s in enumerate(ranked[:k]) if s in gold)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(gold), k)))
    return dcg / ideal if ideal else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rankers", nargs="+")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    embed, hs = Cache(), lme_all()[:a.limit or None]
    for r in a.rankers:
        fn, m, per, lat = getattr(rankers, r.replace("-", "_")), {}, {}, []
        for h in hs:
            idx = Index(h, embed)
            q = h.queries[0]
            qv = embed([q.text], query=True)[0]
            t = time.perf_counter()
            s = sessions(fn(idx, q, qv), idx)
            lat.append(time.perf_counter() - t)
            for k in (3, 5, 10):
                top = set(s[:k])
                for name, v in ((f"R_any@{k}", float(bool(q.gold & top))), (f"R_all@{k}", float(q.gold <= top)),
                                (f"NDCG@{k}", ndcg(s, q.gold, k))):
                    m.setdefault(name, []).append(v)
            per.setdefault(q.category, []).append(float(q.gold <= set(s[:5])))
            idx.close()
        row = {"ranker": r, "suite": "lme-all-470", "model": embed.spec.name, "n": len(hs),
               **{k: round(100 * statistics.mean(v), 2) for k, v in m.items()},
               "R_all@5_by_type": {c: round(100 * statistics.mean(v), 1) for c, v in sorted(per.items())},
               "p50_ms": round(1000 * statistics.median(lat), 2), "at": time.strftime("%Y-%m-%d %H:%M")}
        print(json.dumps(row), flush=True)
        with (DATA / "results.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()
