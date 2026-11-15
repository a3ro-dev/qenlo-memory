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

## revision 1 (2026-11-15, before the run; akshat: no hand-written rules, everything model-driven; batch the API)

- the lexical trigger is dropped. readout is score-adaptive: sessions ranked by their best turn under the 0.3 fused
  model (dense embedding + bm25 + time); the reader gets every session whose best turn scores >= 0.8 x the best
  session, at least 2 and at most 6, each whole. constants fixed now, not tuned. config `dynamic`.
- batching (NIM limits requests, not items): 8 questions per reader request, each a fenced task with its own full,
  verbatim LongMemEval CoT prompt and its own history; 25 answers per judge request, each with its own verbatim
  per-type judge prompt. an answer or verdict the reply does not cover falls back to a single official call.
  batching can only make the reader's job harder (longer input), so it cannot inflate the score.
- run: `dynamic --cot --batch-read 8 --batch-judge 25`, all 500 questions, both keys.
- embedder for this run: snowflake-arctic-embed:33m, the shipped 0.4 default (E2). earlier QA runs used 22m.
