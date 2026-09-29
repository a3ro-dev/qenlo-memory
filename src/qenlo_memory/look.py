"""how qenlo-memory looks in a terminal. piped output, which is what agents read, stays plain lines.

qenlo's accent is #B53C2F; status never relies on color alone.
"""

import os
import re
import shutil
import sys
import textwrap
import time

COLOR = (sys.stdout.isatty() or bool(os.environ.get("FORCE_COLOR"))) and not os.environ.get("NO_COLOR")
if COLOR and os.name == "nt":
    os.system("")  # switches the classic windows console into ANSI mode
RED, DIM, GREEN, BOLD, AMBER = "38;2;181;60;47", "38;2;110;116;112", "38;2;106;153;78", "1", "38;2;204;150;70"
KIND = {"long_term": RED, "procedural": GREEN, "semantic": AMBER, "episodic": DIM}
ANSI = re.compile(r"\x1b\[[0-9;]*m")
MAX_LINES = 4


def paint(text: str, code: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if COLOR else text


def width() -> int:
    return min(shutil.get_terminal_size().columns, 100)


def box(lines: list[str]) -> str:
    """a rounded box around already-painted lines, sized to the widest one."""
    inner = max(len(ANSI.sub("", s)) for s in lines) + 2
    edge = lambda left, right: paint(left + "─" * inner + right, RED)
    rows = [paint("│", RED) + " " + s + " " * (inner - 1 - len(ANSI.sub("", s))) + paint("│", RED) for s in lines]
    return "\n".join([edge("╭", "╮"), *rows, edge("╰", "╯")])


def memory(m: dict) -> str:
    day = time.strftime("%Y-%m-%d %H:%M", time.localtime(m["created"] / 1000))
    score = f"{m['score']:.2f}" if "score" in m else ""
    meta = " · ".join(filter(None, [m["kind"].replace("_", " "), m["agent"], m["project"], day, score]))
    head = f"{paint('●', KIND.get(m['kind'], DIM))} {paint('#' + str(m['id']), BOLD)}  {paint(meta, DIM)}"
    body = textwrap.wrap(" ".join(m["text"].split()), width() - 7) or [""]
    shown = [f"  {paint('⎿', DIM)}  {body[0]}", *(f"     {s}" for s in body[1:MAX_LINES])]
    if len(body) > MAX_LINES:
        shown.append("     " + paint(f"… +{len(body) - MAX_LINES} lines", DIM))
    return "\n".join([head, *shown])


def memories(rows: list[dict]) -> str:
    return "\n\n".join(map(memory, rows)) or paint("  no memories matched.", DIM)


def done(text: str, detail: str = "") -> str:
    return f"{paint('✻', RED)} {text}" + (f"  {paint(detail, DIM)}" if detail else "")


def bars(title: str, counts: dict) -> str:
    top = max(counts.values(), default=1)
    pad = max(map(len, counts), default=0)
    rows = [f"  {name:<{pad}}  {paint('█' * max(1, round(24 * n / top)), KIND.get(name, RED))} {paint(str(n), DIM)}"
            for name, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    return "\n".join([f" {paint(title, BOLD)}", *rows])


def stats(s: dict) -> str:
    home = s["home"].replace(os.path.expanduser("~"), "~").replace("\\", "/")
    head = box([
        f"{paint('✻', RED)} {paint('qenlo-memory', BOLD)}",
        "",
        f"  {s['memories']} memories · {len(s['by_agent'])} agents · embeddings on {s['embed_device']}",
        paint(f"  {s['model']} · {home}", DIM),
    ])
    return "\n\n".join([head, bars("by agent", s["by_agent"]), bars("by kind", s["by_kind"])])


TIPS = [
    ('recall "<query>"', "search every agent's memory by meaning"),
    ('remember "<fact>" --kind K', "save one fact, how-to, decision or preference"),
    ("recent", "what every agent did lately"),
    ("forget <id>", "drop a memory that's wrong"),
    ("stats", "what's in there, and who wrote it"),
]


def welcome(s: dict) -> str:
    head = box([
        f"{paint('✻', RED)} Welcome to {paint('qenlo-memory', BOLD)}",
        "",
        paint("  one memory for every coding agent you use", DIM),
        paint(f"  {s['memories']} memories from {len(s['by_agent'])} agents", DIM),
    ])
    pad = max(len(c) for c, _ in TIPS)
    tips = [f"  {paint('qenlo-memory', DIM)} {c:<{pad}}  {paint(d, DIM)}" for c, d in TIPS]
    return "\n".join([head, "", " Try:", *tips])
