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
   GPT-4o plays in published setups), judge minimax-m3.1-flash at first, nemotron-3.5-lightning-30b for the all-500 run (results.jsonl records the judge per run). one key sustains ~13-22 calls/min; reasoning models
   silently spend the answer budget; the harness rejects substituted models and reasoning replies.

6. **final numbers (0.4, published).** LongMemEval-S retrieval with the shipped 33m default beats EdgeMem on all four
   of its reported numbers (R@3 82.77, NDCG@3 91.44, R@10 94.89, NDCG@10 93.81 vs 81.49 / 90.49 / 93.83 / 92.80).
   answer accuracy on all 500: 71.6% (EdgeMem 60.0 with gpt-4o-mini; supermemory ~85 with gpt-4o; not same-reader).
7. **more context is not automatically better.** whole sessions + LongMemEval's CoT prompt lost to 10 plain turns on the
   shared 195 questions (71.3 vs 79.5); multi-session did not improve (8 vs 11 of 33). the readout, not retrieval
   (session recall_any@10 96-99%), is the open problem, and the next round should separate prompt, readout and
   embedder, which E7 changed together.
8. **batching several questions into one reader call is unsafe:** the reader swapped answers between neighbouring tasks.

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

## 0.5: conflicts, races, review (2026-11-17)

8. **the contradiction check has to be slot-aligned or it's noise.** on the 49 near-duplicate pairs `review` finds in
   my real store (a stand-in for "same subject"), "any differing number or negation" flagged 37, nearly all false
   (dates, hashes, run counts in long notes). requiring the same content word before a different value, values of the
   same shape, and negation only in notes of 30 words or less: 1 flag, borderline. recall on real contradictions is
   not measured; synthetic cases (port, retry count, deployed vs not merged) are in tests/test_smoke.py.
9. **concurrency was "design yes, battle-tested no", and the test proved why.** 8 threads x 25 writes on the store the
   daemon serves failed at once in 0.4: sqlite3 cursors interleaved on the shared connection ("bad parameter or other
   API misuse"). fixed by running and fetching each statement under a lock. after the fix: 200 notes + a CAS race on
   one key, sqlite = fts5 = qenlo live rows, every CAS winner in one unbroken history chain, clean reopen, 3/3 runs.
10. **0.5 does not change recall ranking**, so the 0.4 retrieval and QA numbers stand. the state labels (current,
   possibly stale, tentative) are what A-TMA (arXiv:2607.01935) found helps the reader on conflict-heavy questions; not
   yet measured here.
11. **100 concurrent agents (research/src/load.py, 2026-11-17).** real daemon, real ollama (arctic-embed:33m, RTX
   4050 laptop), store seeded with a copy of my 293 real memories. each agent runs sessions: context, 3 recalls, a
   note (20% tentative), 40% a read-then-`--expect` write on 10 shared hot keys, 20% a `--replaces` race on the same
   seeded memory, 15% a diff `check`, 5% a forget.
   - burst (<=0.2 s between calls), 3,259 calls: 0 failures. 0.5 as first built: 48.9 calls/s, p50 ~1.9 s. ollama runs
     embed requests one at a time (100 single embeds 2.29 s vs one batch of 100 0.86 s), so the Embedder now pools
     concurrent calls into one request: 70 calls/s, p50 ~1.25 s. without embedding the store itself does ~1,000
     recalls/s from 100 threads, so ollama is the ceiling (~115 embeds/s here), not qenlo or sqlite.
   - realistic pace (<=5 s between calls, still faster than an LLM turn), 1,315 calls: 0 failures, recall p50 197 ms
     / p95 924 ms, remember p50 172 ms; the p95 tail is all 100 agents starting a session at the same moment.
   - races: 97 + 6 key writes and 12 replaces lost and were refused (409), none overwrote. after every run: sqlite =
     fts5 = qenlo live rows, no key with two current values, no forked chain, clean restart; daemon ~76 MB.
12. **100 simultaneous writers.** two more 0.4 limits. (a) `ThreadingHTTPServer`'s listen backlog is 5: with 100 agents
   released at once by a barrier, 71/1,000 writes got WinError 10054 (connection reset). backlog 1,024: 1,000/1,000, 3/3
   runs. (b) a write cost ~7.5 ms of syncs (qenlo add_batch 2.5 ms for its own WAL file, sqlite rollback-journal
   commit 2.4 ms, a WAL-directory glob 0.9 ms). group commit (one sqlite transaction with a savepoint per writer, one
   qenlo batch per group, a WAL counter instead of the glob) + sqlite WAL mode: store-only, 100 threads x 10 writes,
   109 -> 1,865 writes/s, p50 867 -> 53 ms, qenlo WAL files 232 -> 21. through the daemon the write burst runs at ~75/s,
   which is ollama's embed ceiling here. ollama batch scaling: 1 text 14/s, 32 texts 45/s, 256 texts 101/s, so pooling
   helps but can't pass ~100/s on this laptop.
