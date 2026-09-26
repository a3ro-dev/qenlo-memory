"""wire qenlo-memory into every coding agent on this machine. each agent is one row of data.

only touches agents whose config dir already exists. backs every edited file up to <file>.bak once.
safe to run again: every edit replaces its own entry and leaves the rest of the file alone.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

H = Path.home()
APPDATA = H / "AppData" / "Roaming"
NAME = "qenlo-memory"
MARK = "<!-- qenlo-memory -->"

RULE = f"""{MARK}
## shared memory (qenlo-memory)
you share one long-term memory with every other coding agent the user runs (claude code, codex, cursor,
antigravity, gemini, and others), through the `qenlo-memory` MCP server.
- before real work, call `recall` with what you're about to do. other agents may have already learned it.
- when you learn something that should outlive this session, call `remember` with the right kind:
  semantic (a fact), procedural (how to do something here), episodic (what happened, a decision or a fix),
  long_term (a durable fact about the user). one self-contained fact per call, and pass `project`.
- every memory says which agent wrote it. trust them like your own notes, and `forget` ones that are wrong.
- never store secrets, tokens, or passwords.
{MARK}
"""

# (agent, file, json path to the server map, entry style). style decides the entry shape.
MCP = [
    ("claude-code", H / ".claude.json", ["mcpServers"], "stdio"),
    ("cursor", H / ".cursor" / "mcp.json", ["mcpServers"], "plain"),
    ("antigravity", H / ".gemini" / "config" / "mcp_config.json", ["mcpServers"], "plain"),
    ("gemini-cli", H / ".gemini" / "settings.json", ["mcpServers"], "plain"),
    ("opencode", H / ".config" / "opencode" / "opencode.jsonc", ["mcp"], "opencode"),
    ("kiro", H / ".kiro" / "settings" / "mcp.json", ["mcpServers"], "plain"),
    ("qwen", H / ".qwen" / "settings.json", ["mcpServers"], "plain"),
    ("qoder", H / ".qoder" / "mcp.json", ["mcpServers"], "plain"),
    ("windsurf", H / ".codeium" / "windsurf" / "mcp_config.json", ["mcpServers"], "plain"),
    ("vscode", APPDATA / "Code" / "User" / "mcp.json", ["servers"], "stdio"),
    ("claude-desktop", APPDATA / "Claude" / "claude_desktop_config.json", ["mcpServers"], "plain"),
]

SKILL_DIRS = [
    H / ".claude" / "skills", H / ".agents" / "skills", H / ".codex" / "skills",
    H / ".gemini" / "config" / "skills", H / ".kiro" / "skills", H / ".qoder" / "skills",
    H / ".codeium" / "windsurf" / "skills",
]

# (file, text). files ending .md get the marker block appended or replaced.
RULES = [
    (H / ".claude" / "rules" / "qenlo-memory.md", RULE),
    (H / ".codex" / "AGENTS.md", RULE),
    (H / ".config" / "opencode" / "AGENTS.md", RULE),
    (H / ".gemini" / "GEMINI.md", RULE),
    (H / ".gemini" / "config" / "rules" / "qenlo-memory.md", RULE),
    (H / ".kiro" / "steering" / "qenlo-memory.md", "---\ninclusion: always\n---\n" + RULE),
    (H / ".cursor" / "rules" / "qenlo-memory.mdc", "---\ndescription: shared memory across agents\nalwaysApply: true\n---\n" + RULE),
    (H / ".qwen" / "QWEN.md", RULE),
    (H / ".codeium" / "windsurf" / "memories" / "global_rules.md", RULE),
]


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


def hook_cmd(cmd: list[str], event: str, agent: str) -> str:
    return " ".join(f'"{c}"' for c in cmd) + f" hook {event} --agent {agent}"


def edit_json(path: Path, change, dry: bool) -> str:
    """load, apply change(data), write back. refuses files it can't parse instead of guessing."""
    target = path.resolve()  # antigravity's legacy path is a symlink; always write the real file
    try:
        data = json.loads(target.read_text(encoding="utf-8")) if target.exists() and target.stat().st_size else {}
    except json.JSONDecodeError:
        return f"skipped {path} (has comments or is not plain json, add the entry by hand)"
    before = json.dumps(data, sort_keys=True)
    change(data)
    if json.dumps(data, sort_keys=True) == before:
        return f"ok      {path}"
    if not dry:
        target.parent.mkdir(parents=True, exist_ok=True)
        bak = target.with_name(target.name + ".bak")
        if target.exists() and not bak.exists():
            shutil.copy2(target, bak)
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return f"wrote   {path}"


def put(data: dict, keys: list[str], name: str, value) -> None:
    for k in keys:
        data = data.setdefault(k, {})
    data[name] = value


def claude_hooks(cmd):
    def change(d):
        hooks = d.setdefault("hooks", {})
        for event, kind in (("SessionStart", "start"), ("UserPromptSubmit", "prompt")):
            groups = [g for g in hooks.get(event, []) if NAME not in json.dumps(g)]
            groups.append({"hooks": [{"type": "command", "command": hook_cmd(cmd, kind, "claude-code"), "timeout": 60}]})
            hooks[event] = groups
    return change


def gemini_hooks(cmd):
    def change(d):
        hooks = d.setdefault("hooks", {})
        for event, kind in (("SessionStart", "start"), ("BeforeAgent", "prompt")):
            groups = [g for g in hooks.get(event, []) if NAME not in json.dumps(g)]
            groups.append({"hooks": [{"name": NAME, "type": "command", "command": hook_cmd(cmd, kind, "gemini-cli"), "timeout": 60000}]})
            hooks[event] = groups
    return change


def codex_hooks(cmd):
    def change(d):
        hooks = d.setdefault("hooks", {})
        for event, kind in (("SessionStart", "start"), ("UserPromptSubmit", "prompt")):
            groups = [g for g in hooks.get(event, []) if NAME not in json.dumps(g)]
            groups.append({"hooks": [{"type": "command", "command": hook_cmd(cmd, kind, "codex"), "timeout": 60}]})
            hooks[event] = groups
    return change


def cursor_hooks(cmd):
    def change(d):
        d.setdefault("version", 1)
        hooks = d.setdefault("hooks", {})
        for event, kind in (("sessionStart", "start"), ("beforeSubmitPrompt", "prompt")):
            hooks[event] = [h for h in hooks.get(event, []) if NAME not in json.dumps(h)]
            hooks[event].append({"command": hook_cmd(cmd, kind, "cursor"), "timeout": 60})
    return change


def codex_mcp(cmd: list[str], dry: bool) -> str:
    path = H / ".codex" / "config.toml"
    if not path.parent.exists():
        return ""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if f"[mcp_servers.{NAME}]" in text:
        return f"ok      {path}"
    block = (f"\n[mcp_servers.{NAME}]\ncommand = '{cmd[0]}'\nargs = {json.dumps([*cmd[1:], 'mcp'])}\n"
             "startup_timeout_sec = 60\ntool_timeout_sec = 300\n")
    if not dry:
        bak = path.with_name(path.name + ".bak")
        if path.exists() and not bak.exists():
            shutil.copy2(path, bak)
        with path.open("a", encoding="utf-8") as f:
            f.write(block)
    return f"wrote   {path}"


def claude_cli(cmd: list[str], dry: bool) -> str:
    """~/.claude.json is rewritten by every running claude code session, so let claude edit it."""
    if not dry:
        claude = shutil.which("claude")
        subprocess.run([claude, "mcp", "remove", "--scope", "user", NAME], capture_output=True)
        done = subprocess.run([claude, "mcp", "add-json", "--scope", "user", NAME, json.dumps(entry("stdio", cmd))],
                              capture_output=True, text=True)
        if done.returncode:
            return f"failed  claude mcp add-json: {done.stderr.strip()}"
    return "wrote   ~/.claude.json (via claude mcp add-json)"


def write_rule(path: Path, text: str, dry: bool) -> str:
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if MARK in old:
        head, _, rest = old.partition(MARK)
        _, _, tail = rest.partition(MARK)
        new = head + text.strip() + tail
    elif NAME in (path.stem, path.parent.name):  # a file that is entirely ours
        new = text
    else:
        new = old + ("\n\n" if old.strip() else "") + text
    if new == old:
        return f"ok      {path}"
    if not dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")
    return f"wrote   {path}"


def install(dry_run: bool = False) -> None:
    cmd = exe()
    skill = resources.files("qenlo_memory").joinpath("SKILL.md").read_text(encoding="utf-8")
    report = []
    for agent, path, keys, style in MCP:
        if agent == "claude-code" and shutil.which("claude"):
            report.append(claude_cli(cmd, dry_run))
        elif path.parent.exists() or agent == "kiro" and (H / ".kiro").exists():  # kiro makes settings/ lazily
            report.append(edit_json(path, lambda d, k=keys, s=style: put(d, k, NAME, entry(s, cmd)), dry_run))
    report.append(codex_mcp(cmd, dry_run))
    for d in SKILL_DIRS:
        if d.parent.exists():
            report.append(write_rule(d / NAME / "SKILL.md", skill, dry_run))
    for path, text in RULES:
        if path.parent.parent.exists():
            report.append(write_rule(path, text, dry_run))
    for path, change in (
        (H / ".claude" / "settings.json", claude_hooks(cmd)),
        (H / ".gemini" / "settings.json", gemini_hooks(cmd)),
        (H / ".codex" / "hooks.json", codex_hooks(cmd)),
        (H / ".cursor" / "hooks.json", cursor_hooks(cmd)),
    ):
        if path.parent.exists():
            report.append(edit_json(path, change, dry_run))
    print(("dry run, nothing written\n" if dry_run else "") + "\n".join(r for r in report if r))
    print("\nrestart your agents so they pick up the new MCP server, skill and hooks.")
