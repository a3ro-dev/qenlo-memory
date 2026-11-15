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
