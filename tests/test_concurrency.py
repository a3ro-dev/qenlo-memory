"""many agents writing at once, through the same Store the daemon serves (one thread per request).
no ollama: a hashed bag-of-words embedder stands in, the store doesn't care where vectors come from."""

import hashlib
import math
import threading

import pytest

from qenlo_memory.store import WORD, Conflict, Store


class HashEmbed:
    model, dim, device = "hash-test", 64, "cpu"

    def __call__(self, texts, query=False):
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for w in WORD.findall(t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1
            n = math.sqrt(sum(x * x for x in v)) or 1
            out.append([x / n for x in v])
        return out


@pytest.mark.parametrize("agents,writes", [(8, 25)])
def test_concurrent_writes(tmp_path, agents, writes):
    s = Store(tmp_path, HashEmbed())
    won, lost, errors = [], [], []

    def agent(n):
        for i in range(writes):
            try:
                # unkeyed notes, plus a read-then-write race on one shared key with compare-and-swap
                s.remember(f"agent {n} note {i} about subsystem {i % 7}", agent=f"a{n}", project="stress")
                cur = s.db.execute("SELECT id FROM memories WHERE key = 'shared.counter' AND superseded_by IS NULL").fetchone()
                try:
                    r = s.remember(f"shared counter set by agent {n} step {i}", key="shared.counter", project="stress",
                                   expect=cur["id"] if cur else 0, agent=f"a{n}")
                    won.append(r["id"])
                except Conflict:
                    lost.append(n)
            except Exception as e:  # anything else is a real failure
                errors.append(repr(e))

    threads = [threading.Thread(target=agent, args=(n,)) for n in range(agents)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[:3]
    live = s.db.execute("SELECT COUNT(*) FROM memories WHERE superseded_by IS NULL").fetchone()[0]
    words = s.db.execute("SELECT COUNT(*) FROM words").fetchone()[0]
    assert live == words == s.vectors.stats().live_rows == agents * writes + 1  # every note, one current counter
    # no lost update: every winning counter write is in one unbroken chain, each replacing the one before it
    chain = [m["id"] for m in s.history(won[-1])]
    assert sorted(chain) == sorted(won) and len(won) + len(lost) == agents * writes
    s.close()
    s = Store(tmp_path, HashEmbed())  # and it all survives a restart without a rebuild mismatch
    assert s.vectors.stats().live_rows == agents * writes + 1
    s.close()


def test_group_commit_isolation(tmp_path):
    """one group, three writers: one fails after inserting, one is added and replaced inside the group."""
    s = Store(tmp_path, HashEmbed())
    v = s.embed(["x"])[0]

    def ok():
        return s._insert([("semantic", "kept note", "a", "", 1, None)], [v])[0]

    def fails():
        s._insert([("semantic", "half-written note", "b", "", 1, None)], [v])
        raise Conflict("lost a race after inserting")

    def churn():
        first = s._insert([("semantic", "short-lived note", "c", "", 1, None)], [v])[0]
        return s._insert([("semantic", "its replacement", "c", "", 2, None)], [v], retire=first)[0], first

    jobs = [{"fn": f, "done": threading.Event()} for f in (ok, fails, churn)]
    s._group(jobs)
    assert isinstance(jobs[1]["error"], Conflict) and "error" not in jobs[0] and "error" not in jobs[2]
    texts = {r["text"] for r in s.recent()}
    assert texts == {"kept note", "its replacement"}  # the failed writer left nothing behind
    new, first = jobs[2]["result"]
    assert [m["id"] for m in s.history(new)] == [first, new]
    assert s.vectors.stats().live_rows == 2  # the short-lived row never reached qenlo
    s.close()
    s = Store(tmp_path, HashEmbed())  # nothing to repair on reopen
    assert s.vectors.stats().live_rows == 2 and s.db.execute("SELECT COUNT(*) FROM words")[0][0] == 2
    s.close()
