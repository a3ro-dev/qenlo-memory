"""wire qenlo-memory into every coding agent on this machine, and make it look like qenlo while doing it.

one brain folder (~/.qenlo-memory/brain) holds the rules, the skill and the user's profile. agents get
symlinks into it, so editing the brain changes every agent at once. agents run the qenlo-memory CLI from
their shell; the MCP server is opt-in (--mcp) for agents without one.

only touches agents whose config dir already exists. backs every edited file up to <file>.bak once.
safe to run again: every edit replaces its own entry and leaves the rest of the file alone.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .cli import HOME

H = Path.home()
if os.name == "nt":
    APP = Path(os.environ.get("APPDATA", H / "AppData" / "Roaming"))
elif sys.platform == "darwin":
    APP = H / "Library" / "Application Support"
else:
    APP = H / ".config"
NAME = "qenlo-memory"
MARK = "<!-- qenlo-memory -->"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
BRAIN = HOME / "brain"

RULE = f"""## shared memory (qenlo-memory)
you share one long-term memory with every other coding agent the user runs (claude code, codex, cursor,
antigravity, gemini, and others), through the `qenlo-memory` command in your shell.
- before real work, run `qenlo-memory recall "<what you're about to do>" --project <repo>`. other agents may have already learned it.
- when you learn something that should outlive this session, run
  `qenlo-memory remember "<one self-contained fact>" --kind <kind> --project <repo>` with the right kind:
  semantic (a fact), procedural (how to do something here), episodic (what happened, a decision or a fix),
  long_term (a durable fact about the user). leave --project off if it applies everywhere.
- `qenlo-memory recent -n 10` shows what was happening lately, `qenlo-memory forget <id>` drops a wrong memory.
- every memory says which agent wrote it. yours is detected from your shell; if it shows up as `cli`,
  pass `--agent <your harness>` (claude-code, codex, cursor, gemini-cli, antigravity, ...).
- trust other agents' memories like your own notes. the user's long-term profile is {(BRAIN / "profile.md").as_posix()}.
- if you have `qenlo-memory` MCP tools (recall, remember, recent, forget), they do the same thing.
- never store secrets, tokens, or passwords.
"""

# files the user also writes to (AGENTS.md, GEMINI.md...) get a short block pointing at the brain.
POINTER = f"""{MARK}
## shared memory (qenlo-memory)
you share one long-term memory with every coding agent the user runs. before real work run
`qenlo-memory recall "<what you're about to do>" --project <repo>`, and save what should outlive this session with
`qenlo-memory remember "<one fact>" --kind semantic|procedural|episodic|long_term --project <repo>`. never store secrets.
full rules: {(BRAIN / "RULES.md").as_posix()} · the user's profile: {(BRAIN / "profile.md").as_posix()}
{MARK}
"""

# --- looks. qenlo's accent is #B53C2F; status never relies on color alone. ---
COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
if COLOR and os.name == "nt":
    os.system("")  # switches the classic windows console into ANSI mode
RED, DIM, GREEN, BOLD = "38;2;181;60;47", "38;2;110;116;112", "38;2;106;153;78", "1"


def paint(text: str, code: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if COLOR else text


WORDMARK = {  # dot-matrix letters, like the qenlo landing page. 5 rows each
    "q": [".###", "#..#", "#..#", ".###", "...#"],
    "e": [".##.", "#..#", "####", "#...", ".###"],
    "n": ["###.", "#..#", "#..#", "#..#", "#..#"],
    "l": ["#.", "#.", "#.", "#.", ".#"],
    "o": [".##.", "#..#", "#..#", "#..#", ".##."],
}


def banner() -> None:
    print()
    for row in range(5):
        cells = " ".join(WORDMARK[c][row] for c in "qenlo")
        print("  " + "".join(paint("● ", RED) if ch == "#" else paint("· ", DIM) if ch == "." else "  " for ch in cells))
    print(f"\n  {paint('memory', BOLD)}  {paint('one memory for every coding agent you use', DIM)}\n")


def say(mark: str, label: str, detail: str = "") -> None:
    symbol = {"ok": paint("✓", GREEN), "skip": paint("–", DIM), "bad": paint("✗", RED + ";1")}[mark]
    print(f"  {symbol} {label:<15} {detail if mark == 'bad' else paint(detail, DIM)}")


# --- edits. each returns "wrote", "ok" (already there) or a problem string. ---
def exe() -> list[str]:
    found = shutil.which(NAME)
    if found:
        return [Path(found).resolve().as_posix()]
    return [Path(sys.executable).as_posix(), "-m", "qenlo_memory.cli"]


def entry(style: str, cmd: list[str]) -> dict:
    command, args = cmd[0], [*cmd[1:], "mcp"]
    if style == "opencode":
        return {"type": "local", "command": [command, *args], "enabled": True}
    if style == "stdio":
        return {"type": "stdio", "command": command, "args": args, "env": {}}
    return {"command": command, "args": args, "env": {}}


def backup(path: Path) -> None:
    bak = path.with_name(path.name + ".bak")
    if path.exists() and not bak.exists():
        shutil.copy2(path, bak)


def edit_json(path: Path, change, dry: bool) -> str:
    """load, apply change(data), write back. refuses files it can't parse instead of guessing."""
    target = path.resolve()  # antigravity's legacy path is a symlink; always write the real file
    try:
        data = json.loads(target.read_text(encoding="utf-8")) if target.exists() and target.stat().st_size else {}
    except json.JSONDecodeError:
        return f"{path.name} isn't plain json, add the entry by hand"
    before = json.dumps(data, sort_keys=True)
    change(data)
    if json.dumps(data, sort_keys=True) == before:
        return "ok"
    if not dry:
        target.parent.mkdir(parents=True, exist_ok=True)
        backup(target)
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return "wrote"


def write_text(path: Path, text: str, dry: bool) -> str:
    """whole file if it's ours, otherwise a marker block appended once and replaced after that."""
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if NAME in (path.stem, path.parent.name):
        new = text
    elif MARK in old:
        head, _, rest = old.partition(MARK)
        new = head + text.strip() + rest.partition(MARK)[2]
    else:
        new = old + ("\n\n" if old.strip() else "") + text
    if new == old:
        return "ok"
    if not dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        if old:
            backup(path)
        path.write_text(new, encoding="utf-8")
    return "wrote"


def is_link(path: Path) -> bool:
    try:
        os.readlink(path)  # symlinks everywhere, and junctions on windows
        return True
    except (OSError, ValueError):
        return False


def link(path: Path, source: Path, dry: bool) -> str:
    """make path point at source in the brain. a dir we own is replaced; a user's file never is."""
    if os.path.realpath(path) == os.path.realpath(source):
        return "ok"
    copy = path.is_file() and not is_link(path) and source.is_file()
    if copy and path.read_bytes() == source.read_bytes():
        return "ok"  # the copy fallback below, still current
    if path.is_dir() and not is_link(path) and {p.name for p in path.iterdir()} - {"SKILL.md", "SKILL.md.bak"}:
        return f"{path} has files that aren't ours, move it and run again"
    if dry:
        return "wrote"
    path.parent.mkdir(parents=True, exist_ok=True)
    if is_link(path) or path.is_file():
        try:
            path.unlink()
        except OSError:  # a junction
            os.rmdir(path)
    elif path.is_dir():
        shutil.rmtree(path)  # an old copied skill dir, only our SKILL.md (and its .bak) in it
    try:
        os.symlink(source, path, target_is_directory=source.is_dir())
    except OSError:  # windows without developer mode can't symlink
        if source.is_dir():
            done = subprocess.run(["cmd", "/c", "mklink", "/J", str(path), str(source)], capture_output=True, text=True, creationflags=NO_WINDOW)
            if done.returncode:
                return f"couldn't link {path}: {done.stdout.strip() or done.stderr.strip()}"
        else:
            # ponytail: a copy goes stale until the next install. enable developer mode for real links
            shutil.copy2(source, path)
    return "wrote"


def merge(results: list[str]) -> str:
    """several edits that make up one step. reports the first problem, else wrote/ok."""
    return next((r for r in results if r not in ("ok", "wrote")), "wrote" if "wrote" in results else "ok")


def links(source: Path, *paths: Path):
    return lambda dry: merge([link(p, source, dry) for p in paths])


def write_brain(skill_md: str, dry: bool) -> str:
    """the brain is ours outright: overwrite on every install, that's how upgrades reach every agent."""
    status = "ok"
    for path, text in ((BRAIN / "RULES.md", RULE), (BRAIN / "skill" / "SKILL.md", skill_md)):
        if path.exists() and path.read_text(encoding="utf-8") == text:
            continue
        status = "wrote"
        if not dry:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    return status


def mcp_json(path: Path, keys: list[str], style: str, cmd, on: bool = True):
    """on: our server entry under keys. off: take ours out again, touching nothing else."""
    def change(data):
        for k in keys:
            if not on and k not in data:
                return
            data = data.setdefault(k, {})
        if on:
            data[NAME] = entry(style, cmd)
        else:
            data.pop(NAME, None)
    return lambda dry: edit_json(path, change, dry) if on or path.exists() else "ok"


def claude_mcp(cmd, on: bool):
    """~/.claude.json is rewritten by every running claude code session, so let claude edit it."""
    def run(dry):
        claude = shutil.which("claude")
        if not claude:
            return mcp_json(H / ".claude.json", ["mcpServers"], "stdio", cmd, on)(dry)
        if not on:
            listed = subprocess.run([claude, "mcp", "get", NAME], capture_output=True, text=True, creationflags=NO_WINDOW)
            if listed.returncode:
                return "ok"
        if not dry:
            subprocess.run([claude, "mcp", "remove", "--scope", "user", NAME], capture_output=True, creationflags=NO_WINDOW)
            if not on:
                return "wrote"
            done = subprocess.run([claude, "mcp", "add-json", "--scope", "user", NAME, json.dumps(entry("stdio", cmd))],
                                  capture_output=True, text=True, creationflags=NO_WINDOW)
            if done.returncode:
                return f"claude mcp add-json failed: {done.stderr.strip()[:120]}"
        return "wrote"
    return run


def allow(path: Path, keys: list[str], rule: str):
    """let the agent run qenlo-memory in its shell without asking every time."""
    def change(data):
        for k in keys[:-1]:
            data = data.setdefault(k, {})
        rules = data.setdefault(keys[-1], [])
        if rule not in rules:
            rules.append(rule)
    return lambda dry: edit_json(path, change, dry)


def codex_mcp(cmd):
    path = H / ".codex" / "config.toml"

    def run(dry):
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        # drop our previous table (every line up to the next [table] header), then append the current one.
        # "approve" means codex calls these tools without asking, which `codex exec` needs.
        text = re.sub(rf"\n*\[mcp_servers\.{NAME}\]\n(?:(?!\[).*(?:\n|$))*", "\n", old).rstrip("\n")
        block = (f"\n\n[mcp_servers.{NAME}]\ncommand = '{cmd[0]}'\nargs = {json.dumps([*cmd[1:], 'mcp'])}\n"
                 'startup_timeout_sec = 60\ntool_timeout_sec = 300\ndefault_tools_approval_mode = "approve"\n')
        new = (text + block) if text else block.lstrip("\n")
        if new == old:
            return "ok"
        if not dry:
            backup(path)
            path.write_text(new, encoding="utf-8")
        return "wrote"
    return run


def hooks(path: Path, agent: str, events: dict, cmd, style: str):
    """events maps the agent's own event name to our hook. None takes our old entry out of that event."""
    def change(d):
        if style == "cursor":
            d.setdefault("version", 1)
        table = d.setdefault("hooks", {})
        for event, kind in events.items():
            keep = [g for g in table.get(event, []) if NAME not in json.dumps(g)]
            if kind is None:
                table.update({event: keep}) if keep else table.pop(event, None)
                continue
            command = " ".join(f'"{c}"' for c in cmd) + f" hook {kind} --agent {agent}"
            if style == "cursor":
                keep.append({"command": command, "timeout": 60})
            elif style == "gemini":  # gemini counts milliseconds
                keep.append({"hooks": [{"name": NAME, "type": "command", "command": command, "timeout": 60000}]})
            else:
                keep.append({"hooks": [{"type": "command", "command": command, "timeout": 60}]})
            table[event] = keep
    return lambda dry: edit_json(path, change, dry)


def files(*writes):
    return lambda dry: merge([write_text(path, text, dry) for path, text in writes])


def agents(cmd, mcp: bool):
    """mcp: wire the MCP server too. without it our old entries come out, except where the shell can't reach memory."""
    sk = lambda d: d / NAME
    skill = BRAIN / "skill"
    rules = BRAIN / "RULES.md"
    shared = sk(H / ".agents" / "skills")  # read by codex, cursor, gemini cli, opencode and copilot
    agy = H / ".gemini" / "config"
    agy = agy if agy.exists() else H / ".gemini" / "antigravity"
    oc = H / ".config" / "opencode"
    oc_file = next((oc / f for f in ("opencode.jsonc", "opencode.json") if (oc / f).exists()), oc / "opencode.json")
    ws = H / ".codeium" / "windsurf"
    only_mcp = lambda step: {"mcp": step} if mcp else {}  # no shell, so the MCP server is the only way in
    return [
        ("claude code", H / ".claude", {
            "mcp": claude_mcp(cmd, mcp), "skill": links(skill, sk(H / ".claude" / "skills")),
            "rules": links(rules, H / ".claude" / "rules" / "qenlo-memory.md"),
            "allow": allow(H / ".claude" / "settings.json", ["permissions", "allow"], "Bash(qenlo-memory:*)"),
            "hooks": hooks(H / ".claude" / "settings.json", "claude-code", {"SessionStart": "start", "UserPromptSubmit": None}, cmd, "claude")}),
        # codex keeps MCP: its sandbox blocks network by default, and the CLI talks to the daemon over localhost.
        ("codex", H / ".codex", {
            "mcp": codex_mcp(cmd), "skill": links(skill, shared, sk(H / ".codex" / "skills")),
            "rules": files((H / ".codex" / "AGENTS.md", POINTER)),
            "hooks": hooks(H / ".codex" / "hooks.json", "codex", {"SessionStart": "start", "UserPromptSubmit": None}, cmd, "codex")}),
        ("cursor", H / ".cursor", {
            "mcp": mcp_json(H / ".cursor" / "mcp.json", ["mcpServers"], "plain", cmd, mcp), "skill": links(skill, shared),
            "rules": files((H / ".cursor" / "rules" / "qenlo-memory.mdc", "---\ndescription: shared memory across agents\nalwaysApply: true\n---\n" + POINTER)),
            "hooks": hooks(H / ".cursor" / "hooks.json", "cursor", {"sessionStart": "start", "beforeSubmitPrompt": None}, cmd, "cursor")}),
        ("antigravity", agy, {
            "mcp": mcp_json(agy / "mcp_config.json", ["mcpServers"], "plain", cmd, mcp), "skill": links(skill, sk(agy / "skills")),
            "rules": links(rules, agy / "rules" / "qenlo-memory.md")}),
        ("gemini cli", H / ".gemini" / "settings.json", {
            "mcp": mcp_json(H / ".gemini" / "settings.json", ["mcpServers"], "plain", cmd, mcp), "skill": links(skill, shared),
            "rules": files((H / ".gemini" / "GEMINI.md", POINTER)),
            "allow": allow(H / ".gemini" / "settings.json", ["tools", "allowed"], "run_shell_command(qenlo-memory)"),
            "hooks": hooks(H / ".gemini" / "settings.json", "gemini-cli", {"SessionStart": "start", "BeforeAgent": None}, cmd, "gemini")}),
        ("opencode", oc, {
            "mcp": mcp_json(oc_file, ["mcp"], "opencode", cmd, mcp), "skill": links(skill, shared), "rules": files((oc / "AGENTS.md", POINTER))}),
        ("kiro", H / ".kiro", {
            "mcp": mcp_json(H / ".kiro" / "settings" / "mcp.json", ["mcpServers"], "plain", cmd, mcp),
            "skill": links(skill, sk(H / ".kiro" / "skills")),
            "rules": files((H / ".kiro" / "steering" / "qenlo-memory.md", "---\ninclusion: always\n---\n" + POINTER))}),
        ("qwen code", H / ".qwen", {
            "mcp": mcp_json(H / ".qwen" / "settings.json", ["mcpServers"], "plain", cmd, mcp), "rules": files((H / ".qwen" / "QWEN.md", POINTER))}),
        ("windsurf", ws, {
            "mcp": mcp_json(ws / "mcp_config.json", ["mcpServers"], "plain", cmd, mcp), "skill": links(skill, sk(ws / "skills")),
            "rules": files((ws / "memories" / "global_rules.md", POINTER))}),
        ("qoder", H / ".qoder", {
            "mcp": mcp_json(H / ".qoder" / "mcp.json", ["mcpServers"], "plain", cmd, mcp), "skill": links(skill, sk(H / ".qoder" / "skills"))}),
        ("vs code", APP / "Code" / "User", only_mcp(mcp_json(APP / "Code" / "User" / "mcp.json", ["servers"], "stdio", cmd))),
        ("claude desktop", APP / "Claude", only_mcp(mcp_json(APP / "Claude" / "claude_desktop_config.json", ["mcpServers"], "plain", cmd))),
    ]


def preflight() -> tuple[bool, str]:
    """is ollama up, and is the embedding model there (pulled if not)."""
    from .store import MODEL, OLLAMA, ollama

    try:
        version = ollama("/api/version")["version"]
    except RuntimeError:
        how = {"win32": "winget install Ollama.Ollama", "darwin": "brew install ollama, or the app from ollama.com"}
        return False, f"ollama isn't running at {OLLAMA}. install it ({how.get(sys.platform, 'curl -fsSL https://ollama.com/install.sh | sh')}), start it, run this again"
    if not any(m["name"] == MODEL for m in ollama("/api/tags")["models"]):
        print(f"  {paint('…', DIM)} pulling {MODEL}")
        ollama("/api/pull", {"model": MODEL, "stream": False})
    return True, f"ollama {version} · {MODEL}"


def install(dry_run: bool = False, mcp: bool = False) -> None:
    banner()
    ready, detail = preflight()
    say("ok" if ready else "bad", "embeddings", detail)
    cmd = exe()
    here = Path(__file__).parent  # the wheel carries SKILL.md in the package, a source checkout in skills/
    skill_md = next(p for p in (here / "SKILL.md", here.parents[1] / "skills" / NAME / "SKILL.md") if p.exists()).read_text(encoding="utf-8")
    say("ok", "brain", f"{BRAIN.as_posix()}  {write_brain(skill_md, dry_run)}")
    print()
    wired = 0
    for name, home, steps in agents(cmd, mcp):
        if not home.exists():
            say("skip", name, "not installed")
            continue
        if not steps:
            say("skip", name, "no shell, wire it with: qenlo-memory install --mcp")
            continue
        done, problems = [], []
        for part, step in steps.items():
            status = step(dry_run)
            if part == "mcp" and not mcp and name != "codex":  # we were taking our old entry out
                part = "mcp removed" if status == "wrote" else ""
            if part:
                done.append(part) if status in ("ok", "wrote") else problems.append(f"{part}: {status}")
        wired += bool(done)
        say("bad" if problems else "ok", name, " · ".join(done) + ("  " + "; ".join(problems) if problems else ""))
    print()
    if dry_run:
        return print(f"  {paint('dry run, nothing was written', DIM)}\n")
    if ready:
        from .cli import call

        s = call("stats")
        say("ok", "daemon", f"127.0.0.1 · embeddings on {s['embed_device']} · {s['memories']} memories")
    print(f"\n  {paint(f'{wired} agents wired.', BOLD)} restart them, then ask any of them what it remembers.")
    print(f"  {paint('try', DIM)}  qenlo-memory remember \"i prefer small diffs\" --kind long_term\n")
