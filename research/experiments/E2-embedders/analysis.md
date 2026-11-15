# E2 analysis

## stage A (confirmatory, LoCoMo suites only, 0.3 recipe `time_interp` and `dense`; 2026-11-15)

hybrid memscore = mean(locomo-obs R@5, locomo-turns R@5). default arctic-22m = 58.25 (dense 48.22).

| model | size | hybrid | dense |
| --- | --- | ---: | ---: |
| embeddinggemma | 622 MB | 64.25 | 59.48 |
| snowflake-arctic-embed2 | 1.2 GB | 63.14 | 57.21 |
| nomic-embed-text | 274 MB | 62.34 | 54.60 |
| snowflake-arctic-embed:335m | 669 MB | 61.93 | 55.86 |
| mxbai-embed-large | 670 MB | 61.85 | 52.13 |
| snowflake-arctic-embed:110m | 219 MB | 61.73 | 56.43 |
| snowflake-arctic-embed:33m | 67 MB | 61.05 | 52.56 |
| qwen3-embedding:0.6b | 639 MB | 59.87 | 48.31 |
| bge-m3 | 1.2 GB | 58.42 | 45.66 |
| snowflake-arctic-embed:22m (default) | 46 MB | 58.25 | 48.22 |
| all-minilm:22m | 46 MB | 56.18 | 40.72 |
| granite-embedding:30m | 63 MB | 55.82 | 40.86 |

- prediction "hybrid shrinks the gap between embedders" holds: dense spreads 40.7-59.5 (18.8 points), hybrid 55.8-64.3 (8.4).
- prediction "335m-1.2GB gain 2-6 over 22m with hybrid" holds (3.7-6.0 except bge-m3 +0.2, qwen3 +1.6).
- prediction "no tiny model beats 22m by 1 point" is **refuted**: arctic-33m (67 MB) +2.8. stage B decides whether it
  holds on lme-s; if it does it meets the default-change rule (<= ~70 MB, >= +1.0 memscore, no suite down > 1).
- pareto front (size vs hybrid): 22m, 33m, 110m, nomic, embeddinggemma. stage B runs lme-s on those four.
- bge-m3 and qwen3-embedding underperform their size here (bge-m3's ollama build has no query prefix; qwen3 is
  instruction-tuned and we use one fixed instruction). not tuned: we measure what a user who picks them gets.
