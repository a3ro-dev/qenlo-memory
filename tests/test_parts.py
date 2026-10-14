"""the parts that don't need ollama: the pet's sprites and vitals, and which model is active."""

from qenlo_memory import models, pet


def test_sprites_keep_their_shape():
    for mood in ("", "blink", "eat", "sleep", "busy"):
        for step in (0, 1):
            rows = pet.frame(mood, step)
            assert len(rows) == 11 and {len(r) for r in rows} == {12}
            assert set("".join(rows)) <= set(".#*o-m")
    assert "o" not in "".join(pet.frame("sleep", 0)) and "mmmm" in "".join(pet.frame("eat", 0))


def test_nvidia_smi_line():
    card = pet.parse_nvidia("NVIDIA GeForce RTX 4050 Laptop GPU, 23, 1946, 6141, 52\n")
    assert card == {"name": "RTX 4050 laptop", "util": 23, "used": 1946 / 1024, "total": 6141 / 1024, "temp": 52}
    assert pet.parse_nvidia("") is None and pet.parse_nvidia("[N/A], x, 1, 2, 3") is None


def test_vitals():
    assert pet.vitals(None, None, 0)[0] == ("daemon", "asleep (no agent has called yet)")
    stats = {"memories": 5, "by_agent": {"codex": 2, "claude-code": 3}, "last_hour": 2, "last_day": 5,
             "model": "snowflake-arctic-embed:22m", "dim": 384, "embed_device": "gpu", "last_search_ran_on": "Cpu",
             "wal_files": 3, "last": {"agent": "codex", "kind": "long_term", "created": 1_000_000}}
    rows = dict(pet.vitals(stats, None, 1_000 + 180))
    assert rows["memories"] == "5 · 2 agents" and rows["ingest"] == "+2/h · +5/day"
    assert rows["search"] == "cpu · wal 3" and rows["last"] == "codex · long term · 3m ago"


def test_active_model(monkeypatch, tmp_path):
    monkeypatch.setattr(models, "CONFIG", tmp_path / "config.json")
    monkeypatch.delenv("QENLO_MEMORY_MODEL", raising=False)
    monkeypatch.delenv("QENLO_MEMORY_QUERY_PREFIX", raising=False)
    assert models.active().name == models.DEFAULT
    models.save(model="nomic-embed-text")
    assert models.active().doc == "search_document: " and models.config()["model"] == "nomic-embed-text"
    monkeypatch.setenv("QENLO_MEMORY_MODEL", "some/custom:tag")
    assert models.active().note == "custom" and models.active().query == ""
