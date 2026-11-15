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

## stage B: arctic-33m on lme-s (confirmatory)

lme-s recall_all@5 88.2 (22m: 87.7). full memscore = mean(60.0, 62.1, 88.2) = **70.10** vs the default's 68.07:
+2.03, every suite up (+1.1 / +4.5 / +0.5). meets the default-change rule (67 MB <= ~70 MB, >= +1.0, no suite down).
before the switch: query-embed latency and daemon RAM vs 22m (measured with the GPU idle), and the product parity
check through Store.recall. remaining stage B (110m, nomic, embeddinggemma) running.

## decision: arctic-33m becomes the default (0.4)

query-embed latency under identical GPU load: 22m p50 39.4 / p90 75.8 ms, 33m p50 40.5 / p90 65.2 ms. ollama VRAM 37 ->
58 MB; the qenlo-memory daemon itself is unchanged. shipped parity through Store.recall on locomo-obs: 59.70 (bench
60.0). existing stores: the vectors.model marker sees the change, the daemon pulls 33m and re-embeds from sqlite.

## the shipped 33m default on EdgeMem's LongMemEval-S table (470 q, all turns, no LLM)

| | R_all@3 | NDCG@3 | R_all@10 | NDCG@10 | R_any@3 |
| --- | ---: | ---: | ---: | ---: | ---: |
| EdgeMem (paper) | 81.49 | 90.49 | 93.83 | 92.80 | |
| 0.4, arctic-33m | 82.77 | 91.44 | 94.89 | 93.81 | 97.23 |
| 0.3 recipe, arctic-22m | 84.47 | 91.89 | 95.32 | 93.79 | 96.60 |

33m still beats EdgeMem on all four reported numbers, but sits 1.7 below 22m on R_all@3 here, although it wins the locked
memscore suites (+2.0, lme-s user-turn suite +0.5). on haystacks that include long assistant turns the smaller model
does slightly better at k=3. reported both ways; the default decision stands on the locked metric.
