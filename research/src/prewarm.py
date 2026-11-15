"""embed every LongMemEval-S turn (user and assistant) into the bench cache, so qa.py's retrieval is cache-only."""
import json, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / "src")]
from product import StoreEmbed  # noqa: E402
from suites import RAW  # noqa: E402
from qenlo_memory.store import redact  # noqa: E402

texts = list(dict.fromkeys(redact(t["content"].strip()) for q in json.loads((RAW / "longmemeval_s.json").read_text(encoding="utf-8"))
                           for s in q["haystack_sessions"] for t in s if t["content"].strip()))
for a in sys.argv[1:]:  # --shard i/n: this worker takes every n-th text, starting at i
    if a.startswith("--shard="):
        i, n = map(int, a.split("=")[1].split("/"))
        texts = texts[i::n]
if "--reverse" in sys.argv:  # a second worker from the other end; both share the cache
    texts.reverse()
embed, t0 = StoreEmbed(), time.time()
print(len(texts), "unique turns", flush=True)
for i in range(0, len(texts), 2000):
    embed(texts[i:i + 2000])
    print(f"{i + 2000}/{len(texts)}  {time.time() - t0:.0f}s", flush=True)
