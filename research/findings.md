# findings

## 0.4 round (2026-11-15, in progress): the best local, LLM-free memory

goal set by akshat: best in the niche (local, no LLM in the memory process, tiny, fast). the bar is EdgeMem
(arXiv:2609.05553), the strongest published LLM-free system; the stretch is supermemory's ~85% published QA.

### what we know so far

1. **we already clear the LLM-free bar on retrieval.** on EdgeMem's own LongMemEval-S table (470 questions, all turns,
   no LLM), the shipped 0.3 recipe gets R@3 84.47 / NDCG@3 91.89 / R@10 95.32 / NDCG@10 93.79 against EdgeMem's
   81.49 / 90.49 / 93.83 / 92.80, using the stricter reading of recall (every gold session in the top k). dense
   alone (0.2) would have lost (77.87 R@3). the lexical channel is what carried us past it.
2. **session context helps turn retrieval and costs session recall.** EdgeMem's episode score (turn + whole-session
   bm25) lifts LoCoMo raw-turn R@5 57.6 -> 63.6 and memscore 68.07 -> 70.16, but drops multi-hop and loses ~1-1.5
   strict session recall on LongMemEval. letting the session score only lift a turn (E3.2, max form) gives the best
   memscore, 70.53, and recovers LongMemEval preference, but not LME session recall. a diversity floor (E3.1) does
   nothing: the loss is demotion, not crowding.
3. **neighbour readout costs recall@5** (neighbours take slots); its value, if any, is for the reader. QA pending.
4. **with hybrid recall, embedder size flattens out.** arctic 22m / 33m / 110m / 335m: 58.3 / 61.1 / 61.7 / 61.9 on
   the LoCoMo suites (dense alone spreads them over ~8 points). the 67 MB 33m model captures most of what 15x larger
   ones add; it is the candidate default (stage B pending). all-minilm and granite-30m sit below the default.
5. **the eval itself:** NIM free tier, reader nemotron-3-ultra with thinking off (a big non-reasoning reader, the role
   GPT-4o plays in published setups), judge minimax-m3.1-flash. one key sustains ~13-22 calls/min; reasoning models
   silently spend the answer budget; the harness rejects substituted models and reasoning replies.

### lessons (0.4)

- reasoning models hide their tokens inside max_tokens: probe with a realistic prompt, not a yes/no.
- a shared backoff across threads turns a few 429s into minutes of silence; back off per thread, log every retry.
- at 100 s per call the run is latency-bound; workers, not the rate limit, set the pace.
- `endswith("22m")` matches all-minilm:22m. match model names exactly.

---

# 0.3 round (2026-10-31)

## what we know

qenlo-memory 0.2 recalled with dense search alone (snowflake-arctic-embed 22M, exact search in qenlo). on three
locked suites that's memscore 59.8. 0.3 recalls with three channels and scores ~68.0, at ~1 ms per recall
(excluding the query embedding), with no new dependency: sqlite FTS5 was already in the standard library, and the
time filter runs inside qenlo.

| ranker | locomo-obs R@5 | locomo-turns R@5 | lme-s recall_all@5 | memscore |
| --- | ---: | ---: | ---: | ---: |
| dense (0.2) | 51.3 | 45.1 | 83.1 | 59.8 |
| bm25 alone | 53.5 | 53.2 | 80.4 | 62.4 |
| hybrid, RRF k=60 | 54.0 | 52.3 | 88.6 | 65.0 |
| hybrid, min-max score interpolation 50/50 | 58.5 | 57.4 | 87.7 | 67.9 |
| + bm25 confidence weighting | 58.2 | 57.7 | 87.9 | 67.9 |
| + time-window channel (= 0.3, `time_interp`) | 58.9 | 57.6 | 87.7 | 68.1 |
| 0.3 as shipped, through `Store.recall` | 58.7 | | | |

the update test (20 facts that change, 200 distractors, the real Store):

| | new value on top | stale value on top | old version flagged as related on save |
| --- | ---: | ---: | ---: |
| append-only (0.2 behaviour) | 40% | **60%** | |
| `--replaces` (0.3) | **100%** | 0% | 100% |

## patterns

1. **a small embedding model needs a lexical channel.** 22M-parameter embeddings blur names, numbers and versions.
   bm25 alone matches dense on LoCoMo (the AgentIR result) and loses on LongMemEval; fused, they beat both on
   every suite. the two channels fail on different queries, which is exactly when fusion pays.
2. **score interpolation beat RRF here** (+3 to +5 points on LoCoMo, -1 on LongMemEval). RRF throws away how
   *much* better the top hit is. lower RRF k (10) recovered part of the gap, consistent with that reading.
   dense-heavy weights (0.65, 0.8) lost on LoCoMo, lexical-heavy (0.35, 0.2) lost on LongMemEval; 0.5 is the
   robust middle (the "fusion tax" paper's warning holds in both directions).
3. **min-max normalisation is fragile in small stores.** fts5 floors idf at ~1e-6 for words in most rows, and
   min-max stretches that noise to 1.0. a 3-memory store ranked "the user prefers lower-case prose" above the
   gpu fact for "what graphics card does the user have". scaling bm25 by its best score *and* trusting it only up
   to an absolute score of 1 fixes that with no cost on the big suites (67.94 vs 67.96).
4. **time words are rare but decisive.** 8% of LoCoMo and 13% of LongMemEval questions name a time. on those,
   a dense search restricted to the window (qenlo filters timestamps natively) adds +6.1 / +3.9 R@5 on LoCoMo,
   and nothing on LongMemEval, whose time questions are aggregates ("how many hours last month").
5. **recency is not a ranking signal, validity is.** a recency bonus lost 1-4 points on LoCoMo (H4 refuted),
   while explicit supersession took stale answers from 60% to 0%. what changed matters; what's new doesn't.
6. **append-only memory is worse than a coin flip after an update** (60% stale). TEPA reported the same shape
   (append-only below no-memory). the agent is already the LLM that can decide "this updates #12", so the store's
   job is to show it the candidates (cosine ≥ 0.82, measured on the user's 243 real memories) and to honour the link.

## lessons and constraints

- ollama embeds at 45-100 texts/s on the laptop GPU (sequential per request); the bench caches by model+text,
  so only the first pass costs (~35 min for LongMemEval-S user turns).
- ollama 400s a whole batch if one text overflows the context; embed one by one, truncating, on error.
- don't run `uv sync` while a process holds qenlo's dll on windows: the venv ends up half-upgraded.
- the time parser must not fire on "how many weeks ago did i..." (asks for a duration, names no time).

## decided against

- no generative model and no cross-encoder reranker in the memory process. both would buy recall points at the cost
  of the thing that makes this usable: a few-ms, tens-of-MB memory with one small embedding model.
- instead, the structuring an LLM would do at write time is done by the agent writing the memory, which already is
  one: `--key` (slot-key supersession, the part of Fortunate Recall / TEPA that carried most of their gain) and
  `--replaces`. with keys alone the update suite went 60% stale -> 0% stale.

## real store (the user's 243 memories, read-only copy)

- migration from 0.2: schema columns added and fts index built from sqlite in 0.5 s, no re-embedding.
- 10 natural questions about the user's projects: old dense recall already had the right #1 on 9; 0.3 fixed the
  10th and cleaned up slot 3 on a few. at this size, ranking is not the pain; stale duplicates are.
- `review` found 31 groups of same-subject memories, e.g. a release called "not merged to prod" and later
  "deployed to prod". that is the append-only failure H5 measured, sitting in a real store.

## open questions
- LongMemEval multi-session (84%) and temporal (80%) are the weakest categories; both want aggregation across
  sessions, which is a reading problem more than a retrieval one.
