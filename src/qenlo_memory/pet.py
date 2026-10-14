"""qenlo, the memory pet: a small dot-matrix creature that sits on the desktop while the daemon runs.

it eats when an agent saves a memory, sweats when the GPU is busy, and sleeps when the daemon is down.
double-click shows or hides its vitals, drag moves it, right-click for the menu. stdlib tkinter only.
"""

from __future__ import annotations

import json
import math
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from . import models
from .cli import NO_WINDOW, PORT, URL, token

PET_PORT = PORT + 1  # one pet per user: the second one can't bind this and leaves
RED, GLOW, INK, MOUTH, CARD, DIM, TEXT, GREEN = "#B53C2F", "#E07A6A", "#161212", "#3A0D0A", "#141414", "#7A7F7C", "#E9E4DF", "#6A994E"
KEY = "#010203"  # painted transparent on windows and macos
PANEL = "#1E0B09"  # the dark behind the dots: it makes the gaps solid, so the whole creature can be grabbed
PITCH, DOT = 9, 7
W = 290

# 12 x 11 dots. # body, * shine, o eye, - closed eye, m mouth
BODY = [
    "...######...",
    "..########..",
    ".##*#######.",
    ".#*########.",
    ".##o####o##.",
    ".##o####o##.",
    ".##########.",
    ".####mm####.",
    ".##########.",
    ".##########.",
]
FEET = [".##.##.##.#.", ".#.##.##.##."]


def frame(mood: str, step: int) -> list[str]:
    """the sprite for a mood. step alternates the feet so it looks like it's shuffling."""
    rows = [*BODY, FEET[step % 2]]
    if mood in ("blink", "sleep"):
        rows[4] = ".##########."
        rows[5] = ".##-####-##."
    if mood == "eat":
        rows[7], rows[8] = ".###mmmm###.", ".###mmmm###."
    if mood == "sleep":
        rows[7] = ".##########."
        rows[-1] = FEET[0]
    return rows


def parse_nvidia(text: str) -> dict | None:
    """one csv line from nvidia-smi: name, util %, used MiB, total MiB, temp C."""
    try:
        name, util, used, total, temp = [part.strip() for part in text.strip().splitlines()[0].split(",")]
        name = name.replace("NVIDIA ", "").replace("GeForce ", "").replace(" Laptop GPU", " laptop")
        return {"name": name, "util": int(util),
                "used": int(used) / 1024, "total": int(total) / 1024, "temp": int(temp)}
    except (ValueError, IndexError):
        return None


def gpu() -> dict | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_nvidia(out.stdout) if out.returncode == 0 else None


def ago(ms: int, now: float) -> str:
    seconds = max(0, now - ms / 1000)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return "just now"


def vitals(stats: dict | None, card: dict | None, now: float) -> list[tuple[str, str]]:
    """the lines on the vitals card, as (label, value)."""
    rows = [("daemon", "up · 127.0.0.1" if stats else "asleep (no agent has called yet)")]
    if card:
        rows += [("gpu", f"{card['name']} · {card['util']}%"), ("vram", f"{card['used']:.1f}/{card['total']:.1f} GB · {card['temp']}°C")]
    else:
        rows.append(("gpu", "no nvidia gpu found"))
    if stats:
        rows += [
            ("memories", f"{stats['memories']} · {len(stats['by_agent'])} agents"),
            ("ingest", f"+{stats.get('last_hour', 0)}/h · +{stats.get('last_day', 0)}/day"),
            ("model", f"{stats['model'].split(':')[0]} · {stats.get('dim', '?')}d · {stats['embed_device']}"),
            ("search", f"{str(stats['last_search_ran_on']).lower()} · wal {stats['wal_files']}"),
        ]
        last = stats.get("last")
        if last:
            rows.append(("last", f"{last['agent']} · {last['kind'].replace('_', ' ')} · {ago(last['created'], now)}"))
    return rows


def fetch_stats() -> dict | None:
    """the daemon's stats, or None when it isn't running. never starts it: the pet only watches."""
    request = urllib.request.Request(URL + "/stats", b"{}", {"Content-Type": "application/json", "Authorization": "Bearer " + token()})
    try:
        with urllib.request.urlopen(request, timeout=3) as r:
            return json.load(r)
    except (OSError, ValueError):
        return None


class Pet:
    def __init__(self, guard: socket.socket):
        import tkinter as tk

        self.tk = tk
        self.root = root = tk.Tk()
        root.title("qenlo")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        if sys.platform == "win32":
            root.attributes("-transparentcolor", KEY)
        elif sys.platform == "darwin":
            root.attributes("-transparent", True)
        self.font = ("Consolas", 9) if sys.platform == "win32" else ("Menlo", 10) if sys.platform == "darwin" else ("DejaVu Sans Mono", 9)
        self.canvas = tk.Canvas(root, width=W, height=320, bg=KEY, highlightthickness=0, cursor="fleur")
        self.canvas.pack()
        self.stats, self.card, self.mood_until, self.mood, self.bubble = None, None, 0.0, "", ""
        self.show_card = models.config().get("pet_card", True)
        self.seen = None
        x, y = models.config().get("pet_at") or (root.winfo_screenwidth() - W - 24, root.winfo_screenheight() - 380)
        # a saved spot from a bigger or unplugged monitor would leave it off screen
        x, y = min(max(0, x), root.winfo_screenwidth() - W), min(max(0, y), root.winfo_screenheight() - 120)
        root.geometry(f"+{x}+{y}")
        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_command(label="show / hide vitals", command=self.toggle)
        self.menu.add_command(label="don't start with qenlo-memory", command=lambda: (models.save(pet=False), root.destroy()))
        self.menu.add_command(label="quit", command=root.destroy)
        for event, handler in (("<ButtonPress-1>", self.grab), ("<B1-Motion>", self.drag), ("<ButtonRelease-1>", self.drop),
                               ("<Double-Button-1>", lambda e: self.toggle()), ("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))):
            self.canvas.bind(event, handler)
        threading.Thread(target=self.listen, args=(guard,), daemon=True).start()
        threading.Thread(target=self.watch, daemon=True).start()
        self.start = time.time()
        self.tick()

    def listen(self, guard: socket.socket) -> None:
        """`qenlo-memory pet stop` connects here."""
        self.guard = guard  # held for life: if it's freed, the next start makes a second pet
        while True:
            try:
                connection, _ = guard.accept()
                with connection:
                    if connection.recv(16) == b"quit":
                        self.root.after(0, self.root.destroy)
            except OSError:
                continue  # a reset probe must not kill the thread that keeps the port

    def watch(self) -> None:
        while True:
            stats, card = fetch_stats(), gpu()
            if stats and self.seen is not None and stats["memories"] > self.seen:
                who = (stats.get("last") or {}).get("agent", "someone")
                self.feel("eat", f"nom · +{stats['memories'] - self.seen} from {who}", 2.5)
            elif card and card["util"] >= 40 and time.time() > self.mood_until:
                self.feel("busy", f"crunching · gpu {card['util']}%", 2.0)
            self.seen = stats["memories"] if stats else self.seen
            self.stats, self.card = stats, card
            time.sleep(2)

    def feel(self, mood: str, bubble: str, seconds: float) -> None:
        self.mood, self.bubble, self.mood_until = mood, bubble, time.time() + seconds

    def toggle(self) -> None:
        self.show_card = not self.show_card
        models.save(pet_card=self.show_card)

    def grab(self, event) -> None:
        self.offset = (event.x, event.y)

    def drag(self, event) -> None:
        self.root.geometry(f"+{event.x_root - self.offset[0]}+{event.y_root - self.offset[1]}")

    def drop(self, event) -> None:
        models.save(pet_at=[self.root.winfo_x(), self.root.winfo_y()])

    def tick(self) -> None:
        now, c = time.time(), self.canvas
        t = now - self.start
        c.delete("all")
        awake = self.stats is not None
        mood = self.mood if now < self.mood_until else ""
        if not awake:
            mood = "sleep"
        elif not mood and t % 4.2 < 0.15:
            mood = "blink"
        speed = 9 if mood == "busy" else 3
        bob = 0 if mood == "sleep" else round(3 * math.sin(t * speed / 3))
        left, top = (W - 12 * PITCH) // 2, 34 + bob
        sprite = frame(mood, int(t * (4 if mood == "busy" else 2)))
        for r, row in enumerate(sprite):
            for col, cell in enumerate(row):
                if cell != ".":
                    x, y = left + col * PITCH - 1, top + r * PITCH - 1
                    c.create_rectangle(x, y, x + PITCH, y + PITCH, fill=PANEL, outline="")
        for r, row in enumerate(sprite):
            for col, cell in enumerate(row):
                if cell == ".":
                    continue
                x, y = left + col * PITCH, top + r * PITCH
                fill = {"#": RED, "*": GLOW, "o": INK, "-": INK, "m": MOUTH}[cell]
                if cell == "-":  # a closed eye: a body dot with a lash across it
                    c.create_oval(x, y, x + DOT, y + DOT, fill=RED, outline="")
                    c.create_rectangle(x, y + 3, x + DOT, y + 5, fill=fill, outline="")
                else:
                    c.create_oval(x, y, x + DOT, y + DOT, fill=fill, outline="")
                if cell == "o" and r == 4:  # a glint so it looks alive
                    c.create_oval(x + 1, y + 1, x + 3, y + 3, fill="#FFFFFF", outline="")
        if mood == "busy":  # a sweat drop
            c.create_oval(left + 12 * PITCH, top + 8 + (t * 20) % 12, left + 12 * PITCH + 5, top + 15 + (t * 20) % 12, fill="#7FB7E8", outline="")
        if mood == "sleep":
            for i in range(3):
                phase = (t * 0.6 + i / 3) % 1
                c.create_text(left + 11 * PITCH + 6 + 10 * phase, top - 2 - 22 * phase, text="z", fill=DIM, font=(self.font[0], 8 + 4 * i))
        if mood == "eat":  # crumbs of memory flying in
            for i in range(4):
                phase = (t * 2 + i / 4) % 1
                c.create_oval(left - 18 + 20 * phase, top + 66 - 4 * i, left - 14 + 20 * phase, top + 70 - 4 * i, fill=GLOW, outline="")
        if self.bubble and now < self.mood_until:
            self.speech(self.bubble, top - 30)
        if self.show_card:
            self.draw_card(top - bob + 11 * PITCH + 12, now)
        self.root.after(100, self.tick)

    def speech(self, text: str, y: int) -> None:
        item = self.canvas.create_text(W // 2, y + 11, text=text, fill=TEXT, font=self.font)
        x0, y0, x1, y1 = self.canvas.bbox(item)
        self.rounded(x0 - 8, y0 - 4, x1 + 8, y1 + 4, 8, CARD, RED)
        self.canvas.tag_raise(item)

    def draw_card(self, y: int, now: float) -> None:
        rows = vitals(self.stats, self.card, now)
        line = 15
        self.rounded(4, y, W - 4, y + 30 + line * len(rows), 10, CARD, RED)
        state = GREEN if self.stats else DIM
        self.canvas.create_oval(14, y + 11, 21, y + 18, fill=state, outline="")
        self.canvas.create_text(28, y + 14, text="qenlo-memory", anchor="w", fill=TEXT, font=(self.font[0], self.font[1], "bold"))
        self.canvas.create_text(W - 14, y + 14, text=time.strftime("%H:%M", time.localtime(now)), anchor="e", fill=state, font=self.font)
        for i, (label, value) in enumerate(rows):
            row_y = y + 32 + i * line
            self.canvas.create_text(14, row_y, text=label, anchor="w", fill=DIM, font=self.font)
            value = value if len(value) <= 28 else value[:27] + "…"
            self.canvas.create_text(76, row_y, text=value, anchor="w", fill=TEXT, font=self.font)

    def rounded(self, x0, y0, x1, y1, r, fill, outline) -> None:
        points = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        self.canvas.create_polygon(points, smooth=True, fill=fill, outline=outline)


def guard() -> socket.socket | None:
    sock = socket.socket()
    if sys.platform == "win32":  # without this windows lets a second socket share the port
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        sock.bind(("127.0.0.1", PET_PORT))
    except OSError:
        sock.close()
        return None
    sock.listen()
    return sock


def spawn() -> None:
    """start the pet in the background, without a console window. a second one exits on its own."""
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    windows = sys.platform == "win32"
    subprocess.Popen([str(windowless if windowless.exists() else exe), "-m", "qenlo_memory.pet"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if windows else 0,
                     start_new_session=not windows, close_fds=True)


def stop() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PET_PORT), timeout=2) as sock:
            sock.sendall(b"quit")
        return True
    except OSError:
        return False


def main() -> None:
    sock = guard()
    if sock is None:
        return  # already on the desktop
    Pet(sock).root.mainloop()


if __name__ == "__main__":
    main()
