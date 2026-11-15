# research log

- 2026-10-31 bootstrap: surveyed ~60 papers (literature/survey.md), downloaded LoCoMo + LongMemEval-S, locked protocol.
- H1: hybrid beats dense on all three suites; bm25 alone ties dense on LoCoMo.
- H2: min-max interpolation 0.5 best overall; RRF k=60 weakest of the hybrids on LoCoMo.
- found the small-store min-max failure through a unit test; bm25 confidence weighting fixes it at no cost.
- H3: rare trigger (8-13% of queries); clear gain on LoCoMo's, none on LongMemEval's aggregate questions.
- H4 refuted. H5: append-only 60% stale, supersede 0%.
- parity: shipped Store.recall reproduces the bench recipe (58.7 R@5 on locomo-obs). concluded for 0.3.
- 2026-11-15 E1 harness built (NIM reader nemotron-ultra no-think, gemma-4 judge after two amendments: minimax reasons past its budget). smoke 5/6.
- read EdgeMem (arXiv:2609.05553), the LLM-free bar: episode context + neighbour readout is their biggest gain. reordered: E3 before E2.
- E3 episode: memscore 68.07 -> 70.16 (locomo-turns +6.0), multi-hop and lme preference down. E4 neighbour readout costs R@5 (expected). E3 goes to the QA gate.
- E3.1 diversity floor refuted (68.87 < 70.16, c1 not recovered): the multi-hop loss is session-score demotion, not crowding.
- LME-S 470 (EdgeMem's table, no LLM): 0.3 R_all@3 84.47 / NDCG@3 91.89 / R_all@10 95.32 / NDCG@10 93.79 vs EdgeMem 81.49/90.49/93.83/92.80. we already beat the LLM-free bar on retrieval. E3: NDCG up, R_all down 1.
- E3.2 episode_boost: memscore 70.53 (best), lme preference recovered, LME-470 R_all@3 82.98 (-1.5 vs 0.3). episode family trades LME session recall for LoCoMo turn recall; QA gate decides.
- E2 stage A (locomo only, time_interp): arctic-33m 61.05 vs 22m 58.25 (+2.8); all-minilm 56.18, granite-30m 55.82. dense alone: 33m 52.56, minilm 40.72, granite 40.86 (bm25 rescues the weak ones, as predicted).
- 2026-11-15 LME-S file = HF longmemeval-cleaned (sha256 match). judge minimax saturated on NIM (1/15 burst ok, both keys): amendment 5, lightning-30b judge (15/15, 1.6 s), step-5 cross-family check. QA restarted, reader answers cached.
- E2 stage B: arctic-33m memscore 70.10 vs 68.07 (every suite up). passes the default-change rule pending latency/RAM.
- E2 shipped: arctic-33m default (70.10 vs 68.07 memscore; p50 query embed 40.5 vs 39.4 ms; VRAM 58 vs 37 MB; parity 59.70).
- E1 0.3 top-10 plain on the 195-q subset: 79.5% (multi-session 11/33, temporal 23/33). oracle partial 82.7% (185 q), stopped for time.
- akshat: qenlo-memory is a cross-session memory, so multi-session is the category. E6 (k=30 CoT vs 3 sessions CoT) stopped
  at ~46 q each (82.6 / 79.2 partial) for the final run. E5 not run.
- E7 final: score-adaptive whole-session readout (sessions within 0.8x of the best, 2-6), LongMemEval CoT reader, batched
  requests (8 questions per reader call, 25 per judge call), 33m embedder, all 500 questions. no lexical rules (akshat).
- infra: ollama restarted with OLLAMA_NUM_PARALLEL=8 (GPU 33% -> ~75%), 6 prewarm shards, windows ultimate performance plan.
- decision models (Jev, Parallel decisions-fast, Laya) looked up and rejected for the memory path: cloud, 250-600 ms, weak on
  counting and dates by their own docs. Laya is local but is a reranker by another name.
- E7 final (500 q, unbatched): 71.6% (ms 48.9, tr 62.4). worse than 0.3 top-10 plain on the shared 195 (71.3 vs 79.5).
  batched run invalid (answers swapped between tasks). published 71.6 with the subset caveat. 33m LME-470 table beats EdgeMem on all four.
