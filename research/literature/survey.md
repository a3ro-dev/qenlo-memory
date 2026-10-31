# literature survey: long-term memory for agents (2026-10-31)

what we need from the literature: ideas that work **without an LLM in the memory process** (qenlo-memory's
writers are the agents themselves, already LLMs) and that a local, single-user store can run in milliseconds.

## benchmarks we use

- **LoCoMo** (Maharana et al. 2024, arXiv:2402.17753). 10 very long conversations, 5,882 turns, 1,986 QA with
  evidence turn ids, 5 categories (1 multi-hop, 2 temporal, 3 open-domain, 4 single-hop, 5 adversarial).
  ships LLM-extracted per-session `observation` facts with evidence ids: the closest public proxy for
  "atomic facts an agent wrote down". https://github.com/snap-research/locomo
- **LongMemEval** (Wu et al. 2024, ICLR 2025, arXiv:2410.10813). 500 questions, each with its own ~50-session
  haystack (LongMemEval-S, cleaned). types: single-session user/assistant/preference, multi-session,
  temporal-reasoning, knowledge-update, plus 30 abstention. retrieval metric: recall_any / recall_all of
  evidence sessions at k. their findings we test: round-level values beat sessions, *time-aware query
  expansion* (filter the search range from time words in the question) helps temporal questions, fact-augmented
  keys help. https://github.com/xiaowu0162/LongMemEval

## systems and the idea we take from each

| work | idea | LLM-free? | taken |
| --- | --- | --- | --- |
| Mem0 (Chhikara et al. 2025, arXiv:2504.19413) | on write, fetch similar memories, decide ADD / UPDATE / DELETE / NOOP | decision needs an LLM: the calling agent is that LLM | yes: `remember` returns similar memories, `--replaces` |
| Zep / Graphiti (Rasmussen et al. 2025, arXiv:2501.13956) | bi-temporal facts: invalidate, never delete | yes | yes: `superseded_by`, `valid_until`, history kept |
| Engram (arXiv:2606.09900) | lossless writes, contradictions invalidate not delete, hybrid dense+lexical+recency read path, lean context beats full history | yes for the read path | yes: hybrid read path |
| TEPA (arXiv:2608.07429) | revocation: append-only and last-write-wins both fall *below no memory* after a reversal; keyed revocation 0.95 | yes | yes: superseded memories leave the default search |
| MemLACE (arXiv:2609.03201) | sparse merge / supersession / contradiction relations over atomic text memories; lifecycle expansion is the main gain | yes | partly: supersession chain shown with the hit |
| MOSAIC (arXiv:2607.16211) | conflict detection at save time against neighbours | yes (neighbour lookup) | yes: possible conflicts printed on save |
| AgentIR (arXiv:2605.25092) | on LoCoMo BM25 alone is the strongest single retriever; RRF of BM25 + dense | yes | tested (H1) |
| EngramRAG (arXiv:2609.32049) | dense + BM25 + graph via RRF: LoCoMo R@5 38.3% (dense) → 53.2% | yes | tested (H1) |
| Cognis (arXiv:2604.19771) | BM25 + vectors via RRF, temporal boosting, version tracking on ingest | yes (minus reranker) | tested |
| The Fusion Tax (arXiv:2610.30612) | RRF hurts when dense is clearly stronger; dense-weighted interpolation is safer | yes | tested (H2) |
| Generative Agents (Park et al. 2023) | score = relevance + recency + importance | yes | tested (H4) |
| ScrubJay-MEM (arXiv:2608.04746), Fortunate Recall (arXiv:2609.10413) | type-conditioned decay; slot-key supersession carries most of the gain | yes | decay tested per kind |
| A-MEM (arXiv:2502.12110) | zettelkasten links between notes | needs an LLM | no |
| MemGPT / Letta | tiers: core memory always in context, archival searched | yes | already have it (long_term tier) |
| memorywire (arXiv:2606.01138) | RRF is robust to rank-0 injection where max-fusion collapses | yes | supports RRF over max |

## what the field agrees on, in one paragraph

flat dense retrieval is the weak baseline everywhere. the cheap wins are (1) adding a lexical channel and
fusing ranks, (2) making validity explicit so stale facts stop being retrieved, and (3) using time in the
query. graph construction, reflection and agentic multi-step retrieval give more, but they need an LLM in the
loop, and in qenlo-memory the agent calling us already *is* that loop: we should return good candidates fast
and let it decide.
