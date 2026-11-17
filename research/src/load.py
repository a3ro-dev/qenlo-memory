"""load test: N concurrent agents doing agent-shaped sessions against a real daemon (real ollama embeddings).

    QENLO_MEMORY_HOME=<scratch> QENLO_MEMORY_PORT=7498 uv run python research/src/load.py --seed seed.jsonl --agents 100

a session is what an agent does with memory in one task: session-start context, a few recalls, one or two new notes,
sometimes a key update with --expect racing other agents on a few hot keys, sometimes a --replaces race on the same
memory, sometimes a diff check, now and then a forget. 409s are expected (lost races); anything else is a failure.
never point it at your real home: it writes and forgets.
"""

import argparse
import json
import multiprocessing as mp
import os
import random
import re
import sqlite3
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

HOME = Path(os.environ["QENLO_MEMORY_HOME"])
URL = f"http://127.0.0.1:{os.environ.get('QENLO_MEMORY_PORT', '7437')}"
PROJECTS = ["api", "web", "infra", "mobile", "ml"]
HOT_KEYS = [f"{p}.{k}" for p in ("staging", "prod") for k in ("db.port", "release.branch", "owner", "region", "cache.ttl")]


def call(op, token, **body):
    req = urllib.request.Request(f"{URL}/{op}", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + token})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            out, code = json.load(r), 200
    except urllib.error.HTTPError as e:
        out, code = json.load(e), e.code
    except OSError as e:  # refused or reset: a failure to count, not a crashed agent
        out, code = {"error": repr(e)}, 0
    return out, code, (time.perf_counter() - t) * 1000


def agent(n, seed, token, sessions, think, log, writes=0, gate=None):
    rnd = random.Random(n)
    name = f"agent-{n:03d}"

    def op(label, path, **body):
        out, code, ms = call(path, token, **body)
        log.append((label, code, ms, "" if code in (200, 409) else str(out)[:200]))
        return out if code == 200 else None

    if writes:  # write burst: every agent released at the same instant, then writes back to back
        gate.wait()
        for i in range(writes):
            key = rnd.choice(HOT_KEYS) if rnd.random() < 0.3 else ''
            op('burst-cas' if key else 'burst-write', 'remember', agent=name, project='shared' if key else rnd.choice(PROJECTS),
               text=f'{name} write {i}: ' + (f'{key} is {rnd.randrange(10_000)}' if key else rnd.choice(seed)['text'][:200]),
               key=key, expect=None)
        return
    for s in range(sessions):
        project = rnd.choice(PROJECTS)
        op("context", "context", project=project, query=rnd.choice(seed)["text"][:80])
        for _ in range(3):
            words = rnd.choice(seed)["text"].split()
            i = rnd.randrange(max(1, len(words) - 8))
            op("recall", "recall", query=" ".join(words[i:i + 8]), project=rnd.choice(["", project]), k=8)
            time.sleep(rnd.random() * think)
        op("remember", "remember", text=f"{name} session {s}: in {project}, {rnd.choice(seed)['text'][:160]} "
                                         f"(verified with build {rnd.randrange(10_000)})",
           kind=rnd.choice(["episodic", "semantic", "procedural"]), agent=name, project=project,
           tentative=rnd.random() < 0.2)
        if rnd.random() < 0.4:  # read a hot key, then write it back with compare-and-swap
            key = rnd.choice(HOT_KEYS)
            cur = op("recall-key", "recall", query=key, project="shared", k=1) or []
            have = next((m["id"] for m in cur if m.get("key") == key), 0)
            val = {"port": rnd.randrange(5000, 9000), "branch": rnd.choice(["main", "dev", "trunk"]), "ttl": rnd.randrange(30, 900)}
            op("remember-cas", "remember", text=f"{key.replace('.', ' ')} is now {val[key.split('.')[-1]] if key.split('.')[-1] in val else rnd.randrange(100)}",
               agent=name, project="shared", key=key, expect=have)
        if rnd.random() < 0.2:  # several agents updating the same seeded memory at once
            target = rnd.choice(seed[:20])
            cur = op("recall-old", "recall", query=target["text"][:80], k=3) or []
            if cur:
                op("replace", "remember", text=re.sub(r"\d+", lambda m: str(int(m[0]) + 1), cur[0]["text"], count=1) + f" (updated by {name})",
                   agent=name, project=cur[0]["project"], replaces=cur[0]["id"])
        if rnd.random() < 0.15:
            lines = [re.sub(r"\d+", lambda m: str(int(m[0]) + 7), rnd.choice(seed)["text"][:200]) for _ in range(5)]
            op("check", "check", text="\n".join("+ " + l for l in lines), project=project)
        if rnd.random() < 0.05:
            mine = op("recent-own", "recent", n=5, agent=name) or []
            if mine:
                op("forget", "forget", id=mine[-1]["id"])
        time.sleep(rnd.random() * think)


def worker(ids, seed, token, sessions, think, q, writes, gate):
    log = []
    threads = [threading.Thread(target=agent, args=(n, seed, token, sessions, think, log, writes, gate)) for n in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    q.put(log)


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * p))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", required=True)
    ap.add_argument("--agents", type=int, default=100)
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--sessions", type=int, default=5)
    ap.add_argument("--writes", type=int, default=0, help="write burst: each agent writes this many memories at once, nothing else")
    ap.add_argument("--think", type=float, default=0.2, help="max seconds between calls")
    a = ap.parse_args()
    seed = [json.loads(l) for l in open(a.seed, encoding="utf-8") if l.strip()]
    seed = [m for m in seed if not m.get("superseded_by")]
    token = (HOME / "token").read_text().strip()
    before = call("stats", token)[0]

    q, gate = mp.Queue(), mp.Barrier(a.agents)
    chunks = [list(range(a.agents))[i::a.procs] for i in range(a.procs)]
    procs = [mp.Process(target=worker, args=(c, seed, token, a.sessions, a.think, q, a.writes, gate)) for c in chunks]
    t0 = time.time()
    for p in procs:
        p.start()
    log = [x for _ in procs for x in q.get()]
    for p in procs:
        p.join()
    wall = time.time() - t0
    after = call("stats", token)[0]

    by = defaultdict(list)
    for kind, code, ms, _ in log:
        by[kind].append(ms)
    codes = Counter((k, c) for k, c, _, _ in log)
    fails = [x for x in log if x[3]]
    print(f"{a.agents} agents x {a.sessions} sessions, think <= {a.think}s: {len(log)} calls in {wall:.1f}s "
          f"({len(log) / wall:.1f} calls/s), {len(fails)} failures")
    print(f"{'op':<14}{'n':>6}{'p50 ms':>9}{'p95 ms':>9}{'p99 ms':>9}{'max ms':>9}  codes")
    for k in sorted(by):
        v = by[k]
        cs = " ".join(f"{c}x{n}" for (kk, c), n in sorted(codes.items()) if kk == k)
        print(f"{k:<14}{len(v):>6}{statistics.median(v):>9.0f}{pct(v, .95):>9.0f}{pct(v, .99):>9.0f}{max(v):>9.0f}  {cs}")
    for f in fails[:10]:
        print("FAIL", f)
    print("memories", before["memories"], "->", after["memories"], "| superseded", before["superseded"], "->", after["superseded"],
          "| pending", after.get("pending"))
    json.dump({"agents": a.agents, "sessions": a.sessions, "think": a.think, "calls": len(log), "wall_s": round(wall, 1),
               "failures": len(fails), "by_op": {k: {"n": len(v), "p50": round(statistics.median(v)), "p95": round(pct(v, .95)),
                                                     "p99": round(pct(v, .99))} for k, v in by.items()},
               "codes": {f"{k}:{c}": n for (k, c), n in codes.items()}}, open(HOME / "load-result.json", "w"))


def invariants():
    """run after the daemon is stopped: the three indexes agree, no key has two current values, chains don't fork."""
    db = sqlite3.connect(HOME / "memories.db")
    live = db.execute("SELECT COUNT(*) FROM memories WHERE superseded_by IS NULL").fetchone()[0]
    words = db.execute("SELECT COUNT(*) FROM words").fetchone()[0]
    dup_keys = db.execute("SELECT key, project, COUNT(*) FROM memories WHERE key IS NOT NULL AND superseded_by IS NULL "
                          "GROUP BY key, project HAVING COUNT(*) > 1").fetchall()
    forks = db.execute("SELECT superseded_by, COUNT(*) FROM memories WHERE superseded_by IS NOT NULL "
                       "GROUP BY superseded_by HAVING COUNT(*) > 1").fetchall()
    dangling = db.execute("SELECT COUNT(*) FROM memories m WHERE superseded_by IS NOT NULL AND NOT EXISTS "
                          "(SELECT 1 FROM memories n WHERE n.id = m.superseded_by)").fetchone()[0]
    print(f"sqlite live {live}, fts rows {words}, keys with 2+ current values {dup_keys}, "
          f"memories replacing 2+ others {len(forks)}, replaced-by pointing nowhere {dangling}")
    return live, words, dup_keys, dangling


if __name__ == "__main__":
    invariants() if sys.argv[1:] == ["--invariants"] else main()
