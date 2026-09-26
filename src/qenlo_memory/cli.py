"""one daemon owns the store (qenlo allows a single process per collection). everything else talks to it."""

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOME = Path(os.environ.get("QENLO_MEMORY_HOME", Path.home() / ".qenlo-memory"))
PORT = int(os.environ.get("QENLO_MEMORY_PORT", "7437"))
URL = f"http://127.0.0.1:{PORT}"
OPS = {"remember", "recall", "recent", "forget", "stats", "context"}
# a hidden console instead of none: children of a console-less process get a fresh, visible window.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def token() -> str:
    """shared secret in the user's home, so a web page can't talk to the daemon on localhost."""
    path = HOME / "token"
    HOME.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, secrets.token_hex(24).encode())
        os.close(fd)
    except FileExistsError:
        pass
    for _ in range(50):  # another process may have created it a moment ago and not written yet
        value = path.read_text().strip()
        if value:
            return value
        time.sleep(0.02)
    raise RuntimeError(f"empty token file {path}")


def serve() -> None:
    key = token()
    # bind before opening the store, so a second daemon started by a race fails fast on the port.
    # on windows SO_REUSEADDR would let both bind the same port, so turn it off there.
    ThreadingHTTPServer.allow_reuse_address = sys.platform != "win32"
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    from .store import Store

    server.store = Store(HOME)
    server.key = key
    print(f"qenlo-memory listening on {URL}, home {HOME}", flush=True)
    server.serve_forever()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code: int, body) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.reply(200, {"ok": True, "pid": os.getpid()}) if self.path == "/health" else self.reply(404, {})

    def do_POST(self):
        if not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + self.server.key):
            return self.reply(401, {"error": "bad token"})
        op = self.path.strip("/")
        if op not in OPS:
            return self.reply(404, {"error": f"unknown op {op}"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            self.reply(200, getattr(self.server.store, op)(**body))
        except (ValueError, KeyError, TypeError) as e:
            self.reply(400, {"error": str(e).strip("'\"")})
        except Exception as e:  # keep the daemon up, tell the caller what broke
            self.reply(500, {"error": repr(e)})


def alive() -> bool:
    try:
        with urllib.request.urlopen(URL + "/health", timeout=2):
            return True
    except OSError:
        return False


def ensure_daemon(wait: float = 180) -> None:
    if alive():
        return
    HOME.mkdir(parents=True, exist_ok=True)
    log = open(HOME / "daemon.log", "ab")
    windows = sys.platform == "win32"
    flags = NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if windows else 0
    daemon = subprocess.Popen(
        [sys.executable, "-m", "qenlo_memory.cli", "serve"],
        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
        creationflags=flags, start_new_session=not windows, close_fds=True,
    )
    deadline = time.time() + wait  # first start downloads the model, later starts take seconds
    while time.time() < deadline:
        if alive():
            return
        if daemon.poll() is not None:  # it died (ollama down, store locked...), no point waiting
            break
        time.sleep(0.3)
    raise RuntimeError(f"daemon did not come up, see {HOME / 'daemon.log'}")


def call(op: str, **body):
    ensure_daemon()
    request = urllib.request.Request(
        f"{URL}/{op}", json.dumps(body).encode(),
        {"Content-Type": "application/json", "Authorization": "Bearer " + token()},
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(json.load(e).get("error", str(e))) from None


def project_of(cwd: str | os.PathLike | None) -> str:
    path = Path(cwd or os.getcwd()).resolve()
    boring = {Path.home().resolve(), Path(path.anchor)}
    return "" if path in boring or "system32" in str(path).lower() else path.name


# what each client calls itself in the MCP handshake, mapped to the name you'd use.
# several agents share one config file (cursor ide + cursor-agent, codex cli + app), so the handshake
# is the only reliable way to tell who wrote a memory.
CLIENTS = {
    "claude-code": "claude-code", "codex-mcp-client": "codex", "cursor-vscode": "cursor",
    "gemini-cli-mcp-client": "gemini-cli", "antigravity-client": "antigravity", "opencode": "opencode",
    "qwen-code-mcp-client": "qwen", "Visual Studio Code": "vscode-copilot", "Kiro": "kiro", "claude-ai": "claude-desktop",
}


def parent_name() -> str:
    """antigravity's app, ide and agy cli all send the same client name. the parent process tells them apart."""
    if sys.platform != "win32":
        return ""
    out = subprocess.run(["tasklist", "/FI", f"PID eq {os.getppid()}", "/FO", "CSV", "/NH"], capture_output=True, text=True, creationflags=NO_WINDOW)
    return out.stdout.lower()


def show(rows: list[dict]) -> str:
    from .store import line

    return "\n".join(map(line, rows)) or "no memories matched."


def mcp_server(agent: str) -> None:
    from mcp.server.mcpserver import Context, MCPServer

    app = MCPServer("qenlo-memory")
    here = project_of(None)

    def who(ctx: Context) -> str:
        if agent:
            return agent
        try:
            name = ctx.session.client_params.client_info.name or "unknown"
        except AttributeError:
            return "unknown"
        name = CLIENTS.get(name, name.lower().replace(" ", "-"))
        if name == "antigravity" and "agy" in parent_name():
            name = "antigravity-cli"
        return name

    @app.tool()
    def remember(text: str, ctx: Context, kind: str = "semantic", project: str = "") -> str:
        """Save one memory to the user's shared memory (every coding agent they use reads it).
        kind: episodic = something that happened (a decision, a fix, a session outcome),
        semantic = a fact (about the user, a project, a tool), procedural = how to do something
        (commands, workflows, conventions), long_term = a durable core fact about the user that
        every session should start with. project = repo/folder name, '' if it applies everywhere.
        One self-contained fact per call. Never store secrets."""
        r = call("remember", text=text, kind=kind, agent=who(ctx), project=project or here)
        return f"already remembered as #{r['id']} (by {r['agent']})" if r.get("duplicate") else f"remembered #{r['id']}"

    @app.tool()
    def recall(query: str, kind: str = "", agent: str = "", project: str = "", k: int = 8) -> str:
        """Search the user's shared memory by meaning. Returns lines like
        '#id kind | by <agent that wrote it> | time | project | score | text'. Leave filters empty
        to search everything every agent has saved; set agent (e.g. 'codex', 'claude-code') or
        kind or project to narrow it."""
        return show(call("recall", query=query, kind=kind, agent=agent, project=project, k=k))

    @app.tool()
    def recent(n: int = 20, kind: str = "", agent: str = "", project: str = "") -> str:
        """Newest memories first, from every agent unless filtered. Good for 'what was i doing'."""
        return show(call("recent", n=n, kind=kind, agent=agent, project=project))

    @app.tool()
    def forget(id: int) -> str:
        """Delete memory #id for good (text and vector). Use when a memory is wrong or stale."""
        r = call("forget", id=id)
        return f"forgot #{r['id']}: {r['text'][:80]}"

    app.run()


def hook(event: str, agent: str) -> None:
    """agent hooks pass a json payload on stdin. start prints context, prompt logs an episode."""
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    cwd = payload.get("cwd") or (payload.get("workspace_roots") or [None])[0]
    project = project_of(cwd)
    if event == "start":
        text = call("context", project=project)
        if text and agent == "gemini-cli":  # gemini only accepts json on stdout
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
        elif text and agent == "cursor":
            print(json.dumps({"additional_context": text}))
        elif text:
            print(text)
    elif event == "prompt":
        prompt = str(payload.get("prompt") or payload.get("user_prompt") or "").strip()
        if len(prompt) >= 10 and not prompt.startswith("<"):  # "<task-notification>" and friends aren't the user
            call("remember", text="user asked: " + prompt[:2000], kind="episodic", agent=agent, project=project)
        if agent == "cursor":
            print(json.dumps({"continue": True}))


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="qenlo-memory", description="one memory for all your coding agents")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the daemon in the foreground")
    sub.add_parser("mcp", help="stdio MCP server (what agents launch)").add_argument("--agent", default="")
    h = sub.add_parser("hook", help="called by agent hooks")
    h.add_argument("event", choices=["start", "prompt"])
    h.add_argument("--agent", required=True)
    r = sub.add_parser("remember")
    r.add_argument("text")
    r.add_argument("--kind", default="semantic")
    r.add_argument("--project", default="")
    r.add_argument("--agent", default="cli")
    q = sub.add_parser("recall")
    q.add_argument("query")
    q.add_argument("-k", type=int, default=8)
    n = sub.add_parser("recent")
    n.add_argument("-n", type=int, default=20)
    for s in (q, n):
        s.add_argument("--kind", default="")
        s.add_argument("--agent", default="")
        s.add_argument("--project", default="")
    sub.add_parser("forget").add_argument("id", type=int)
    sub.add_parser("stats")
    sub.add_parser("stop", help="stop the daemon")
    i = sub.add_parser("install", help="wire the MCP server, skill and hooks into every agent found")
    i.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)

    if a.cmd == "serve":
        serve()
    elif a.cmd == "mcp":
        mcp_server(a.agent)
    elif a.cmd == "hook":
        try:
            hook(a.event, a.agent)
        except Exception as e:  # a memory hiccup must never block the user's prompt
            print(f"qenlo-memory hook skipped: {e}", file=sys.stderr)
    elif a.cmd == "remember":
        r = call("remember", text=a.text, kind=a.kind, agent=a.agent, project=a.project)
        print(("already remembered as #" if r.get("duplicate") else "remembered #") + str(r["id"]))
    elif a.cmd == "recall":
        print(show(call("recall", query=a.query, kind=a.kind, agent=a.agent, project=a.project, k=a.k)))
    elif a.cmd == "recent":
        print(show(call("recent", n=a.n, kind=a.kind, agent=a.agent, project=a.project)))
    elif a.cmd == "forget":
        print("forgot #" + str(call("forget", id=a.id)["id"]))
    elif a.cmd == "stats":
        print(json.dumps(call("stats"), indent=2))
    elif a.cmd == "stop":
        stop()
    elif a.cmd == "install":
        from .install import install

        install(dry_run=a.dry_run)


def stop() -> None:
    if not alive():
        return print("daemon not running")
    with urllib.request.urlopen(URL + "/health", timeout=2) as r:
        pid = json.load(r)["pid"]
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=NO_WINDOW)
    else:
        os.kill(pid, 15)
    print(f"stopped daemon (pid {pid})")


if __name__ == "__main__":
    main()
