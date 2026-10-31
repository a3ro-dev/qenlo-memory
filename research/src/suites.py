"""the three locked eval suites as plain data: stores of (id, text, created_ms) and queries with gold units."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
EVID = re.compile(r"D\d+:\d+")


@dataclass
class Query:
    text: str
    gold: set[str]  # gold units (evidence ids or session ids)
    category: str
    when: int  # ms, when the question is asked


@dataclass
class Haystack:
    name: str
    docs: list[tuple[int, str, int]]  # id, text, created ms
    unit: dict[int, str | tuple]  # doc id -> unit(s) it covers
    queries: list[Query] = field(default_factory=list)


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def locomo_date(s: str) -> int:
    return ms(datetime.strptime(s.strip(), "%I:%M %p on %d %B, %Y"))


def locomo(kind: str) -> list[Haystack]:
    out = []
    for conv in json.loads((RAW / "locomo10.json").read_text(encoding="utf-8")):
        c, h = conv["conversation"], Haystack(conv["sample_id"], [], {})
        sessions = sorted(int(k.split("_")[1]) for k in c if re.fullmatch(r"session_\d+", k))
        last = 0
        for n in sessions:
            when = locomo_date(c[f"session_{n}_date_time"])
            last = max(last, when)
            if kind == "turns":
                for i, t in enumerate(c[f"session_{n}"]):
                    text = f"{t['speaker']}: {t['text']}"
                    if t.get("blip_caption"):
                        text += f" [shares a photo: {t['blip_caption']}]"
                    h.docs.append((len(h.docs) + 1, text, when + i * 1000))
                    h.unit[len(h.docs)] = (t["dia_id"],)
            else:
                for facts in conv["observation"].get(f"session_{n}_observation", {}).values():
                    for i, (text, ev) in enumerate(facts):
                        h.docs.append((len(h.docs) + 1, text, when + i * 1000))
                        h.unit[len(h.docs)] = tuple(EVID.findall(str(ev)))
        for q in conv["qa"]:
            gold = {e for ev in q.get("evidence", []) for e in EVID.findall(ev)}
            if gold:
                h.queries.append(Query(q["question"], gold, f"c{q['category']}", last + 86_400_000))
        out.append(h)
    return out


def lme() -> list[Haystack]:
    out = []
    for q in json.loads((RAW / "longmemeval_s.json").read_text(encoding="utf-8")):
        if q["question_id"].endswith("_abs") or q["question_type"] == "single-session-assistant":
            continue
        h = Haystack(q["question_id"], [], {})
        for sid, date, session in zip(q["haystack_session_ids"], q["haystack_dates"], q["haystack_sessions"]):
            when = ms(datetime.strptime(date, "%Y/%m/%d (%a) %H:%M"))
            for i, t in enumerate(session):
                if t["role"] == "user" and t["content"].strip():
                    h.docs.append((len(h.docs) + 1, t["content"], when + i * 1000))
                    h.unit[len(h.docs)] = (sid,)
        when = ms(datetime.strptime(q["question_date"], "%Y/%m/%d (%a) %H:%M"))
        h.queries.append(Query(q["question"], set(q["answer_session_ids"]), q["question_type"], when))
        out.append(h)
    return out


SUITES = {"locomo-obs": lambda: locomo("obs"), "locomo-turns": lambda: locomo("turns"), "lme-s": lme}
