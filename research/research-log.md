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
