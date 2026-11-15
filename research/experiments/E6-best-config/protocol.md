# E6 protocol: the strongest LLM-free configuration (locked 2026-11-15)

goal (akshat): publishable SOTA numbers for local, LLM-free memory. the memory stays LLM-free; what changes is what
it hands the reader and how the reader is asked, both standard practice in the published systems we compare with.

- reader prompt: LongMemEval's own chain-of-thought template (`--cot`, run_generation.py, verified verbatim), 800-token
  budget as in their code. the judge sees only the reader's answer text, as in the official evaluate_qa.py.
- arms (stratified 200 first, the winner then on all 500):
  A `ranker:time_interp --topk 30 --cot`
  B `sessions:time_interp:3 --cot` (three best sessions, whole; what LongMemEval's RAG baselines hand the reader)
- comparators: E1/E5 arms without CoT; published numbers labelled with their reader/judge.
- prediction: both beat k=10 plain by >= 10 points overall; B best on multi-session; the winner on 500 clears
  EdgeMem's 60.0 by >= 10 points.
- all the arms ever run are reported, not only the winner.
