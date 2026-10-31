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
    key = f"[{m['key']}] " if m.get("key") else ""
    return f"#{m['id']} {m['kind']} | by {m['agent']} | {day} | {m['project'] or '-'}{score}{gone} | {key}{m['text']}"


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
        return [v for i in range(0, len(texts), 256)
                for v in ollama("/api/embed", {"model": self.model, "input": texts[i:i + 256], "keep_alive": -1})["embeddings"]]

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


class Store:
    def __init__(self, root: Path, embed=None, backend: str = "automatic"):
        root.mkdir(parents=True, exist_ok=True)
        self.root, self.backend = root, backend
        self.embed = embed or Embedder()
        self.db = sqlite3.connect(root / "memories.db", check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,"
            " text TEXT NOT NULL, agent TEXT NOT NULL, project TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL)"
        )
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(memories)")}
        # 0.3: which memory replaced this one and when, and the slot key the writing agent gave it
        for col, typ in (("superseded_by", "INTEGER"), ("superseded_at", "INTEGER"), ("key", "TEXT")):
            if col not in have:
                self.db.execute(f"ALTER TABLE memories ADD COLUMN {col} {typ}")
        self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS words USING fts5(text, tokenize='porter unicode61')")
        # ponytail: one global lock. one user's agents never come close to needing more.
        self.lock = threading.Lock()
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
        """every qenlo write is one WAL file that open replays. fold them into a snapshot now and then."""
        if len(list(self.path.glob("wal-*.qwal"))) >= COMPACT_EVERY:
            with contextlib.suppress(QenloError):  # the write is already durable; compaction can wait
                self.vectors.flush()

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

    def _related(self, vector, skip: int) -> list[dict]:
        """current memories that are probably about the same thing, so the agent can decide: update or keep both."""
        near = [(i, s) for i, s in self._dense(vector, "", 4) if i != skip and s >= RELATED][:3]
        rows = self._rows(i for i, _ in near)
        return [{**rows[i], "score": round(s, 3)} for i, s in near if i in rows]

    def _insert(self, rows: list[tuple], vectors: list, retire: int | None = None) -> list[int]:
        """rows of (kind, text, agent, project, created, key): one sqlite transaction, one qenlo batch.
        retire: a memory the (single) new row supersedes, in the same transaction."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            ids = [self.db.execute("INSERT INTO memories(kind, text, agent, project, created, key) VALUES (?, ?, ?, ?, ?, ?)", r).lastrowid
                   for r in rows]
            # the key is indexed with the text: "staging.db.port" is exactly the kind of word bm25 is for
            self.db.executemany("INSERT INTO words(rowid, text) VALUES (?, ?)", [(i, f"{r[1]} {r[5] or ''}") for i, r in zip(ids, rows)])
            self.vectors.add_batch([Record(i, KINDS[r[0]], r[4], v) for i, r, v in zip(ids, rows, vectors)])
            if retire:
                self._retire(retire, ids[0], rows[0][4])
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return ids

    def _retire(self, id: int, by: int | None, now: int) -> None:
        """take a memory out of both indexes. by=None deletes it; otherwise it stays in sqlite as history."""
        self.db.execute("DELETE FROM words WHERE rowid = ?", (id,))
        if by is None:
            self.db.execute("DELETE FROM memories WHERE id = ?", (id,))
        else:
            self.db.execute("UPDATE memories SET superseded_by = ?, superseded_at = ? WHERE id = ?", (by, now, id))
        with contextlib.suppress(QenloError):  # a superseded memory has no vector left to delete
            self.vectors.delete(id)

    @staticmethod
    def _check(text: str, kind: str) -> str:
        text = redact(text.strip())
        if not text:
            raise ValueError("nothing to remember")
        if kind not in KINDS:
            raise ValueError(f"kind must be one of: {', '.join(KINDS)}")
        return text

    def remember(self, text: str, kind: str = "semantic", agent: str = "unknown", project: str = "",
                 replaces: int | None = None, key: str = "") -> dict:
        """replaces: the id of a memory this one updates. the old one leaves search but stays in `history`.
        key: a stable name for what the fact is about ("staging.db.port"), chosen by the writing agent. a new
        memory with the key of a current one in the same project replaces it, with no similarity guess."""
        text, key = self._check(text, kind), " ".join(key.lower().split())
        vector = self.embed([text])[0]
        with self.lock:
            # only the exact same text is a duplicate. "port 3000" vs "port 3001" embed almost identically,
            # and a similarity cutoff would silently keep the stale one. related memories are shown instead.
            same = self.db.execute("SELECT * FROM memories WHERE kind = ? AND lower(text) = lower(?) AND superseded_by IS NULL",
                                   (kind, text)).fetchone()
            if key and not replaces:
                slot = self.db.execute("SELECT id FROM memories WHERE key = ? AND project = ? AND superseded_by IS NULL",
                                       (key, project)).fetchone()
                replaces = slot and (not same or slot["id"] != same["id"]) and slot["id"]  # same text, same key: a duplicate
            old = self._rows([int(replaces)]).get(int(replaces)) if replaces else None
            if replaces and (not old or old["superseded_by"] or (same and same["id"] == old["id"])):
                raise KeyError(f"no current memory #{replaces} to replace")
            now = int(time.time() * 1000)
            if same:  # saved already. with replaces, this links the two after the fact
                if old:
                    self._retire(old["id"], same["id"], now)
                    if old["kind"] == "long_term":
                        self._profile()
                return {**dict(same), "duplicate": True, "replaced": old["id"] if old else None, "related": []}
            mid = self._insert([(kind, text, agent or "unknown", project, now, key or None)], [vector], old and old["id"])[0]
            self._compact()
            if kind == "long_term" or (old and old["kind"] == "long_term"):
                self._profile()
        related = [] if old else self._related(vector, mid)
        return {"id": mid, "kind": kind, "text": text, "agent": agent, "project": project, "created": now, "key": key or None,
                "replaced": old["id"] if old else None, "related": related}

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
        with self.lock:
            ids = self._insert(rows, vectors) if rows else []
            self.vectors.flush()
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
        with self.lock:
            row = self._rows([id]).get(id)
            if not row:
                raise KeyError(f"no memory #{id}")
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self._retire(id, None, 0)
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            self._compact()
            if row["kind"] == "long_term":
                self._profile()
        return row

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

    def export(self) -> list[dict]:
        """everything, superseded versions included, oldest first. `import` reads it back."""
        return [dict(r) for r in self.db.execute("SELECT * FROM memories ORDER BY id")]

    def context(self, project: str = "", query: str = "") -> str:
        """what an agent should know at session start: the pinned long-term tier, then this project."""
        parts = []
        core = self.recent(50, kind="long_term")
        if core:
            parts.append("long-term memory (about the user, from every agent):\n" + "\n".join(map(line, core)))
        mine = self.recent(15, project=project) if project else []
        mine = [m for m in mine if m["kind"] != "long_term"]
        if query:
            seen = {m["id"] for m in core + mine}
            mine += [m for m in self.recall(query, k=8) if m["id"] not in seen and m["kind"] != "episodic"]
        if mine:
            parts.append(f"memory for {project or 'this session'} (newest first, any agent):\n" + "\n".join(map(line, mine)))
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
        with self.lock:
            self.vectors.close()
            self.db.close()
