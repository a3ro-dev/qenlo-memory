# E3/E4 analysis (2026-11-15, confirmatory, locked protocol 674a616)

| ranker | locomo-obs R@5 | locomo-turns R@5 | lme-s R_all@5 | memscore |
| --- | ---: | ---: | ---: | ---: |
| 0.3 `time_interp` | 58.9 | 57.6 | 87.7 | 68.07 |
| E3 `episode` | 59.0 | 63.6 | 87.9 | **70.16** |
| E4 `episode_nb` | 54.2 | 61.9 | 87.9 | 68.02 |
| E4 `time_interp_nb` | 53.5 | 57.5 | 87.7 | 66.26 |

- E3 passes the memscore rule (+2.09, no suite down). prediction held: big on locomo-turns (+6.0), ~0 on obs (+0.1),
  small on lme (+0.2; recall_any@10 98.1 vs 99.0).
- it is not uniform. locomo-turns by category: single-hop c4 66.1 -> 73.8, adversarial c5 57.1 -> 69.1, temporal c2
  67.6 -> 70.2, but multi-hop c1 30.9 -> 27.7. obs: c1 36.5 -> 31.1, c3 31.0 -> 24.0. lme preference 93.3 -> 86.7
  (2 of 30 questions). reading: the session score pulls the ranking toward the one session that matches as a whole,
  which helps questions answered inside one session and hurts questions whose evidence is spread across sessions.
  that is the cost EdgeMem pays down with its diversity floor (K=3 distinct episodes) and the co-occurrence channel.
- E4 (neighbour readout) loses turn-level R@5 everywhere, as predicted: neighbours eat slots. its value is for the
  reader (context around a hit); judged in QA, not here.
- latency: episode 0.9-3.8 ms per query (session fts table), still CLI-instant.
- next: E3 QA gate; E3.1 = episode + diversity floor (>= 3 distinct sessions in the top k) to recover multi-hop.

## E3.1 diversity floor (confirmatory, protocol d9e3964): refuted

`episode_div` memscore 68.87 (obs 58.7, turns 60.0, lme 87.9) vs E3 70.16. multi-hop not recovered (c1 obs 32.7,
turns 28.4; 0.3 had 36.5 / 30.9). so the c1 loss is not one session crowding the top 5. better reading: multi-hop
evidence lives in sessions that are mostly about something else, so their *session* bm25 is low and the 0.5 session
weight demotes the gold turn itself. that predicts a fix through a lower session weight or a session score that
only boosts (max(turn, mix)) rather than replaces. not tuned on the test set yet: next protocol if E3 passes QA.

## EdgeMem's LongMemEval-S table (confirmatory, lme_retrieval.py, 470 questions, all turns, no LLM)

| ranker | R_all@3 | NDCG@3 | R_all@10 | NDCG@10 | R_any@3 | p50 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| EdgeMem (paper Table 4, "R") | 81.49 | 90.49 | 93.83 | 92.80 | | |
| MemGAS (paper Table 4) | 78.51 | 86.83 | 94.47 | 89.96 | | |
| dense (0.2) | 77.87 | 87.87 | 93.19 | 90.77 | 95.32 | 0.5 ms |
| time_interp (0.3 shipped) | **84.47** | 91.89 | **95.32** | 93.79 | 96.60 | 1.2 ms |
| episode (E3) | 83.40 | **92.06** | 94.26 | **94.15** | 96.81 | 15 ms |

- 0.3 beats EdgeMem's retrieval on all four reported numbers, with our strict recall_all; under recall_any the gap
  is wider (96.6 at k=3). EdgeMem's "R" is not defined beyond "Recall"; we report both so neither reading flatters us.
- dataset: our data/raw/longmemeval_s.json is byte-identical (sha256 d6f21ea9...c3a442) to longmemeval_s_cleaned.json in
  huggingface.co/datasets/xiaowu0162/longmemeval-cleaned, the cleaned release EdgeMem reports using. same data.
  ranking unit differs: EdgeMem ranks turns into an evidence pack; we rank sessions by their best turn.
- E3 at session level: NDCG up, R_all down ~1 (spread-evidence questions again). episode_div is identical at the
  session level by construction (it only reorders turns within already-seen sessions).
- episode costs 15 ms here (sessions with long assistant turns in the session fts table) vs 1.2 ms.

## E3.2 episode_boost (confirmatory, protocol b890519): best memscore, mechanism half-confirmed

memscore 70.53 (obs 60.4, turns 63.6, lme-s 87.7): +2.46 over 0.3, +0.37 over E3. predictions: memscore >= 69.07 yes;
turns keeps >= +3 yes (+6.0); c1 within 1 of 0.3: turns 30.1 vs 30.9 yes, obs 34.4 vs 36.5 no; LME-470 R_all@3 >= 83.97
no (82.98; NDCG@10 93.75 vs 0.3's 93.79). lme-s preference back to 93.3 (E3 had 86.7).
reading: the max form removes most of the demotion (preference fully, turns c1 mostly) but on LongMemEval's
all-turns haystacks any session prior still costs ~1.5 strict session recall at k=3. the episode family trades LME
session recall for LoCoMo turn recall. QA decides; a memory that ships must not lose LongMemEval.
