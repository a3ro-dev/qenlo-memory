"""sqlite keeps the text and who wrote it. qenlo keeps the vectors and does every search."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from qenlo import Collection, Filter, QenloError, Record

# qenlo filters on user_id by equality, so the memory kind lives there and kind filters run natively.
KINDS = {"episodic": 1, "semantic": 2, "procedural": 3, "long_term": 4}
MODEL = os.environ.get("QENLO_MEMORY_MODEL", "snowflake-arctic-embed:22m")
# arctic-embed wants this on queries and nothing on stored text. set to "" for models that don't.
QUERY_PREFIX = os.environ.get("QENLO_MEMORY_QUERY_PREFIX", "Represent this sentence for searching relevant passages: ")
_host = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
OLLAMA = (_host if "://" in _host else "http://" + _host).rstrip("/")
DUPLICATE = 0.05  # cosine distance under this is the same memory said twice
MAX_WAL = 2000

SECRET = re.compile(
    r"\bsk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
    r"|((?i:(?<![a-z0-9])(?:password|passwd|secret(?:_access)?_key|secret|api[_-]?key|token)\b)[\"']?\s*[:=]\s*[\"']?)\S{6,}"
)


def redact(text: str) -> str:
    return SECRET.sub(lambda m: (m.group(1) or "") + "[redacted]", text)


def line(m: dict) -> str:
    day = time.strftime("%Y-%m-%d %H:%M", time.localtime(m["created"] / 1000))
    score = f" | {m['score']:.2f}" if "score" in m else ""
    return f"#{m['id']} {m['kind']} | by {m['agent']} | {day} | {m['project'] or '-'}{score} | {m['text']}"


class Embedder:
    """embeddings come from ollama, which already runs them on the GPU. no torch in this process."""

    def __init__(self, model: str = MODEL):
        self.model = model
        try:
            self.dim = len(self(["probe"])[0])
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            ollama("/api/pull", {"model": model, "stream": False})  # first run: fetch the ~45MB model
            self.dim = len(self(["probe"])[0])

    def __call__(self, texts: list[str], query: bool = False) -> list[list[float]]:
        texts = [QUERY_PREFIX + t for t in texts] if query else texts
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
        # ponytail: one global lock. one user's agents never come close to needing more.
        self.lock = threading.Lock()
        self.path = root / "vectors.qenlo"
        self.search_backend = "none yet"
        self.vectors = self._open()
        live = self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        wal = len(list(self.path.glob("wal-*.qwal")))
        # qenlo replays every WAL file on open and never compacts from Python, and a crash between the
        # sqlite commit and the qenlo add leaves the two out of step. both get fixed the same way.
        if self.vectors.stats().live_rows != live or wal > MAX_WAL:
            self.vectors.close()
            self.vectors = self._rebuild()

    def _open(self) -> Collection:
        make = Collection.open if self.path.exists() else Collection.create
        try:
            return make(self.path, self.embed.dim, backend=self.backend)
        except QenloError as e:
            if "already open" in str(e) or self.backend == "cpu":
                raise
            self.backend = "cpu"  # no usable GPU adapter, qenlo still works fine on cpu
            return make(self.path, self.embed.dim, backend="cpu")

    def _rebuild(self) -> Collection:
        rows = self.db.execute("SELECT id, kind, created, text FROM memories ORDER BY id").fetchall()
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

    def _rows(self, ids) -> dict[int, dict]:
        ids = list(ids)
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        return {r["id"]: dict(r) for r in self.db.execute(f"SELECT * FROM memories WHERE id IN ({marks})", ids)}

    def _search(self, vector, kind: str, k: int):
        with self.lock:
            response = self.vectors.search(vector, Filter(user_id=KINDS[kind]) if kind else Filter(), k=k)
        self.search_backend = response.report.actual_backend
        return response.results

    def remember(self, text: str, kind: str = "semantic", agent: str = "unknown", project: str = "") -> dict:
        text = redact(text.strip())
        if not text:
            raise ValueError("nothing to remember")
        if kind not in KINDS:
            raise ValueError(f"kind must be one of: {', '.join(KINDS)}")
        vector = self.embed([text])[0]
        with self.lock:
            same = self.vectors.search(vector, Filter(user_id=KINDS[kind]), k=1).results
            if same and same[0].distance < DUPLICATE:
                return {**self._rows([same[0].id])[same[0].id], "duplicate": True}
            now = int(time.time() * 1000)
            self.db.execute("BEGIN IMMEDIATE")
            try:
                mid = self.db.execute(
                    "INSERT INTO memories(kind, text, agent, project, created) VALUES (?, ?, ?, ?, ?)",
                    (kind, text, agent or "unknown", project, now),
                ).lastrowid
                self.vectors.add(Record(mid, KINDS[kind], now, vector))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        return {"id": mid, "kind": kind, "text": text, "agent": agent, "project": project, "created": now}

    def recall(self, query: str, kind: str = "", agent: str = "", project: str = "", k: int = 8) -> list[dict]:
        if kind and kind not in KINDS:
            raise ValueError(f"kind must be one of: {', '.join(KINDS)}")
        k = max(1, min(int(k), 64))
        # qenlo only filters kind and time, so agent/project filters over-fetch and trim here.
        hits = self._search(self.embed([query], query=True)[0], kind, 64 if agent or project else k)
        rows = self._rows(h.id for h in hits)
        out = []
        for h in hits:
            r = rows.get(h.id)
            if r and (not agent or r["agent"] == agent) and (not project or r["project"] == project):
                out.append({**r, "score": round(1 - h.distance, 3)})
        return out[:k]

    def recent(self, n: int = 20, kind: str = "", agent: str = "", project: str = "") -> list[dict]:
        sql, args = "SELECT * FROM memories WHERE 1=1", []
        for col, val in (("kind", kind), ("agent", agent), ("project", project)):
            if val:
                sql += f" AND {col} = ?"
                args.append(val)
        rows = self.db.execute(sql + " ORDER BY id DESC LIMIT ?", [*args, max(1, min(int(n), 500))])
        return [dict(r) for r in rows]

    def forget(self, id: int) -> dict:
        id = int(id)
        with self.lock:
            row = self._rows([id]).get(id)
            if not row:
                raise KeyError(f"no memory #{id}")
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute("DELETE FROM memories WHERE id = ?", (id,))
                self.vectors.delete(id)
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        return row

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
        count = lambda col: dict(self.db.execute(f"SELECT {col}, COUNT(*) FROM memories GROUP BY {col}").fetchall())
        s = self.vectors.stats()
        return {
            "memories": s.live_rows,
            "by_agent": count("agent"),
            "by_kind": count("kind"),
            "model": MODEL,
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
