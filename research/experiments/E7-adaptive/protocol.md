# E7 protocol: adaptive readout, the final run (locked 2026-11-15)

why (akshat): qenlo-memory is a cross-session memory, so multi-session is the category that matters. E1 showed
multi-session at 11/33 with 10 turns while the right sessions are retrieved ~99% of the time: counting and aggregation
questions need every mention, which turns cannot hold. E6's first answers (6/13 and 7/15) point the same way.

- rule (lexical, no model, never sees the question type): `AGGREGATE` in qa.py ("how many", "in total", "how often",
  "all the", "list", "every", "number of", ...). match -> the 5 best sessions whole (`sessions`, ranked by their best
  turn under the 0.3 recipe); no match -> the top 30 turns. LongMemEval's own CoT reader prompt for both.
  coverage on LME-S: 113/133 multi-session, 44/78 knowledge-update, 56/133 temporal, 24/156 single-session.
- run: `adaptive:time_interp:5 --topk 30 --cot`, all 500 questions, both NIM keys. reader/judge per E1 amendment 5;
  then the 50-item cross-family judge check (step-5-preview).
- E6 arms are stopped to free the keys and reported as partial.
- prediction: multi-session >= 60% (from 33%), overall >= 85%.
- the same rule ships in the product as "counting questions get more memories back" only if this run beats 0.3's
  79.5% (195-q subset) on its own 195 questions.
