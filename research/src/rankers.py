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


# --- H2: fusion form ---
def _rrf_w(wd, wl, k=60):
    return lambda idx, q, qv: rrf([ids(idx.dense(qv)), ids(idx.lexical(q.text))], [wd, wl], k)


rrf_d2 = _rrf_w(2.0, 1.0)
rrf_l2 = _rrf_w(1.0, 2.0)
rrf_k20 = _rrf_w(1.0, 1.0, 20)
rrf_k10 = _rrf_w(1.0, 1.0, 10)


def _norm(pairs):
    if not pairs:
        return {}
    hi, lo = pairs[0][1], pairs[-1][1]
    return {i: (s - lo) / (hi - lo) if hi > lo else 1.0 for i, s in pairs}


def _interp(alpha):
    def f(idx, q, qv):
        d, l = _norm(idx.dense(qv)), _norm(idx.lexical(q.text))
        s = {i: alpha * d.get(i, 0) + (1 - alpha) * l.get(i, 0) for i in d.keys() | l.keys()}
        return sorted(s, key=s.get, reverse=True)
    return f


interp_50 = _interp(0.5)
interp_65 = _interp(0.65)
interp_80 = _interp(0.8)


# --- H3: time-aware query ---
import calendar  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402

UNIT = {"day": 1, "week": 7, "month": 30.4, "year": 365}
NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
       "nine": 9, "ten": 10, "a couple of": 2, "a few": 3, "several": 4}
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m} | {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
N = r"(\d+|a couple of|a few|several|an?|one|two|three|four|five|six|seven|eight|nine|ten)"


def time_window(text: str, now_ms: int):
    """(lo, hi) in ms for the time words in a query, or None. wide on purpose: a soft hint, not a filter."""
    t, now = text.lower(), datetime.fromtimestamp(now_ms / 1000)
    day = 86_400_000

    def back(days_lo, days_hi):
        return int(now_ms - days_hi * day), int(now_ms - days_lo * day)

    if m := re.search(N + r" (day|week|month|year)s? ago", t):
        n, u = NUM.get(m[1]) or int(m[1]), UNIT[m[2]]
        return back(max(0, n * u - u - 2), n * u + u + 2)
    if m := re.search(r"(?:past|last|previous) " + N + r" (day|week|month|year)s?", t):
        n, u = NUM.get(m[1]) or int(m[1]), UNIT[m[2]]
        return back(0, n * u + 2)
    if m := re.search(r"(?:past|last|this|previous) (day|week|month|year)\b", t):
        return back(0, UNIT[m[1]] * (1 if m[0].startswith("this") else 2) + 2)
    if "yesterday" in t:
        return back(0, 3)
    if "today" in t or "tonight" in t or "this morning" in t:
        return back(0, 1.5)
    if m := re.search(r"\b(?:in|during|on|of|since) (" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b(?:,? (\d{4}))?", t):
        mon = MONTHS[m[1]]
        year = int(m[2]) if m[2] else (now.year if mon <= now.month else now.year - 1)
        start = datetime(year, mon, 1)
        end = start + timedelta(days=calendar.monthrange(year, mon)[1])
        return int((start - timedelta(days=3)).timestamp() * 1000), int((end + timedelta(days=3)).timestamp() * 1000)
    return None


def time_hybrid(idx, q, qv):
    lists = [ids(idx.dense(qv)), ids(idx.lexical(q.text))]
    if w := time_window(q.text, q.when):
        lists.append(ids(idx.dense(qv, k=20, lo=w[0], hi=w[1])))
    return rrf(lists)


# --- H4: recency prior over the fused candidates ---
def _recent(beta):
    def f(idx, q, qv):
        fused = rrf([ids(idx.dense(qv)), ids(idx.lexical(q.text))])[:50]
        by_age = sorted(fused, key=idx.created.get, reverse=True)
        return rrf([fused, by_age], [1.0, beta])
    return f


recent_10 = _recent(0.1)
recent_30 = _recent(0.3)


interp_35 = _interp(0.35)
interp_20 = _interp(0.2)


def time_interp(idx, q, qv):
    """interp_50, plus a third channel: dense search restricted (natively, in qenlo) to the query's time window."""
    d, l = _norm(idx.dense(qv)), _norm(idx.lexical(q.text))
    w = time_window(q.text, q.when)
    t = _norm(idx.dense(qv, k=20, lo=w[0], hi=w[1])) if w else {}
    s = {i: 0.5 * d.get(i, 0) + 0.5 * l.get(i, 0) + 0.25 * t.get(i, 0) for i in d.keys() | l.keys() | t.keys()}
    return sorted(s, key=s.get, reverse=True)


def _scale(pairs):
    """divide by the best score: keeps ratios, so near-zero idf noise stays near zero relative to... itself."""
    if not pairs:
        return {}
    hi = max(s for _, s in pairs)
    return {i: s / hi if hi > 0 else 0.0 for i, s in pairs}


def interp_lexmax(idx, q, qv):
    d, l = _norm(idx.dense(qv)), _scale(idx.lexical(q.text))
    s = {i: 0.5 * d.get(i, 0) + 0.5 * l.get(i, 0) for i in d.keys() | l.keys()}
    return sorted(s, key=s.get, reverse=True)


def interp_lexconf(idx, q, qv):
    """lexical channel scaled by its best score, and trusted only as far as that score is real bm25 evidence
    (fts5 floors idf at ~1e-6 for words in most rows, which happens in every small store)."""
    lex = idx.lexical(q.text)
    hi = max((s for _, s in lex), default=0)
    d, l = _norm(idx.dense(qv)), {i: s / hi * min(1.0, hi) for i, s in lex} if hi > 0 else {}
    s = {i: 0.5 * d.get(i, 0) + 0.5 * l.get(i, 0) for i in d.keys() | l.keys()}
    return sorted(s, key=s.get, reverse=True)


# --- E3/E4 (from EdgeMem, arXiv:2609.05553): episode context + neighbour readout ---
def _session(idx, d):
    u = idx.h.unit[d][0] if idx.h.unit[d] else str(d)
    return u.split(":")[0] if ":" in u else u  # locomo "D3:5" -> "D3"; lme session ids as they are


def _episodes(idx):
    """lazily: session -> its docs in time order, and an fts5 table over each session's concatenated text."""
    if not hasattr(idx, "_eps"):
        import sqlite3
        eps = {}
        for d in sorted(idx.text, key=lambda d: (idx.created[d], d)):
            eps.setdefault(_session(idx, d), []).append(d)
        db = sqlite3.connect(":memory:")
        db.execute("CREATE VIRTUAL TABLE s USING fts5(name UNINDEXED, text, tokenize='porter unicode61')")
        db.executemany("INSERT INTO s(name, text) VALUES (?, ?)", [(n, " ".join(idx.text[d] for d in ds)) for n, ds in eps.items()])
        idx._eps, idx._sdb = eps, db
    return idx._eps


def _session_scores(idx, query, k=20):
    _episodes(idx)
    match = fts_query(query)
    if not match:
        return {}
    rows = idx._sdb.execute("SELECT name, -bm25(s) FROM s WHERE s MATCH ? ORDER BY bm25(s) LIMIT ?", (match, k)).fetchall()
    hi = max((v for _, v in rows), default=0)
    return {n: v / hi for n, v in rows} if hi > 0 else {}  # max-normalised, as EdgeMem does


def _turn_scores(idx, q, qv):
    """the shipped 0.3 fusion, as scores."""
    d, l = _norm(idx.dense(qv)), _norm(idx.lexical(q.text))
    w = time_window(q.text, q.when)
    t = _norm(idx.dense(qv, k=20, lo=w[0], hi=w[1])) if w else {}
    return {i: 0.5 * d.get(i, 0) + 0.5 * l.get(i, 0) + 0.25 * t.get(i, 0) for i in d.keys() | l.keys() | t.keys()}


def episode(idx, q, qv, lam=0.5, top_sessions=3):
    """rho(t) = lam * turn score + (1 - lam) * session score. the turns of the best sessions join the candidates,
    so a session that matches as a whole can surface a turn no channel found on its own."""
    eps, turn, sess = _episodes(idx), _turn_scores(idx, q, qv), _session_scores(idx, q.text)
    hi = max(turn.values(), default=0) or 1.0
    cand = set(turn) | {d for n in sorted(sess, key=sess.get, reverse=True)[:top_sessions] for d in eps[n]}
    rho = {d: lam * turn.get(d, 0) / hi + (1 - lam) * sess.get(_session(idx, d), 0) for d in cand}
    return sorted(rho, key=rho.get, reverse=True)


def _with_neighbours(ranked, idx, k=50):
    """each seed followed by the turn before and after it in its session, deduplicated (EdgeMem's readout)."""
    eps, out, seen = _episodes(idx), [], set()
    pos = {d: (n, i) for n, ds in eps.items() for i, d in enumerate(ds)}
    for d in ranked:
        n, i = pos[d]
        for x in (d, *(eps[n][j] for j in (i - 1, i + 1) if 0 <= j < len(eps[n]))):
            if x not in seen:
                seen.add(x)
                out.append(x)
        if len(out) >= k:
            break
    return out


def episode_nb(idx, q, qv):
    return _with_neighbours(episode(idx, q, qv), idx)


def time_interp_nb(idx, q, qv):
    return _with_neighbours(time_interp(idx, q, qv), idx)


def _diverse(ranked, idx, floor=3, window=5):
    """EdgeMem's diversity floor: at least `floor` distinct sessions among the first `window` results."""
    out, seen = [], set()
    for d in ranked:
        s = _session(idx, d)
        if len(out) < window and s in seen and window - len(out) <= floor - len(seen):
            continue  # this slot is needed for a new session
        out.append(d)
        seen.add(s)
    head = out[:window]
    return head + [d for d in ranked if d not in set(head)]  # everything else keeps its original order


def episode_div(idx, q, qv):
    return _diverse(episode(idx, q, qv), idx)


def episode_boost(idx, q, qv, lam=0.5, top_sessions=3):
    """E3.2: the session score can lift a turn, never sink it: rho = max(t, lam*t + (1-lam)*s)."""
    eps, turn, sess = _episodes(idx), _turn_scores(idx, q, qv), _session_scores(idx, q.text)
    hi = max(turn.values(), default=0) or 1.0
    cand = set(turn) | {d for n in sorted(sess, key=sess.get, reverse=True)[:top_sessions] for d in eps[n]}
    t = {d: turn.get(d, 0) / hi for d in cand}
    rho = {d: max(t[d], lam * t[d] + (1 - lam) * sess.get(_session(idx, d), 0)) for d in cand}
    return sorted(rho, key=rho.get, reverse=True)
