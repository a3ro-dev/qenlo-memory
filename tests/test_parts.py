"""the parts that don't need ollama: which model is active."""

from qenlo_memory import models


def test_active_model(monkeypatch, tmp_path):
    monkeypatch.setattr(models, "CONFIG", tmp_path / "config.json")
    monkeypatch.delenv("QENLO_MEMORY_MODEL", raising=False)
    monkeypatch.delenv("QENLO_MEMORY_QUERY_PREFIX", raising=False)
    assert models.active().name == models.DEFAULT
    models.save(model="nomic-embed-text")
    assert models.active().doc == "search_document: " and models.config()["model"] == "nomic-embed-text"
    monkeypatch.setenv("QENLO_MEMORY_MODEL", "some/custom:tag")
    assert models.active().note == "custom" and models.active().query == ""
