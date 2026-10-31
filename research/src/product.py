"""the shipped Store, not the bench's rankers: parity on locomo-obs, and the H5 update suite.

usage: python -I research/src/product.py parity|updates
"""

from __future__ import annotations

import json
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / "src")]

from bench import Cache, DATA  # noqa: E402
from suites import locomo  # noqa: E402

from qenlo_memory.store import Store  # noqa: E402


class StoreEmbed(Cache):
    """the bench cache with the attributes Store expects from an Embedder."""

    model = property(lambda self: self.spec.name)
    dim = 384
    device = "cached"


def parity():
    embed, r5, lat = StoreEmbed(), [], []
    for h in locomo("obs"):
        with tempfile.TemporaryDirectory() as tmp:
            s = Store(Path(tmp), embed)
            s.remember_many([{"text": t, "created": c} for _, t, c in h.docs])
            # ids line up: remember_many inserts in order into an empty store. duplicates would shift them
            texts = {m["id"]: m["text"] for m in s.export()}
            unit = {i: h.unit[d] for i, t in texts.items() for d, dt, _ in [h.docs[i - 1]] if dt == t}
            for q in h.queries:
                t = time.perf_counter()
                hits = s.recall(q.text, k=5, now=q.when)
                lat.append(time.perf_counter() - t)
                covered = {u for m in hits for u in unit.get(m["id"], ())}
                r5.append(len(q.gold & covered) / len(q.gold))
            s.close()
    print(f"product recall on locomo-obs: R@5 {100 * statistics.mean(r5):.2f}  p50 {1000 * statistics.median(lat):.2f} ms (incl. cached embed)")
    return 100 * statistics.mean(r5)


SLOTS = [  # (subject question, statement template, old value, new value)
    ("which port does the staging database listen on", "the staging database listens on port {}", "5432", "6543"),
    ("what node version does the web app need", "the web app needs node {}", "18", "22"),
    ("which branch do we release from", "releases are cut from the {} branch", "main", "release"),
    ("what is the api rate limit", "the public api rate limit is {} requests per minute", "60", "120"),
    ("who reviews database migrations", "database migrations are reviewed by {}", "priya", "marco"),
    ("which embedding model is the default", "the default embedding model is {}", "all-minilm", "snowflake-arctic-embed:22m"),
    ("where are the nightly backups stored", "nightly backups are stored in {}", "s3://old-backups", "r2://backups-eu"),
    ("what python version does the sdk support", "the sdk supports python {} and newer", "3.9", "3.10"),
    ("which test runner does the repo use", "the repo's tests run with {}", "nose", "pytest"),
    ("how often does the cron job run", "the cleanup cron job runs every {}", "hour", "15 minutes"),
    ("what is the user's preferred editor", "the user's preferred editor is {}", "vim", "zed"),
    ("which cloud region hosts production", "production runs in {}", "us-east-1", "ap-south-1"),
    ("what package manager does the frontend use", "the frontend uses {} for packages", "npm", "pnpm"),
    ("what is the max upload size", "the max upload size is {}", "10 MB", "50 MB"),
    ("which font does the brand use", "the brand typeface is {}", "Inter", "IBM Plex Sans"),
    ("what is the support email", "support requests go to {}", "help@old.dev", "support@qenlo.dev"),
    ("how long do sessions stay logged in", "login sessions expire after {}", "1 day", "7 days"),
    ("which ci provider runs the builds", "builds run on {}", "travis", "github actions"),
    ("what is the default branch protection", "the default branch requires {} approvals", "1", "2"),
    ("what timezone are standups in", "standups are scheduled in {}", "UTC", "IST"),
]


def updates():
    """TEPA-style: every slot gets an old value, distractors, then a new value. is the old one still on top?"""
    embed, rng = StoreEmbed(), random.Random(7)
    distract = [f"unrelated note {i}: {w}" for i, w in enumerate(rng.choices(
        ["refactored the parser", "the logo is red", "lunch at noon", "docs moved to the wiki", "the laptop has 16GB",
         "ci takes 6 minutes", "the pet sleeps at night", "rust toolchain is pinned", "qenlo uses avx2"], k=200))]
    out = {}
    for mode in ("append-only", "supersede"):
        with tempfile.TemporaryDirectory() as tmp:
            s = Store(Path(tmp), embed)
            old = {i: s.remember(tpl.format(a), "semantic", "codex")["id"] for i, (_, tpl, a, _) in enumerate(SLOTS)}
            s.remember_many([{"text": t} for t in distract])
            caught = 0
            for i, (_, tpl, _, b) in enumerate(SLOTS):
                r = s.remember(tpl.format(b), "semantic", "claude-code")
                caught += old[i] in [m["id"] for m in r["related"]]
                if mode == "supersede":
                    s.remember(tpl.format(b), "semantic", "claude-code", replaces=old[i])  # what the skill tells agents to do
            top = [s.recall(q, k=1)[0]["text"] for q, *_ in SLOTS]
            stale = [t == tpl.format(a) for t, (_, tpl, a, _) in zip(top, SLOTS)]
            fresh = [t == tpl.format(b) for t, (_, tpl, _, b) in zip(top, SLOTS)]
            out[mode] = {"fresh@1": sum(fresh) / len(SLOTS), "stale@1": sum(stale) / len(SLOTS),
                         "old shown as related": caught / len(SLOTS)}
            s.close()
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    res = {"parity": parity, "updates": updates}[sys.argv[1]]()
    with (DATA / "results.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ranker": "product-" + sys.argv[1], "result": res, "at": time.strftime("%Y-%m-%d %H:%M")}) + "\n")
