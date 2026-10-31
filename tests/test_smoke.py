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
    for leak in ("use sudo and pass = Tr0ub4dor/z8j+l", "keep the password as Tr0ub4dor", "DB_PASSWORD=Tr0ub4dor&3xyz","AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCY", '{"password": "Tr0ub4dor&3xyz"}'):
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


def test_update_and_history(tmp_path, embed):
    """a changed fact replaces the old one: the old value stops coming back, but history keeps it."""
    s = Store(tmp_path, embed)
    old = s.remember("the qenlo-memory daemon listens on port 7437", "semantic", "codex", "qenlo-memory")
    new = s.remember("the qenlo-memory daemon listens on port 7438", "semantic", "claude-code", "qenlo-memory")
    assert [m["id"] for m in new["related"]] == [old["id"]]  # the agent is told what may be stale
    again = s.remember("the qenlo-memory daemon listens on port 7438", "semantic", "claude-code", "qenlo-memory", replaces=old["id"])
    assert again["duplicate"] and again["replaced"] == old["id"]  # linking after the fact
    hits = s.recall("which port does the daemon use")
    assert [m["id"] for m in hits] == [new["id"]] and old["id"] not in [m["id"] for m in s.recent()]
    assert [m["id"] for m in s.history(old["id"])] == [old["id"], new["id"]] == [m["id"] for m in s.history(new["id"])]
    third = s.remember("the daemon port moved again, to 7440", "semantic", "cursor", replaces=new["id"])
    assert third["replaced"] == new["id"] and [m["id"] for m in s.history(old["id"])] == [old["id"], new["id"], third["id"]]
    with pytest.raises(KeyError):
        s.remember("anything", replaces=old["id"])  # only a current memory can be replaced
    assert s.stats()["superseded"] == 2 and s.stats()["memories"] == 1
    s.close()
    s = Store(tmp_path, embed)  # restart: superseded memories stay out of both indexes
    assert s.vectors.stats().live_rows == 1 and [m["id"] for m in s.recall("daemon port 7437")] == [third["id"]]
    s.close()


def test_keywords_and_import(tmp_path, embed):
    """names, versions and paths that a small embedding model blurs still come back by keyword."""
    s = Store(tmp_path, embed)
    r = s.remember_many([{"text": f"note {i}: routine status update about the build"} for i in range(30)]
                        + [{"memory": "pin qenlo==0.1.0b2 in pyproject.toml, plain pip skips pre-releases", "kind": "procedural"},
                           {"text": "note 0: routine status update about the build"}])
    assert r == {"added": 31, "skipped": 1}
    assert s.recall("0.1.0b2", k=1)[0]["kind"] == "procedural"
    dump = s.export()
    assert len(dump) == 31 and {"superseded_by", "created", "agent"} <= dump[0].keys()
    s.close()


def test_time_window():
    from datetime import datetime

    from qenlo_memory.store import time_window

    now = int(datetime(2026, 10, 31, 12).timestamp() * 1000)
    day = lambda ms: datetime.fromtimestamp(ms / 1000).date().isoformat()
    assert [day(x) for x in time_window("what did we ship in september", now)] == ["2026-08-29", "2026-10-04"]
    assert [day(x) for x in time_window("the fix from 3 days ago", now)] == ["2026-10-25", "2026-10-31"]
    assert time_window("what did i decide yesterday", now)[1] == now
    for none in ("how do i release qenlo", "the season may change", "how many weeks ago did i start"):
        assert time_window(none, now) is None, none
