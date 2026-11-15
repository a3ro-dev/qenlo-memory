# E1 protocol: end-to-end QA harness + 0.3 baseline (locked 2026-11-15, approved by akshat)

why: memscore measures retrieval only. published memory systems (supermemory 85.2-85.4, mastra 94.87,
agentmemory 96.2, contextstream 96.8 on LongMemEval-S) report judged answer accuracy. we need that number,
with the LLM only in the benchmark, never in qenlo-memory.

- suite: LongMemEval-S, all 500 questions (incl. 30 abstention, incl. single-session-assistant).
- ingest: every turn (user and assistant) is one memory, tagged with its session date. same Store recall
  code, embedder (snowflake-arctic-embed:22m) and qenlo build as 0.3.
- read: recall top-10 turns -> reader LLM with LongMemEval's reader prompt (dates shown, "current date" = the
  question date), temperature 0.
- judge: LongMemEval's official evaluate_qa.py prompts, verbatim, per question type incl. abstention.
- llm: NVIDIA NIM free API (OpenAI-compatible), one pinned model for the reader and one for the judge; the
  model id is logged on every call. a failed or substituted call is retried, never counted.
- configs: (a) dense-only (0.2), (b) 0.3 hybrid, (c) oracle: only the gold sessions in context (ceiling).
- report: accuracy overall and per type, next to published numbers labelled "different reader/judge".
- gate for later experiments: the memscore rule (>= +1.0, no suite < -1.0) and QA must not drop by > 1 point.
- prediction: (b) beats (a) by 3-8 points; (b) stays well below (c) on multi-session and temporal-reasoning.
