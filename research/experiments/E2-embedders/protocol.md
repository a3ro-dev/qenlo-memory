# E2 protocol: the embedder table (locked 2026-11-15, before any result)

why: qenlo-memory lets users pick any of 12 ollama embedders (models.py LIBRARY), but nobody has measured them
on memory retrieval. users get a table to choose from; we learn whether the 22M default is the right default.

- rankers: `time_interp` (the 0.3 shipped recipe) and `dense`, through research/src/bench.py, unchanged.
- models: all 12 in LIBRARY, each with its own query/doc prefixes from models.py.
- stage A: every model on locomo-obs + locomo-turns (8.4k texts per model, cheap).
- stage B: lme-s for the default and the stage-A Pareto set (memscore vs size), since lme-s means ~123k
  embeddings per model.
- also measured per model: query-embed latency p50 (single query, warm, 50 queries), docs/s when embedding,
  ollama-reported size. GPU: RTX 4050 laptop 6 GB.
- decision rule: the default only changes if a model of <= ~70 MB beats arctic-22m by >= 1.0 memscore with no suite
  down > 1.0 (the footprint is the product). heavier models that sit on the Pareto front become documented opt-in
  choices; README numbers stay on the default.
- prediction: hybrid shrinks the gap between embedders (bm25 rescues weak ones); the 335m-1.2GB models gain
  2-6 memscore over 22m with hybrid, more with dense alone; no tiny model beats 22m by 1 point.
