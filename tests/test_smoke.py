"""one end-to-end check of the store: provenance, cross-agent recall, dedup, forget, restart, lock, repair."""

import pytest
from qenlo import QenloError

from qenlo_memory.store import Embedder, Store, redact


@pytest.fixture(scope="module")
def embed():
    return Embedder()


def test_store(tmp_path, embed):
    s = Store(tmp_path, embed)
    a = s.remember("the user's laptop GPU is an RTX 4050 with 6GB of VRAM", "semantic", "claude-code", "qenloDB")
    b = s.remember("release qenlo: tag the commit, then run sdk-release.yml, then verify SHA256SUMS", "procedural", "codex", "qenloDB")
    c = s.remember("the user prefers lower-case prose", "long_term", "cursor")
    assert (a["id"], b["id"], c["id"]) == (1, 2, 3)

    # any agent reads every agent's memories, and each hit says who wrote it
    hit = s.recall("what graphics card does the user have", k=1)[0]
    assert hit["id"] == 1 and hit["agent"] == "claude-code"
    assert s.recall("how do i ship a new version", kind="procedural", k=1)[0]["agent"] == "codex"
    assert [m["id"] for m in s.recall("gpu", agent="codex")] == [2]

    # the same fact from another agent is not stored twice
    again = s.remember("the user's laptop GPU is an RTX 4050 with 6GB of VRAM", "semantic", "codex", "qenloDB")
    assert again["duplicate"] and again["id"] == 1 and again["agent"] == "claude-code"

    assert "long-term memory" in s.context("qenloDB") and "by cursor" in s.context("qenloDB")
    assert s.forget(2)["agent"] == "codex"
    with pytest.raises(KeyError):
        s.forget(2)

    # qenlo holds one process lock per collection; a second owner must fail loudly
    with pytest.raises(QenloError, match="already open"):
        Store(tmp_path, embed)
    s.close()

    # restart keeps everything, and ids keep counting up (qenlo never reuses a deleted id)
    s = Store(tmp_path, embed)
    assert [m["id"] for m in s.recent()] == [3, 1]
    assert s.remember("episodic event after restart", "episodic", "gemini-cli")["id"] == 4

    # a row qenlo never saw (crash between the two commits) is repaired on the next start
    s.db.execute("INSERT INTO memories(kind, text, agent, project, created) VALUES ('semantic', 'orphan fact about rust', 'x', '', 0)")
    s.close()
    s = Store(tmp_path, embed)
    assert s.vectors.stats().live_rows == 4
    assert s.recall("orphan fact about rust", k=1)[0]["text"] == "orphan fact about rust"
    s.close()


def test_redact():
    assert "sk-" not in redact("key is sk-abcdefghijklmnopqrstuv ok")
    assert redact("password: hunter22") == "password: [redacted]"
    assert redact("the token budget is small") == "the token budget is small"
