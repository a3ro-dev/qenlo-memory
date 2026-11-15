# E3/E4 protocol: episode context and neighbour readout, from EdgeMem (locked 2026-11-15, before full runs)

source: EdgeMem (arXiv:2609.05553), the strongest LLM-free system we know of. its largest single gain (+8.44 strict
judge on LoCoMo) is scoring a turn together with its session and reading each hit out with its neighbours.
order changed from the original plan (embedders first) because of that ablation; E2 still runs, on the GPU, in parallel.

- E3 `episode`: rho(t) = 0.5 * (0.3 fused turn score, max-normalised) + 0.5 * (fts5 bm25 of the turn's whole
  session, max-normalised); the turns of the top-3 sessions join the candidate pool. lambda = 0.5 and top-3 are
  EdgeMem's / fixed in advance, not tuned.
- E4 `episode_nb` / `time_interp_nb`: each ranked turn followed by its predecessor and successor in the same session.
- suites: the locked memscore suites (locomo-obs, locomo-turns, lme-s), plus EdgeMem's LongMemEval-S table
  (`lme_retrieval.py`: 470 questions, all turns, session R/NDCG @3/@10).
- QA: the winner gets an E1 QA run (same reader/judge) against 0.3.
- decision: memscore rule (>= +1.0, no suite < -1.0) and QA not down > 1.
- disclosed: a 2-conversation locomo smoke test was run to check the code before this lock (episode 61.4 vs
  time_interp 57.9 memscore on those 2); it is not a result.
- prediction: episode gains on locomo-turns and lme (sessions are the gold unit there), ~0 on locomo-obs (observations
  are already self-contained facts); neighbour readout costs turn-level R@5 slots on locomo-obs but helps QA.
- for the product: a "session" for agent-written memories = same agent + project within a short gap (to be designed
  only if E3 wins).

## E3.1 (locked 2026-11-15, after E3's result, before running it)

hypothesis: E3 loses multi-hop because the session score fills the top with one session. EdgeMem's diversity floor
(K=3 distinct episodes) should recover it. `episode_div`: walk the episode ranking; a turn from an already-seen
session may take one of the first 5 slots only while enough slots remain to still reach 3 distinct sessions.
K=3 and the 5-slot window fixed in advance. prediction: c1 (multi-hop) back to >= 0.3's level, memscore >= E3 - 0.5.
QA gate: ranker:time_interp (0.3) vs ranker:episode / episode_div / episode_nb on the E1 harness.

## E3.2 (locked 2026-11-15, after E3.1, before running it)

hypothesis: E3's multi-hop loss is demotion: a gold turn in a session that is mostly about something else gets a
low session score, and the 0.5/0.5 mix pulls it under turns from well-matching sessions. if the session score can
only lift a turn, never sink it, the loss should go. `episode_boost`: rho = max(t, 0.5*t + 0.5*s), same t, s,
candidates as E3. one variant, no sweep.
prediction: c1 back to >= 0.3's (obs 36.5, turns 30.9) within 1 point; memscore >= 0.3 + 1.0; locomo-turns keeps
>= +3 of E3's +6.0; LME-470 R_all@3 >= 0.3's 84.47 - 0.5.
