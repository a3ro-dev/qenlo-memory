"""the embedding models qenlo-memory knows how to use. all of them run in ollama.

switching models re-embeds every memory from sqlite into a fresh collection on the next daemon start,
because vectors from two models don't share a space (or usually a dimension).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from .cli import HOME

CONFIG = HOME / "config.json"
ARCTIC = "Represent this sentence for searching relevant passages: "


@dataclass(frozen=True)
class Model:
    name: str  # the ollama tag
    dim: int
    size: str  # download size, roughly
    note: str
    query: str = ""  # prefix for search queries
    doc: str = ""  # prefix for stored text


LIBRARY = {m.name: m for m in [
    Model("snowflake-arctic-embed:22m", 384, "46 MB", "default. tiny, fast, english", ARCTIC),
    Model("snowflake-arctic-embed:33m", 384, "67 MB", "a little better, still tiny", ARCTIC),
    Model("snowflake-arctic-embed:110m", 768, "219 MB", "mid-size english", ARCTIC),
    Model("snowflake-arctic-embed:335m", 1024, "669 MB", "best arctic v1, english", ARCTIC),
    Model("snowflake-arctic-embed2", 1024, "1.2 GB", "multilingual arctic", "query: "),
    Model("all-minilm:22m", 384, "46 MB", "classic sentence-transformers"),
    Model("granite-embedding:30m", 384, "63 MB", "ibm, english"),
    Model("nomic-embed-text", 768, "274 MB", "long context (8k tokens)", "search_query: ", "search_document: "),
    Model("mxbai-embed-large", 1024, "670 MB", "strong english retrieval", ARCTIC),
    Model("bge-m3", 1024, "1.2 GB", "multilingual, long context"),
    Model("embeddinggemma", 768, "622 MB", "google, multilingual", "task: search result | query: ", "title: none | text: "),
    Model("qwen3-embedding:0.6b", 1024, "639 MB", "multilingual, instruction-tuned",
          "Instruct: Given a question, retrieve memories that answer it\nQuery: "),
]}
DEFAULT = "snowflake-arctic-embed:22m"


def config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(**changes) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps({**config(), **changes}, indent=2) + "\n", encoding="utf-8")


def active() -> Model:
    """env beats config beats the default. a tag outside the library still works, with no prefixes."""
    name = os.environ.get("QENLO_MEMORY_MODEL") or config().get("model") or DEFAULT
    model = LIBRARY.get(name) or LIBRARY.get(name.removesuffix(":latest")) or Model(name, 0, "?", "custom")
    query = os.environ.get("QENLO_MEMORY_QUERY_PREFIX")
    return model if query is None else Model(model.name, model.dim, model.size, model.note, query, model.doc)
