"""rankers under test. each takes (index, query, query_vector) and returns doc ids, best first."""

from __future__ import annotations

import re

WORD = re.compile(r"[\w][\w.\-/]*\w|\w", re.UNICODE)
# short and boring words carry no signal in bm25 and make FTS5 scan everything
STOP = set("a an the and or but if of to in on at by for with from as is are was were be been being do does did "
           "i me my you your he she it its we our they them their what which who whom when where why how this that "
           "these those there here have has had not no can could would should will shall may might must so than then "
           "about into over after before up down out just also very any some all".split())


def fts_query(text: str) -> str:
    words = [w.lower() for w in WORD.findall(text)]
    words = [w for w in dict.fromkeys(words) if w not in STOP]
    return " OR ".join('"' + w.replace('"', "") + '"' for w in words)


def ids(pairs):
    return [i for i, _ in pairs]


def rrf(lists, weights=None, k=60):
    weights = weights or [1.0] * len(lists)
    score = {}
    for w, ranked in zip(weights, lists):
        for r, d in enumerate(ranked):
            score[d] = score.get(d, 0.0) + w / (k + r + 1)
    return sorted(score, key=score.get, reverse=True)


# --- baseline: what qenlo-memory 0.2 ships ---
def dense(idx, q, qv):
    return ids(idx.dense(qv))


def bm25(idx, q, qv):
    return ids(idx.lexical(q.text))


# --- H1 ---
def hybrid_rrf(idx, q, qv):
    return rrf([ids(idx.dense(qv)), ids(idx.lexical(q.text))])
