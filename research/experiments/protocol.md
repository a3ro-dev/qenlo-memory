# protocol (locked before any result, 2026-10-31)

## question

which LLM-free changes to qenlo-memory's read and write paths make the right memory come back, and stale
memories stop coming back, at the latency of a CLI call?

## evaluation (fixed before running)

three retrieval suites, each a per-conversation store, embeddings from the shipped default model
(`snowflake-arctic-embed:22m` in ollama), exact search in qenlo:

1. **locomo-obs**: LoCoMo observations (agent-style atomic facts, 2,541 in total). query = question, gold = facts whose evidence id is in
   the question's evidence. metric R@5 (fraction of gold evidence ids covered in the top 5).
2. **locomo-turns**: LoCoMo raw turns (5,882), gold = evidence turns. R@5.
3. **lme-s**: LongMemEval-S, the 470 non-abstention questions, minus the 56 single-session-assistant
   ones (we index user turns only, like a memory of what the user said). gold = answer sessions.
   recall_all@5 at the session level (every evidence session appears in the top 5 sessions, ranked by best turn).

**primary metric `memscore` = mean of the three.** secondary: R@10 / recall_any@10, per-category numbers,
p50 query latency.

decision rule: a change ships if memscore improves by >= 1.0 point and no suite drops by > 1.0 point,
or if it is a correctness feature (supersession) that passes its own test without costing memscore.

## hypotheses

- **H1 hybrid**: BM25 (sqlite FTS5) fused with dense by reciprocal rank fusion (k=60) beats dense alone, because
  memories are full of names, paths, versions and numbers that 22M-parameter embeddings blur. predict
  +3 to +10 points memscore, largest on locomo-turns.
- **H2 fusion form**: per the "fusion tax", a dense-weighted fusion beats equal RRF when dense is the
  stronger channel. predict weighted ≥ equal RRF on lme-s.
- **H3 time-aware query**: dates and relative time words in the question ("last saturday", "in may 2023",
  "two weeks ago") become a soft time window on `created`. predict a gain on LongMemEval temporal-reasoning
  and LoCoMo category 2, ~0 elsewhere.
- **H4 recency prior**: a small recency bonus on fused scores helps knowledge-update (newest value wins)
  and hurts little elsewhere. predict + on lme knowledge-update, -0.5 to +0.5 overall.
- **H5 supersession**: an explicit `replaces` link hides the old memory from default recall. tested
  with a deterministic update suite (old value must not outrank the new one) rather than memscore.
