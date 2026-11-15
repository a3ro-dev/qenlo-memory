# E5 protocol: how much context to hand the reader (locked 2026-11-15)

disclosed motivation: partial E1 counts (not results) showed 0.3 at top-10 turns answering 11/33 multi-session
questions while session recall@10 is ~99%. so the evidence is found but not handed over: multi-session questions
("how many times...", "in total...") need every mention, and 10 turns (~1k tokens) cannot hold them. EdgeMem hands
its reader up to 18 items + neighbours (~3.2k tokens); LongMemEval's own RAG baselines hand whole sessions.

in the product the agent picks `-k`, so the budget is the agent's choice; what we learn sets the default `k` and what
the skill tells agents to do for counting/aggregation questions. the memory stays LLM-free either way.

- harness: E1 (amendment 5), stratified 200 questions, `ranker:time_interp` (0.3 recipe) unless noted.
- arms: k=10 (baseline, already running), k=20 (~EdgeMem's token budget), k=30, `time_interp_nb` k=30 (each hit with
  its neighbours), `sessions:time_interp:3` (three best sessions, whole).
- measured: accuracy overall and per type, prompt size (chars), paired wins/losses against k=10.
- prediction: multi-session rises with k (k=30 >= k=10 + 15 points); overall k=30 >= k=10 + 5; whole sessions best
  on multi-session but no better overall than k=30 at ~2.5x the tokens; single-session types flat (+-1 question).
