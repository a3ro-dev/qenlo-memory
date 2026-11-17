"""sqlite keeps the text, who wrote it, and which memory replaced which. qenlo keeps the vectors of the
current memories and runs the dense half of every search; sqlite FTS5 runs the keyword half.

recall fuses the two (min-max normalised scores, 50/50), plus a third channel when the query names a time
("last week", "in may", "3 days ago"): a dense search restricted to that window, which qenlo filters natively.
measured on LoCoMo and LongMemEval in research/. superseded memories leave both indexes but stay in sqlite,
so `history` can show what a fact used to be (invalidate, don't delete: zep, engram, tepa).
"""

from __future__ import annotations

import calendar
import contextlib
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from qenlo import Collection, Filter, QenloError, Record

from . import models

# qenlo filters on user_id by equality, so the memory kind lives there and kind filters run natively.
KINDS = {"episodic": 1, "semantic": 2, "procedural": 3, "long_term": 4}
_host = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
OLLAMA = (_host if "://" in _host else "http://" + _host).rstrip("/")
COMPACT_EVERY = 256  # WAL files before we fold them into a qenlo snapshot
GPU_FROM = 4096  # qenlo's automatic router only uses the GPU from this many eligible rows (AUTOMATIC_GPU_MIN_ELIGIBLE_ROWS)
RELATED = 0.82  # cosine above which an existing memory is probably about the same thing (measured on real memories)


class Conflict(Exception):
    """a write lost a race or would contradict a current memory. the daemon answers 409."""

SECRET = re.compile(
    r"\bsk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
    r"|((?i:(?<![a-z0-9])(?:password|passwd|pass|pwd|secret(?:_access)?_key|secret|api[_-]?key|token)\b)[\"']?\s*[:=]\s*[\"']?"
    r"|(?i:(?<![a-z0-9])password\s+(?:is|as|to)\s+)[\"']?)\S{6,}"
)


def redact(text: str) -> str:
    return SECRET.sub(lambda m: (m.group(1) or "") + "[redacted]", text)


def line(m: dict) -> str:
    day = time.strftime("%Y-%m-%d %H:%M", time.localtime(m["created"] / 1000))
    score = f" | {m['score']:.2f}" if "score" in m else ""
    gone = f" | replaced by #{m['superseded_by']}" if m.get("superseded_by") else ""
    # state labels for the reader (A-TMA, arXiv:2607.01935): which note is current, contested or unreviewed
    flag = (" | tentative" if m.get("tentative") else "") + (f" | possibly stale: newer #{m['contested_by']} disagrees" if m.get("contested_by") else "")
    key = f"[{m['key']}] " if m.get("key") else ""
    return f"#{m['id']} {m['kind']} | by {m['agent']} | {day} | {m['project'] or '-'}{score}{gone}{flag} | {key}{m['text']}"


NEG = re.compile(r"\b(?:not|no|never|none|cannot|without|stop(?:ped)?|disabled?|removed?|deprecated|avoid|don't|doesn't|isn't|aren't|won't|can't|shouldn't|wasn't)\b|n't\b", re.I)
TOKEN = re.compile(r"[\w.:#/-]+")
VALUE = re.compile(r"v?\d+(?:[.:]\d+)*[a-z%]{0,2}")  # 5433, 3.12, v1.0.0, 17:05, 30s, 8gb. not dates, hashes or ids
# words that name which thing ("step 3", "agent 5"), so two different numbers after them don't disagree.
# shortcut: an english word list, extend it when real stores show more label words
LABEL = set("agent note step item issue pr ticket session part phase task test case line row id round run".split())
SHORT = 30  # words. a long note holds many facts, so one "not" in it says nothing about its polarity


def slots(text: str) -> dict[str, set[str]]:
    """value tokens by the content word just before them: 'listens on port 5433' -> {'port': {'5433'}}."""
    out, anchor = {}, ""
    for t in TOKEN.findall(text):
        t = t.strip(".:,;")
        if VALUE.fullmatch(t):
            if anchor and anchor not in LABEL:
                out.setdefault(anchor, set()).add(t)
        elif t.isalpha() and t not in STOP:
            anchor = t
    return out


def contradicts(new: str, old: str) -> str:
    """a cheap, no-llm guess that two texts about the same thing disagree (the symbolic slot check LatticeMind
    and MEMOIR run before anything expensive): the same word followed by a different value, or, in short notes,
    one saying not where the other doesn't. call it only on pairs already known to be about the same thing.
    returns the reason, or ''. measured on a real store in research/findings.md (0.5)."""
    a, b = new.lower(), old.lower()
    sa, sb = slots(a), slots(b)
    shape = lambda vs: {(v[0] == "v", v.count("."), v.count(":"), v.lstrip("v0123456789.:")) for v in vs}
    for word in sa.keys() & sb.keys():
        if not sa[word] & sb[word] and shape(sa[word]) & shape(sb[word]):  # 192k vs 402 is a unit mismatch, not a change
            return f"{word}: {', '.join(sorted(sb[word]))} -> {', '.join(sorted(sa[word]))}"
    if max(len(a.split()), len(b.split())) <= SHORT and bool(NEG.search(a)) != bool(NEG.search(b)):
        return "one says not"
    return ""


WORD = re.compile(r"\w[\w.\-/]*\w|\w")
# words that carry no signal for bm25 and make FTS5 walk every row
STOP = set("a an the and or but if of to in on at by for with from as is are was were be been being do does did "
           "i me my you your he she it its we our they them their what which who whom when where why how this that "
           "these those there here have has had not no can could would should will shall may might must so than then "
           "about into over after before up down out just also very any some all".split())


def fts_query(text: str) -> str:
    words = [w for w in dict.fromkeys(w.lower() for w in WORD.findall(text)) if w not in STOP]
    return " OR ".join('"' + w.replace('"', "") + '"' for w in words)


UNIT = {"day": 1, "week": 7, "month": 30.4, "year": 365}
NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
       "nine": 9, "ten": 10, "a couple of": 2, "a few": 3, "several": 4}
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m} | {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
N = r"(\d+|a couple of|a few|several|an?|one|two|three|four|five|six|seven|eight|nine|ten)"


def time_window(text: str, now_ms: int) -> tuple[int, int] | None:
    """the time a query talks about, as a wide (lo, hi) in ms. a hint for ranking, never a hard filter."""
    t, now, day = text.lower(), datetime.fromtimestamp(now_ms / 1000), 86_400_000
    back = lambda lo, hi: (int(now_ms - hi * day), int(now_ms - lo * day))
    if m := re.search(N + r" (day|week|month|year)s? ago", t):
        n, u = NUM.get(m[1]) or int(m[1]), UNIT[m[2]]
        return back(max(0, n * u - u - 2), n * u + u + 2)
    if m := re.search(r"(?:past|last|previous) " + N + r" (day|week|month|year)s?", t):
        return back(0, (NUM.get(m[1]) or int(m[1])) * UNIT[m[2]] + 2)
    if m := re.search(r"(?:past|last|this|previous) (day|week|month|year)\b", t):
        return back(0, UNIT[m[1]] * (1 if m[0].startswith("this") else 2) + 2)
    if "yesterday" in t:
        return back(0, 3)
    if re.search(r"\btoday\b|\btonight\b|this morning", t):
        return back(0, 1.5)
    if m := re.search(r"\b(?:in|during|on|of|since) (" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b(?:,? (\d{4}))?", t):
        mon = MONTHS[m[1]]
        year = int(m[2]) if m[2] else now.year if mon <= now.month else now.year - 1
        start = datetime(year, mon, 1)
        end = start + timedelta(days=calendar.monthrange(year, mon)[1])
        return int((start - timedelta(days=3)).timestamp() * 1000), int((end + timedelta(days=3)).timestamp() * 1000)
    return None


def _norm(pairs: list[tuple[int, float]]) -> dict[int, float]:
    """best hit 1, worst 0, so cosine and bm25 can be added."""
    if not pairs:
        return {}
    hi, lo = max(s for _, s in pairs), min(s for _, s in pairs)
    return {i: (s - lo) / (hi - lo) if hi > lo else 1.0 for i, s in pairs}


def _lexical(pairs: list[tuple[int, float]]) -> dict[int, float]:
    """bm25 over its best score, trusted only as far as that score is real evidence: fts5 floors the idf of
    words found in most rows at ~1e-6, which is every word in a store of three memories."""
    hi = max((s for _, s in pairs), default=0)
    return {i: s / hi * min(1.0, hi) for i, s in pairs} if hi > 0 else {}


class Embedder:
    """embeddings come from ollama, which already runs them on the GPU. no torch in this process."""

    def __init__(self, model: models.Model | None = None):
        self.spec = model or models.active()
        self.model = self.spec.name
        # concurrent single-text calls (one per daemon request) are pooled into one ollama call: ollama runs embed
        # requests one at a time, so 100 agents queued 100 calls (2.3 s) where one batch takes 0.9 s (research/src/load.py)
        self.waiting, self.ready = [], threading.Condition()
        threading.Thread(target=self._batcher, daemon=True).start()
        try:
            self.dim = len(self(["probe"])[0])
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            ollama("/api/pull", {"model": self.model, "stream": False})  # first run, or a new model
            self.dim = len(self(["probe"])[0])

    def __call__(self, texts: list[str], query: bool = False) -> list[list[float]]:
        prefix = self.spec.query if query else self.spec.doc
        texts = [prefix + t for t in texts]
        if len(texts) == 1:
            job = {"text": texts[0], "done": threading.Event()}
            with self.ready:
                self.waiting.append(job)
                self.ready.notify()
            job["done"].wait()
            if "error" in job:
                raise job["error"]
            return [job["vector"]]
        return self._embed(texts)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        return [v for i in range(0, len(texts), 256)
                for v in ollama("/api/embed", {"model": self.model, "input": texts[i:i + 256], "keep_alive": -1})["embeddings"]]

    def _batcher(self) -> None:
        while True:
            with self.ready:
                self.ready.wait_for(lambda: self.waiting)
                jobs, self.waiting = self.waiting[:256], self.waiting[256:]
            try:
                for job, v in zip(jobs, self._embed([j["text"] for j in jobs]), strict=True):
                    job["vector"] = v
            except Exception as e:  # every caller in the batch gets the error, the batcher keeps running
                for job in jobs:
                    job["error"] = e
            for job in jobs:
                job["done"].set()

    @property
    def device(self) -> str:
        for m in ollama("/api/ps")["models"]:
            if m["name"] == self.model:
                return "gpu" if m["size_vram"] >= m["size"] else "partly gpu" if m["size_vram"] else "cpu"
        return "not loaded"


def ollama(path: str, body: dict | None = None):
    request = urllib.request.Request(OLLAMA + path, json.dumps(body).encode() if body else None, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=1800) as r:
            return json.load(r)
    except urllib.error.URLError as e:
        if isinstance(e, urllib.error.HTTPError):
            raise
        raise RuntimeError(f"ollama isn't reachable at {OLLAMA}, start it and try again") from None


class _Rows(list):
    lastrowid = None

    def fetchone(self):
        return self[0] if self else None

    def fetchall(self):
        return self


class _DB:
    """one sqlite connection shared by the daemon's request threads. each statement runs and is fetched whole
    under a lock: interleaved cursors on one connection fail with 'bad parameter or other API misuse' (found by
    tests/test_concurrency.py). writes still serialise on Store.lock, so a transaction is never interleaved by a write."""

    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()

    def execute(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            rows = _Rows(cur.fetchall())
            rows.lastrowid = cur.lastrowid
            return rows

    def executemany(self, sql, seq):
        with self.lock:
            self.conn.executemany(sql, seq)

    def close(self):
        with self.lock:
            self.conn.close()


class Store:
    def __init__(self, root: Path, embed=None, backend: str = "automatic"):
        root.mkdir(parents=True, exist_ok=True)
        self.root, self.backend = root, backend
        self.embed = embed or Embedder()
        self.db = _DB(root / "memories.db")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,"
            " text TEXT NOT NULL, agent TEXT NOT NULL, project TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL)"
        )
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(memories)")}
        # 0.3: which memory replaced this one and when, and the slot key the writing agent gave it
        # 0.5: tentative = written but not confirmed yet; contested_by = a newer memory that seems to disagree
        for col, typ in (("superseded_by", "INTEGER"), ("superseded_at", "INTEGER"), ("key", "TEXT"),
                         ("tentative", "INTEGER"), ("contested_by", "INTEGER")):
            if col not in have:
                self.db.execute(f"ALTER TABLE memories ADD COLUMN {col} {typ}")
        self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS words USING fts5(text, tokenize='porter unicode61')")
        # wal journal: a commit is one append + fsync instead of the rollback journal's several
        self.db.execute("PRAGMA journal_mode=WAL")
        self.lock = threading.Lock()  # around every qenlo call
        # group commit: writers that arrive together share one sqlite transaction and one qenlo batch.
        # wlock is held by whichever writer commits the group; queue holds the writers waiting for the next one.
        self.wlock, self.qlock, self.queue = threading.Lock(), threading.Lock(), []
        self._staged, self._gone, self._long_term = [], [], False
        self.path = root / "vectors.qenlo"
        self.marker = root / "vectors.model"  # which model made the vectors
        self.search_backend = "none yet"
        if self.path.exists() and self.marker.exists() and self.marker.read_text().strip() != self.embed.model:
            shutil.rmtree(self.path)  # another model's vectors. sqlite has the text, re-embed below
        live = self.db.execute("SELECT COUNT(*) FROM memories WHERE superseded_by IS NULL").fetchone()[0]
        # bringing up wgpu costs ~200 MB of RAM, and below GPU_FROM rows the router would search on the cpu anyway.
        # a store that grows past it gets the GPU path on the next daemon start.
        if self.backend == "automatic" and live < GPU_FROM:
            self.backend = "cpu"
        self.vectors = self._open()
        # a crash between the sqlite commit and the qenlo add leaves the two out of step. rebuild from sqlite.
        if self.vectors.stats().live_rows != live:
            self.vectors.close()
            self.vectors = self._rebuild()
        if self.db.execute("SELECT COUNT(*) FROM words").fetchone()[0] != live:  # new in 0.3, or out of step
            self.db.execute("DELETE FROM words")
            self.db.execute("INSERT INTO words(rowid, text) SELECT id, text || ' ' || coalesce(key, '') FROM memories WHERE superseded_by IS NULL")
        self.vectors.flush()  # qenlo >= 0.1.0a11 folds the WAL into a snapshot here
        self.wal = 0  # qenlo WAL files written since the last flush
        self._profile()

    def _open_collection(self) -> Collection:
        make = Collection.open if self.path.exists() else Collection.create
        try:
            return make(self.path, self.embed.dim, backend=self.backend)
        except QenloError as e:
            if "already open" in str(e) or self.backend == "cpu":
                raise
            self.backend = "cpu"  # no usable GPU adapter, qenlo still works fine on cpu
            return make(self.path, self.embed.dim, backend="cpu")

    def _rebuild(self) -> Collection:
        rows = self.db.execute("SELECT id, kind, created, text FROM memories WHERE superseded_by IS NULL ORDER BY id").fetchall()
        tmp, old = self.path.with_name("vectors.rebuild"), self.path.with_name("vectors.old")
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(old, ignore_errors=True)
        with Collection.create(tmp, self.embed.dim) as fresh:
            if rows:
                vectors = self.embed([r["text"] for r in rows])
                fresh.add_batch([Record(r["id"], KINDS[r["kind"]], r["created"], v) for r, v in zip(rows, vectors)])
        if self.path.exists():
            self.path.rename(old)
        tmp.rename(self.path)
        shutil.rmtree(old, ignore_errors=True)
        return self._open()

    def _open(self) -> Collection:
        vectors = self._open_collection()
        self.marker.write_text(self.embed.model)
        return vectors

    def _compact(self) -> None:
        """every qenlo write is one WAL file that open replays. fold them into a snapshot now and then.
        call with self.lock held."""
        if self.wal >= COMPACT_EVERY:
            with contextlib.suppress(QenloError):  # the write is already durable; compaction can wait
                self.vectors.flush()
                self.wal = 0

    def _profile(self) -> None:
        """the long-term tier as a plain file in the brain folder, for agents without session hooks."""
        brain = self.root / "brain"
        brain.mkdir(exist_ok=True)
        rows = self.recent(50, kind="long_term")
        text = "# long-term memory about the user (from every agent, newest first)\n\n" + "".join(f"- {line(m)}\n" for m in rows)
        (brain / "profile.md").write_text(text, encoding="utf-8")

    def _rows(self, ids) -> dict[int, dict]:
        ids = list(ids)
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        return {r["id"]: dict(r) for r in self.db.execute(f"SELECT * FROM memories WHERE id IN ({marks})", ids)}

    def _dense(self, vector, kind: str, k: int, lo: int | None = None, hi: int | None = None) -> list[tuple[int, float]]:
        with self.lock:
            response = self.vectors.search(vector, Filter(KINDS[kind] if kind else None, lo, hi), k=k)
        self.search_backend = response.report.actual_backend
        return [(h.id, 1 - h.distance) for h in response.results]

    def _words(self, query: str, kind: str, k: int) -> list[tuple[int, float]]:
        match = fts_query(query)
        if not match:
            return []
        sql = "SELECT w.rowid, bm25(words) FROM words w JOIN memories m ON m.id = w.rowid WHERE words MATCH ?"
        args = [match] + ([kind] if kind else [])
        rows = self.db.execute(sql + (" AND m.kind = ?" if kind else "") + " ORDER BY bm25(words) LIMIT ?", [*args, k])
        return [(i, -s) for i, s in rows]  # fts5's bm25 is lower-is-better

    def _related(self, vector, text: str) -> list[dict]:
        """current memories that are probably about the same thing, so the agent can decide: update or keep both.
        each carries "conflict": why it seems to disagree with the new text, by the no-llm check, or ''."""
        near = [(i, s) for i, s in self._dense(vector, "", 4) if s >= RELATED][:3]
        rows = self._rows(i for i, _ in near)
        out = [{**rows[i], "score": round(s, 3)} for i, s in near if i in rows and rows[i]["text"].lower() != text.lower()]
        return [{**r, "conflict": contradicts(text, r["text"])} for r in out]

    def _write(self, fn):
        """run fn inside a group commit; return what it returns or raise what it raised.
        each writer queues its fn. whoever takes wlock first runs every queued fn in one sqlite transaction, each
        in its own savepoint (one writer's Conflict doesn't undo the others), sends the group's vectors to qenlo in
        one batch and commits. later fns see earlier ones' rows, so key and replaces checks stay exact.
        under 100 writers that is one fsync per group instead of two per write (research/findings.md, 0.5)."""
        job = {"fn": fn, "done": threading.Event()}
        with self.qlock:
            self.queue.append(job)
        with self.wlock:
            if not job["done"].is_set():
                with self.qlock:
                    jobs, self.queue = self.queue, []
                self._group(jobs)
        if "error" in job:
            raise job["error"]
        return job["result"]

    def _group(self, jobs: list[dict]) -> None:
        self._staged, self._gone, self._long_term = [], [], False
        try:
            self.db.execute("BEGIN IMMEDIATE")
            for job in jobs:
                mark = (len(self._staged), len(self._gone), self._long_term)
                self.db.execute("SAVEPOINT w")
                try:
                    job["result"] = job["fn"]()
                    self.db.execute("RELEASE w")
                except Exception as e:
                    self.db.execute("ROLLBACK TO w")
                    self.db.execute("RELEASE w")
                    del self._staged[mark[0]:], self._gone[mark[1]:]
                    self._long_term, job["error"] = mark[2], e
            # qenlo only gets what is current at the end of the group: a row added and replaced in the same group
            # never reaches it, and a row whose retirement was rolled back keeps its vector
            ids = [r.id for r in self._staged] + self._gone
            live = {r["id"] for r in self.db.execute(
                f"SELECT id FROM memories WHERE superseded_by IS NULL AND id IN ({','.join('?' * len(ids))})", ids)} if ids else set()
            with self.lock:
                for i in self._gone:
                    if i not in live:
                        with contextlib.suppress(QenloError):  # a superseded memory has no vector left to delete
                            self.vectors.delete(i)
                            self.wal += 1
                add = [r for r in self._staged if r.id in live]
                if add:
                    self.vectors.add_batch(add)
                    self.wal += 1
                self.db.execute("COMMIT")
                self._compact()
        except BaseException as e:
            with contextlib.suppress(sqlite3.Error):
                self.db.execute("ROLLBACK")
            for job in jobs:
                job.pop("result", None)
                job.setdefault("error", e if isinstance(e, Exception) else RuntimeError("write interrupted"))
            if not isinstance(e, Exception):
                raise
        finally:
            for job in jobs:
                job["done"].set()
        if self._long_term:
            self._profile()

    def _insert(self, rows: list[tuple], vectors: list, retire: int | None = None, tentative: bool = False) -> list[int]:
        """rows of (kind, text, agent, project, created, key), inside _write. vectors reach qenlo when the group commits.
        retire: a memory the (single) new row supersedes."""
        ids = [self.db.execute("INSERT INTO memories(kind, text, agent, project, created, key, tentative) VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (*r, 1 if tentative else None)).lastrowid for r in rows]
        # the key is indexed with the text: "staging.db.port" is exactly the kind of word bm25 is for
        self.db.executemany("INSERT INTO words(rowid, text) VALUES (?, ?)", [(i, f"{r[1]} {r[5] or ''}") for i, r in zip(ids, rows)])
        self._staged += [Record(i, KINDS[r[0]], r[4], v) for i, r, v in zip(ids, rows, vectors)]
        self._long_term |= any(r[0] == "long_term" for r in rows)
        if retire:
            self._retire(retire, ids[0], rows[0][4])
        return ids

    def _retire(self, id: int, by: int | None, now: int) -> None:
        """take a memory out of both indexes, inside _write. by=None deletes it; otherwise it stays in sqlite as history."""
        row = self.db.execute("SELECT kind FROM memories WHERE id = ?", (id,)).fetchone()
        self._long_term |= bool(row) and row[0] == "long_term"
        self.db.execute("DELETE FROM words WHERE rowid = ?", (id,))
        if by is None:
            self.db.execute("DELETE FROM memories WHERE id = ?", (id,))
        else:
            self.db.execute("UPDATE memories SET superseded_by = ?, superseded_at = ? WHERE id = ?", (by, now, id))
        self.db.execute("UPDATE memories SET contested_by = NULL WHERE contested_by = ?", (id,))  # its objection leaves with it
        self._gone.append(id)

    @staticmethod
    def _check(text: str, kind: str) -> str:
        text = redact(text.strip())
        if not text:
            raise ValueError("nothing to remember")
        if kind not in KINDS:
            raise ValueError(f"kind must be one of: {', '.join(KINDS)}")
        return text

    def remember(self, text: str, kind: str = "semantic", agent: str = "unknown", project: str = "",
                 replaces: int | None = None, key: str = "", expect: int | None = None, tentative: bool = False,
                 strict: bool = False) -> dict:
        """replaces: the id of a memory this one updates. the old one leaves search but stays in `history`.
        key: a stable name for what the fact is about ("staging.db.port"), chosen by the writing agent. a new
        memory with the key of a current one in the same project replaces it, with no similarity guess.
        expect: compare-and-swap on the key. the id the writer read as current for it (0 = it was empty); if
        another agent wrote the key since, nothing is saved and Conflict says who. replaces is checked the same way.
        tentative: saved and searchable, kept out of session-start context until someone confirms it.
        strict: refuse the write if it seems to contradict a current memory it doesn't replace."""
        text, key = self._check(text, kind), " ".join(key.lower().split())
        vector = self.embed([text])[0]
        related = [] if replaces else self._related(vector, text)  # outside the write: _dense takes the qenlo lock

        def write():
            nonlocal replaces
            # only the exact same text is a duplicate. "port 3000" vs "port 3001" embed almost identically,
            # and a similarity cutoff would silently keep the stale one. related memories are shown instead.
            same = self.db.execute("SELECT * FROM memories WHERE kind = ? AND lower(text) = lower(?) AND superseded_by IS NULL",
                                   (kind, text)).fetchone()
            if key and not replaces:
                slot = self.db.execute("SELECT id FROM memories WHERE key = ? AND project = ? AND superseded_by IS NULL",
                                       (key, project)).fetchone()
                if expect is not None and (slot["id"] if slot else 0) != int(expect):
                    holder = self._rows([slot["id"]])[slot["id"]] if slot else None
                    raise Conflict(f"key {key} changed since you read it, it is now " + (
                        f"#{holder['id']} by {holder['agent']}: {holder['text'][:120]}" if holder else "empty")
                        + ". nothing saved: read it and decide again")
                replaces = slot and (not same or slot["id"] != same["id"]) and slot["id"]  # same text, same key: a duplicate
            old = self._rows([int(replaces)]).get(int(replaces)) if replaces else None
            if old and old["superseded_by"]:  # updated by someone after this writer read it: refuse, don't lose theirs
                new = self._rows([old["superseded_by"]]).get(old["superseded_by"]) or {"agent": "?", "text": "?"}
                raise Conflict(f"#{old['id']} was already replaced by #{old['superseded_by']} (by {new['agent']}: "
                               f"{new['text'][:120]}). nothing saved: read it and decide again")
            if replaces and (not old or (same and same["id"] == old["id"])):
                raise KeyError(f"no current memory #{replaces} to replace")
            now = int(time.time() * 1000)
            conflicts = [r for r in related if r["conflict"] and not (old and r["id"] == old["id"])]
            if strict and conflicts and not same:
                raise Conflict("it seems to contradict " + "; ".join(
                    f"#{r['id']} ({r['conflict']}): {r['text'][:120]}" for r in conflicts)
                    + ". pass replaces=<id> if it updates one, or save without strict to keep both")
            if same:  # saved already. with replaces, this links the two after the fact
                if old:
                    self._retire(old["id"], same["id"], now)
                return {**dict(same), "duplicate": True, "replaced": old["id"] if old else None, "related": []}
            mid = self._insert([(kind, text, agent or "unknown", project, now, key or None)], [vector], old and old["id"], tentative)[0]
            # both stay live; the older one is labelled until someone replaces or confirms it (LatticeMind's "contested")
            self.db.executemany("UPDATE memories SET contested_by = ? WHERE id = ? AND superseded_by IS NULL",
                                [(mid, r["id"]) for r in conflicts])
            return {"id": mid, "kind": kind, "text": text, "agent": agent, "project": project, "created": now, "key": key or None,
                    "tentative": 1 if tentative else None, "replaced": old["id"] if old else None,
                    "related": [r for r in related if not old or r["id"] != old["id"]]}

        return self._write(write)

    def remember_many(self, items: list[dict], agent: str = "import") -> dict:
        """bulk import: [{text, kind?, agent?, project?, created?}], embedded in batches. exact duplicates skipped."""
        seen = {(r["kind"], r["text"].lower()) for r in self.db.execute("SELECT kind, text FROM memories WHERE superseded_by IS NULL")}
        rows, now = [], int(time.time() * 1000)
        for it in items:
            kind = it.get("kind") or "semantic"
            text = self._check(str(it.get("text") or it.get("memory") or ""), kind)  # "memory" is mem0's export field
            if (kind, text.lower()) not in seen:
                seen.add((kind, text.lower()))
                rows.append((kind, text, it.get("agent") or agent, it.get("project") or "", int(it.get("created") or now),
                             " ".join(str(it.get("key") or "").lower().split()) or None))
        vectors = self.embed([r[1] for r in rows]) if rows else []
        ids = self._write(lambda: self._insert(rows, vectors)) if rows else []
        with self.lock:
            self.vectors.flush()
            self.wal = 0
        self._profile()
        return {"added": len(ids), "skipped": len(items) - len(ids)}

    def recall(self, query: str, kind: str = "", agent: str = "", project: str = "", k: int = 8, now: int = 0) -> list[dict]:
        if kind and kind not in KINDS:
            raise ValueError(f"kind must be one of: {', '.join(KINDS)}")
        k = max(1, min(int(k), 64))
        vector = self.embed([query], query=True)[0]
        pool = 64 if agent or project else max(50, k)  # qenlo filters kind and time; agent/project trim afterwards
        dense, words = _norm(self._dense(vector, kind, pool)), _lexical(self._words(query, kind, pool))
        window = time_window(query, now or int(time.time() * 1000))
        timed = _norm(self._dense(vector, kind, 20, *window)) if window else {}
        score = {i: 0.5 * dense.get(i, 0) + 0.5 * words.get(i, 0) + 0.25 * timed.get(i, 0)
                 for i in dense.keys() | words.keys() | timed.keys()}
        rows = self._rows(score)
        out = [{**rows[i], "score": round(score[i], 3)} for i in sorted(score, key=score.get, reverse=True)
               if i in rows and (not agent or rows[i]["agent"] == agent) and (not project or rows[i]["project"] == project)]
        return out[:k]

    def recent(self, n: int = 20, kind: str = "", agent: str = "", project: str = "") -> list[dict]:
        sql, args = "SELECT * FROM memories WHERE superseded_by IS NULL", []
        for col, val in (("kind", kind), ("agent", agent), ("project", project)):
            if val:
                sql += f" AND {col} = ?"
                args.append(val)
        rows = self.db.execute(sql + " ORDER BY id DESC LIMIT ?", [*args, max(1, min(int(n), 500))])
        return [dict(r) for r in rows]

    def history(self, id: int) -> list[dict]:
        """every version of a memory, oldest first: what it replaced, and what replaced it."""
        id = int(id)
        if not self._rows([id]):
            raise KeyError(f"no memory #{id}")
        chain = [id]
        while row := self.db.execute("SELECT id FROM memories WHERE superseded_by = ?", (chain[0],)).fetchone():
            chain.insert(0, row["id"])
        while (nxt := self._rows([chain[-1]]).get(chain[-1], {}).get("superseded_by")) and nxt not in chain:
            chain.append(nxt)
        rows = self._rows(chain)
        return [rows[i] for i in chain if i in rows]

    def forget(self, id: int) -> dict:
        id = int(id)

        def write():
            row = self._rows([id]).get(id)
            if not row:
                raise KeyError(f"no memory #{id}")
            self._retire(id, None, 0)
            return row

        return self._write(write)

    def review(self, project: str = "", limit: int = 20) -> list[list[dict]]:
        """groups of current memories that are probably about the same thing, oldest first in each group.
        what a store written before --replaces and --key existed is full of. the agent decides which are updates."""
        rows = self.recent(500, project=project)
        if not rows:
            return []
        by_id = {r["id"]: r for r in rows}
        vectors = self.embed([r["text"] for r in rows])
        taken, found = set(), []
        # newest first, each memory with its own close neighbours. no chaining: a~b and b~c doesn't make a~c
        for r, v in zip(rows, vectors):
            if r["id"] in taken:
                continue
            near = [j for j, s in self._dense(v, "", 6) if j != r["id"] and j in by_id and j not in taken and s >= RELATED]
            if near:
                taken.update([r["id"], *near])
                found.append(sorted([r, *(by_id[j] for j in near)], key=lambda m: m["id"]))
        return found[:max(1, min(int(limit), 100))]

    def confirm(self, id: int, expect: int | None = None) -> dict:
        """someone checked memory #id and it holds: promote it from tentative, clear any "possibly stale" label.
        review happens at promotion, not at every write (0xEddie's split). a contested memory needs expect = the
        disagreeing id the reviewer looked at; if a newer one arrived during the review, nothing changes (0xEddie's race)."""
        id = int(id)

        def write():
            row = self._rows([id]).get(id)
            if not row or row["superseded_by"]:
                raise KeyError(f"no current memory #{id}")
            if row["contested_by"] and (expect is None or int(expect) != row["contested_by"]):
                raise Conflict(f"#{id} is contested by #{row['contested_by']}" + (f", not #{expect}" if expect is not None else "")
                               + f". read #{row['contested_by']}, then confirm {id} --expect {row['contested_by']}")
            self.db.execute("UPDATE memories SET tentative = NULL, contested_by = NULL WHERE id = ?", (id,))
            self._long_term |= row["kind"] == "long_term"
            return {**row, "tentative": None, "contested_by": None}

        return self._write(write)

    def pending(self, project: str = "", n: int = 50) -> list[dict]:
        """current memories waiting on a decision: tentative ones, and ones a newer memory seems to contradict."""
        sql = "SELECT * FROM memories WHERE superseded_by IS NULL AND (tentative IS NOT NULL OR contested_by IS NOT NULL)"
        rows = self.db.execute(sql + (" AND project = ?" if project else "") + " ORDER BY id LIMIT ?",
                               [*([project] if project else []), max(1, min(int(n), 500))])
        return [dict(r) for r in rows]

    def changes(self, since: int = 0, project: str = "", days: float = 7) -> list[dict]:
        """what changed: every supersession since a time (ms, default `days` back), oldest first, as {old, new}.
        the "what used to be true, what is true now" view the temporal questions ask for, with no llm."""
        since = int(since or time.time() * 1000 - days * 86_400_000)
        sql = "SELECT * FROM memories WHERE superseded_at >= ?" + (" AND project = ?" if project else "") + " ORDER BY superseded_at"
        olds = [dict(r) for r in self.db.execute(sql, [since, *([project] if project else [])])]
        news = self._rows(o["superseded_by"] for o in olds)
        return [{"old": o, "new": news.get(o["superseded_by"])} for o in olds]

    def check(self, text: str, project: str = "", k: int = 3) -> list[dict]:
        """does this text (a diff, a plan, an answer) contradict a current memory? each line or sentence is matched
        to the memories closest to it and run through the same no-llm check as remember. a flag, never a verdict:
        it catches changed values and flipped negations, not a decision broken in other words."""
        chunks = list(dict.fromkeys(c.strip(" \t+-*>#") for c in re.split(r"\n|(?<=[.!?])\s+", text)))
        chunks = [c for c in chunks if len(WORD.findall(c)) >= 3][:300]  # shortcut: first 300 lines, enough for a pr diff
        if not chunks:
            return []
        out, seen = [], set()
        for c, v in zip(chunks, self.embed(chunks, query=True)):
            words = {w for w in WORD.findall(c.lower()) if w not in STOP and not w.isdigit()}
            # shortcut: 0.6 cosine and 2 shared words are unmeasured guesses for diff-line vs note; tune on real diffs
            near = {i for i, s in self._dense(v, "", k) if s >= 0.6} | {i for i, _ in self._words(c, "", k)}
            for i, m in self._rows(near).items():
                if project and m["project"] not in (project, ""):
                    continue
                shared = words & {w for w in WORD.findall(m["text"].lower()) if w not in STOP and not w.isdigit()}
                why = len(shared) >= 2 and contradicts(c, m["text"])
                if why and (c, i) not in seen:
                    seen.add((c, i))
                    out.append({"line": c, "memory": m, "reason": why})
        return out

    def export(self) -> list[dict]:
        """everything, superseded versions included, oldest first. `import` reads it back."""
        return [dict(r) for r in self.db.execute("SELECT * FROM memories ORDER BY id")]

    def context(self, project: str = "", query: str = "") -> str:
        """what an agent should know at session start: the pinned long-term tier, then this project."""
        parts = []
        core = [m for m in self.recent(50, kind="long_term") if not m["tentative"]]
        if core:
            parts.append("long-term memory (about the user, from every agent):\n" + "\n".join(map(line, core)))
        mine = self.recent(15, project=project) if project else []
        mine = [m for m in mine if m["kind"] != "long_term" and not m["tentative"]]  # unreviewed notes wait for recall
        if query:
            seen = {m["id"] for m in core + mine}
            mine += [m for m in self.recall(query, k=8) if m["id"] not in seen and m["kind"] != "episodic" and not m["tentative"]]
        if mine:
            parts.append(f"memory for {project or 'this session'} (newest first, any agent):\n" + "\n".join(map(line, mine)))
        waiting = len(self.pending(project))
        if waiting:
            parts.append(f"{waiting} memories wait on a decision (tentative or contradicted): qenlo-memory pending")
        return "\n\n".join(parts)

    def stats(self) -> dict:
        current = "FROM memories WHERE superseded_by IS NULL"
        count = lambda col: dict(self.db.execute(f"SELECT {col}, COUNT(*) {current} GROUP BY {col}").fetchall())
        since = lambda ms: self.db.execute(f"SELECT COUNT(*) {current} AND created >= ?", (int(time.time() * 1000) - ms,)).fetchone()[0]
        last = self.db.execute(f"SELECT agent, kind, created {current} ORDER BY id DESC LIMIT 1").fetchone()
        s = self.vectors.stats()
        return {
            "memories": s.live_rows,
            "superseded": self.db.execute("SELECT COUNT(*) FROM memories WHERE superseded_by IS NOT NULL").fetchone()[0],
            "pending": len(self.pending(n=500)),
            "by_agent": count("agent"),
            "by_kind": count("kind"),
            "last_hour": since(3_600_000),
            "last_day": since(86_400_000),
            "last": dict(last) if last else None,
            "model": self.embed.model,
            "dim": self.embed.dim,
            "embed_device": self.embed.device,
            "qenlo_backend": self.backend,
            "last_search_ran_on": self.search_backend,
            "wal_files": len(list(self.path.glob("wal-*.qwal"))),
            "home": str(self.root),
        }

    def close(self):
        with self.wlock, self.lock:
            self.vectors.close()
            self.db.close()
