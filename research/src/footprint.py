"""what qenlo-memory costs on a real store: RSS, latency with live ollama embeddings, disk. usage: footprint.py <store dir>"""

import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from qenlo_memory.store import Embedder, Store  # noqa: E402

me = psutil.Process()
rss = lambda: me.memory_info().rss / 2**20
base = rss()
work = Path(tempfile.mkdtemp())
shutil.copy(Path(sys.argv[1]) / "memories.db", work)
t = time.perf_counter()
s = Store(work, Embedder())  # no vectors copied: rebuilds them, like a fresh machine importing a store
print(f"open + rebuild {s.stats()['memories']} memories: {time.perf_counter() - t:.2f}s, rss {rss():.0f} MB (python alone {base:.0f} MB)")
queries = [m["text"][:60] for m in s.recent(100)]
lat = []
for q in queries:
    t = time.perf_counter()
    s.recall(q, k=8)
    lat.append(1000 * (time.perf_counter() - t))
print(f"recall, live embedding: p50 {statistics.median(lat):.1f} ms, p95 {statistics.quantiles(lat, n=20)[18]:.1f} ms")
lat = []
for i in range(50):
    t = time.perf_counter()
    s.remember(f"footprint probe {i}: the build cache lives in target/{i}", "semantic", "bench")
    lat.append(1000 * (time.perf_counter() - t))
print(f"remember (embed + sqlite + qenlo WAL + related check): p50 {statistics.median(lat):.1f} ms, p95 {statistics.quantiles(lat, n=20)[18]:.1f} ms")
n = s.stats()["memories"]
print(f"rss after: {rss():.0f} MB, backend {s.backend}, search ran on {s.search_backend}")
s.close()
size = lambda p: sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 2**20
print(f"disk: {size(work):.2f} MB for {n} memories")
