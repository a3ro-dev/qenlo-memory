# EdgeMem (Cui et al., arXiv:2609.05553, 2026-09) — our direct competitor: LLM-free memory construction + retrieval

https://arxiv.org/abs/2609.05553 . spaCy 3.8 en_core_web_sm + rank_bm25 + dateparser. **no embeddings at all.**
every source turn stored verbatim; three hyperedge families over turns:
- time: calendar-month hubs; co-occurrence: one hub per entity/keyword (lemmatized content words, NER spans);
  episode: one hub per native session.
retrieval (k=14, kappa=18 items, B=3,072 tokens):
1. episode channel: rho(t) = 0.5*bm25_turn(q, x_t) + 0.5*bm25_session(q, X_s(t)), each max-normalized; rank turns,
   diversity floor K=3 distinct episodes (5 for enumerative questions, fixed lexical trigger); **each seed read out
   with its predecessor and successor turn**; stop at k.
2. co-occurrence channel: query keys with df in the rare band [2,10]; up to 4 unseen turns incident to those hubs,
   ranked by rho.
3. time channel: only when the query has an explicit month + 4-digit year; month hubs; 2 turns.
evidence pack = dedup(episode || co-occ || time) <= 18 items, date-sorted? (rendered with speaker + session date).
results: LoCoMo strict judge 61.01 (gpt-4o-mini reader, temp 0, 128 output tokens) vs CompassMem 58.70, CAM 54.94.
anchor ladder (LoCoMo SJ): flat turn bm25 49.74 -> +episode 58.18 (+8.44) -> +co-occ 60.43 (+2.21) -> +time 61.01.
LongMemEval-S (n=470): session R@3 81.49, NDCG@3 90.49, R@10 93.83, NDCG@10 92.80; SJ 60.00 (MemGAS 60.20),
3,202 tokens/question. P95 retrieval 10.73 ms CPU.
for us: the episode (session-context) score and +-1 neighbour readout are the largest single gain; we already have
dense + bm25 + time. their LoCoMo/LME numbers are the bar for "best LLM-free memory".
