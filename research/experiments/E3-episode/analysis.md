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
