"""one end-to-end check of the store: provenance, cross-agent recall, dedup, forget, restart, lock, repair."""

import pytest
from qenlo import QenloError

from qenlo_memory.store import COMPACT_EVERY, Embedder, Store, redact


@pytest.fixture(scope="module")
def embed():
    return Embedder()


def test_store(tmp_path, embed):
    s = Store(tmp_path, embed)
    a = s.remember("the user's laptop GPU is an RTX 4050 with 6GB of VRAM", "semantic", "claude-code", "qenloDB")
    b = s.remember("release qenlo: tag the commit, then run sdk-release.yml, then verify SHA256SUMS", "procedural", "codex", "qenloDB")
    c = s.remember("the user prefers lower-case prose", "long_term", "cursor")
    assert (a["id"], b["id"], c["id"]) == (1, 2, 3)
    profile = tmp_path / "brain" / "profile.md"
    assert "by cursor" in profile.read_text(encoding="utf-8") and "RTX" not in profile.read_text(encoding="utf-8")

    # any agent reads every agent's memories, and each hit says who wrote it
    hit = s.recall("what graphics card does the user have", k=1)[0]
    assert hit["id"] == 1 and hit["agent"] == "claude-code"
    assert s.recall("how do i ship a new version", kind="procedural", k=1)[0]["agent"] == "codex"
    assert [m["id"] for m in s.recall("gpu", agent="codex")] == [2]

    # the same fact from another agent is not stored twice
    again = s.remember("the user's laptop GPU is an RTX 4050 with 6GB of VRAM", "semantic", "codex", "qenloDB")
    assert again["duplicate"] and again["id"] == 1 and again["agent"] == "claude-code"
    assert "duplicate" not in s.remember("the user's laptop GPU is an RTX 4060 with 8GB of VRAM", "semantic", "codex")

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
    assert [m["id"] for m in s.recent()] == [4, 3, 1]
    assert s.remember("episodic event after restart", "episodic", "gemini-cli")["id"] == 5

    # a row qenlo never saw (crash between the two commits) is repaired on the next start
    s.db.execute("INSERT INTO memories(kind, text, agent, project, created) VALUES ('semantic', 'orphan fact about rust', 'x', '', 0)")
    s.close()
    s = Store(tmp_path, embed)
    assert s.vectors.stats().live_rows == 5
    assert s.recall("orphan fact about rust", k=1)[0]["text"] == "orphan fact about rust"
    s.forget(3)  # forgetting a long-term memory takes it out of the profile too
    assert "lower-case" not in profile.read_text(encoding="utf-8")
    s.close()


def test_wal_compacts(tmp_path, embed):
    s = Store(tmp_path, embed)
    for i in range(COMPACT_EVERY + 5):
        s.remember(f"distinct fact number {i}", "semantic", "codex")
    assert len(list(s.path.glob("wal-*.qwal"))) < COMPACT_EVERY
    s.close()
    s = Store(tmp_path, embed)  # startup flush folds whatever is left
    assert not list(s.path.glob("wal-*.qwal")) and s.vectors.stats().live_rows == COMPACT_EVERY + 5
    s.close()


def test_redact():
    assert "sk-" not in redact("key is sk-abcdefghijklmnopqrstuv ok")
    assert redact("password: hunter22") == "password: [redacted]"
    assert redact("the token budget is small") == "the token budget is small"
    for leak in ("DB_PASSWORD=Tr0ub4dor&3xyz", "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCY", '{"password": "Tr0ub4dor&3xyz"}'):
        assert "Tr0ub4dor" not in redact(leak) and "wJalr" not in redact(leak), leak
    for fine in ("run the task-runner-integration-tests first", "max_tokens=100000", "tokenizer: bpe"):
        assert redact(fine) == fine, fine


def test_link(tmp_path):
    from qenlo_memory.install import link

    brain = tmp_path / "brain" / "skill"
    brain.mkdir(parents=True)
    (brain / "SKILL.md").write_text("new", encoding="utf-8")
    old = tmp_path / "agent" / "skills" / "qenlo-memory"  # a copy from an older install
    old.mkdir(parents=True)
    (old / "SKILL.md").write_text("old", encoding="utf-8")
    assert link(old, brain, False) == "wrote" and (old / "SKILL.md").read_text(encoding="utf-8") == "new"
    assert link(old, brain, False) == "ok"
    (brain / "SKILL.md").write_text("edited once, seen everywhere", encoding="utf-8")
    assert (old / "SKILL.md").read_text(encoding="utf-8") == "edited once, seen everywhere"

    mine = tmp_path / "agent" / "skills" / "users-own"
    mine.mkdir()
    (mine / "notes.txt").write_text("keep", encoding="utf-8")
    assert "aren't ours" in link(mine, brain, False) and (mine / "notes.txt").exists()
