"""retrieval bench: every ranker sees the same stores, the same cached embeddings, the same metrics.

usage: python -I research/src/bench.py <ranker> [<ranker> ...] [--suites a,b] [--limit N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import sys
import time
from array import array
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / "src")]

from qenlo import Collection, Filter, Record  # noqa: E402

import rankers  # noqa: E402
from suites import SUITES  # noqa: E402

DATA = HERE.parents[0] / "data"


class Cache:
    """embeddings keyed by model + prefixed text, so every experiment after the first is free."""

    def __init__(self, model=None):
        from qenlo_memory import models

        self.spec = model or models.active()
        self.db = sqlite3.connect(DATA / "embcache.sqlite")
        self.db.execute("CREATE TABLE IF NOT EXISTS e(k BLOB PRIMARY KEY, v BLOB)")

    def key(self, text: str, query: bool) -> bytes:
        prefix = self.spec.query if query else self.spec.doc
        return hashlib.sha1(f"{self.spec.name}\0{prefix}{text}".encode()).digest()

    def __call__(self, texts: list[str], query: bool = False) -> list[list[float]]:
        from qenlo_memory.store import ollama

        keys = [self.key(t, query) for t in texts]
        have = {}
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            have.update(self.db.execute(f"SELECT k, v FROM e WHERE k IN ({','.join('?' * len(chunk))})", chunk).fetchall())
        missing = list({k: t for k, t in zip(keys, texts) if k not in have}.items())
        prefix = self.spec.query if query else self.spec.doc
        for i in range(0, len(missing), 128):
            part = missing[i:i + 128]
            vecs = ollama("/api/embed", {"model": self.spec.name, "input": [prefix + t for _, t in part], "keep_alive": -1, "truncate": True})["embeddings"]
            rows = [(k, array("f", v).tobytes()) for (k, _), v in zip(part, vecs)]
            self.db.executemany("INSERT OR REPLACE INTO e VALUES (?, ?)", rows)
            have.update(rows)
        self.db.commit()
        return [array("f", have[k]).tolist() for k in keys]


class Index:
    """one store: qenlo for vectors (timestamp = created), sqlite FTS5 for words."""

    def __init__(self, h, embed: Cache):
        self.h, self.embed = h, embed
        self.created = {i: c for i, _, c in h.docs}
        self.text = {i: t for i, t, _ in h.docs}
        vectors = embed([t for _, t, _ in h.docs])
        self.dim = len(vectors[0])
        self.vec = dict(zip((i for i, _, _ in h.docs), vectors))
        self.col = Collection.memory(dimension=self.dim)
        self.col.add_batch([Record(i, 0, c, v) for (i, _, c), v in zip(h.docs, vectors)])
        self.fts = sqlite3.connect(":memory:")
        self.fts.execute("CREATE VIRTUAL TABLE f USING fts5(text, tokenize='porter unicode61')")
        self.fts.executemany("INSERT INTO f(rowid, text) VALUES (?, ?)", [(i, t) for i, t, _ in h.docs])

    def dense(self, qv, k=50, lo=None, hi=None):
        r = self.col.search(qv, Filter(timestamp_lower=lo, timestamp_upper=hi), k=k)
        return [(x.id, 1 - x.distance) for x in r.results]

    def lexical(self, query: str, k=50):
        match = rankers.fts_query(query)
        if not match:
            return []
        rows = self.fts.execute("SELECT rowid, bm25(f) FROM f WHERE f MATCH ? ORDER BY bm25(f) LIMIT ?", (match, k)).fetchall()
        return [(i, -s) for i, s in rows]

    def close(self):
        self.col.close()
        self.fts.close()


def score_locomo(ranked, idx, q, k):
    covered = {u for d in ranked[:k] for u in idx.h.unit[d]}
    return len(q.gold & covered) / len(q.gold)


def sessions(ranked, idx):
    seen = []
    for d in ranked:
        for u in idx.h.unit[d]:
            if u not in seen:
                seen.append(u)
    return seen


def run(ranker: str, suite: str, limit: int, embed: Cache) -> dict:
    fn = getattr(rankers, ranker.replace("-", "_"))
    per_cat, main, second, lat = {}, [], [], []
    for h in SUITES[suite]()[:limit or None]:
        idx = Index(h, embed)
        qvs = embed([q.text for q in h.queries], query=True)
        for q, qv in zip(h.queries, qvs):
            t = time.perf_counter()
            ranked = fn(idx, q, qv)
            lat.append(time.perf_counter() - t)
            if suite == "lme-s":
                s = sessions(ranked, idx)
                a, b = float(q.gold <= set(s[:5])), float(bool(q.gold & set(s[:10])))
            else:
                a, b = score_locomo(ranked, idx, q, 5), score_locomo(ranked, idx, q, 10)
            main.append(a)
            second.append(b)
            per_cat.setdefault(q.category, []).append(a)
        idx.close()
    return {
        "main": 100 * statistics.mean(main), "second": 100 * statistics.mean(second), "n": len(main),
        "p50_ms": 1000 * statistics.median(lat),
        "cats": {c: round(100 * statistics.mean(v), 1) for c, v in sorted(per_cat.items())},
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("rankers", nargs="+")
    p.add_argument("--suites", default=",".join(SUITES))
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--tag", default="")
    a = p.parse_args()
    embed = Cache()
    out = DATA / "results.jsonl"
    for r in a.rankers:
        res = {s: run(r, s, a.limit, embed) for s in a.suites.split(",")}
        memscore = statistics.mean(v["main"] for v in res.values())
        row = {"ranker": r, "tag": a.tag, "model": embed.spec.name, "limit": a.limit, "memscore": round(memscore, 2),
               **{s: {k: round(v, 2) if isinstance(v, float) else v for k, v in x.items()} for s, x in res.items()},
               "at": time.strftime("%Y-%m-%d %H:%M")}
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(f"{r:<28} memscore {memscore:6.2f}  " + "  ".join(f"{s} {x['main']:.1f}/{x['second']:.1f} ({x['p50_ms']:.2f}ms)" for s, x in res.items()), flush=True)
        for s, x in res.items():
            print(f"    {s}: {x['cats']}", flush=True)


if __name__ == "__main__":
    main()
