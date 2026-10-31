"""head to head on locomo-obs: mem0 OSS (infer=False, its LLM-free path) vs qenlo-memory's Store.

same embedding model in the same ollama, same facts, same questions, each system's own add/search code.
run each side in its own venv:
  <mem0 venv>/python -I research/src/vs_mem0.py mem0
  uv run --with psutil python -I research/src/vs_mem0.py qenlo
"""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

import psutil

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / "src")]
from suites import locomo  # noqa: E402

MODEL = "snowflake-arctic-embed:22m"
me = psutil.Process()
rss = lambda: me.memory_info().rss / 2**20


def mem0_side():
    from mem0 import Memory

    def make(tmp):
        return Memory.from_config({
            "vector_store": {"provider": "qdrant", "config": {"collection_name": "locomo", "path": tmp, "embedding_model_dims": 384}},
            "embedder": {"provider": "ollama", "config": {"model": MODEL}},
            "llm": {"provider": "ollama", "config": {"model": "unused-with-infer-false"}},
            "history_db_path": str(Path(tmp) / "history.db"),
        })

    def run(h, tmp):
        m = make(tmp)
        t = time.perf_counter()
        for d, text, _ in h.docs:
            m.add(text, user_id="u", metadata={"doc": d}, infer=False)
        add = (time.perf_counter() - t) / len(h.docs)
        hits = []
        for q in h.queries:
            t = time.perf_counter()
            res = m.search(q.text, filters={"user_id": "u"}, top_k=5)["results"]
            hits.append(([r["metadata"]["doc"] for r in res], time.perf_counter() - t))
        return add, hits

    return run


def qenlo_side():
    from qenlo_memory.store import Embedder, Store

    embed = Embedder()

    def run(h, tmp):
        s = Store(Path(tmp), embed)
        t = time.perf_counter()
        for d, text, c in h.docs:  # one remember per fact, like mem0's add loop
            s.remember(text, "semantic", "bench")
        add = (time.perf_counter() - t) / len(h.docs)
        doc_of = {m["id"]: d for m, (d, _, _) in zip(sorted(s.export(), key=lambda m: m["id"]), h.docs)}
        hits = []
        for q in h.queries:
            t = time.perf_counter()
            res = s.recall(q.text, k=5, now=q.when)
            hits.append(([doc_of[m["id"]] for m in res], time.perf_counter() - t))
        s.close()
        return add, hits

    return run


def main(side: str):
    base = rss()
    run = {"mem0": mem0_side, "qenlo": qenlo_side}[side]()
    r5, lat, adds, peak = [], [], [], 0.0
    for h in locomo("obs"):
        with tempfile.TemporaryDirectory() as tmp:
            add, hits = run(h, tmp)
            adds.append(add)
            for q, (docs, sec) in zip(h.queries, hits):
                covered = {u for d in docs for u in h.unit[d]}
                r5.append(len(q.gold & covered) / len(q.gold))
                lat.append(1000 * sec)
            peak = max(peak, rss())
    out = {"side": side, "R@5": round(100 * statistics.mean(r5), 2), "search_p50_ms": round(statistics.median(lat), 1),
           "search_p95_ms": round(statistics.quantiles(lat, n=20)[18], 1), "add_mean_ms": round(1000 * statistics.mean(adds), 1),
           "rss_python_mb": round(base), "rss_peak_mb": round(peak)}
    print(json.dumps(out))
    with (HERE.parents[0] / "data" / "results.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ranker": f"vs-mem0:{side}", **out, "at": time.strftime("%Y-%m-%d %H:%M")}) + "\n")


if __name__ == "__main__":
    main(sys.argv[1])
