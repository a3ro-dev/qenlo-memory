# qenlo-memory

i use claude code, codex, cursor, antigravity and gemini cli. each one starts every session knowing nothing, and none of them knows what the others figured out an hour ago. so the same release steps, the same preferences and the same "no, we tried that" get explained again, once per agent.

qenlo-memory is one local memory that all of them share. it's a CLI your agents run from their shell, one brain folder they all link to, and hooks. an MCP server is still there for agents without a shell. every agent can read everything, and every memory records which agent wrote it, so `recall` might hand claude code a fix that codex found yesterday, labeled `by codex`.

it runs on [qenlo](https://github.com/a3ro-dev/qenlo), the embedded vector database i'm building, through its python sdk (`qenlo==0.1.0a11`). building a real product on it was also a way to find out where it hurts.

## install

you need [ollama](https://ollama.com) running. it does the embeddings, on your GPU if you have one.

mac and linux:

```bash
curl -fsSL https://raw.githubusercontent.com/a3ro-dev/qenlo-memory/main/install.sh | sh
```

windows (powershell):

```powershell
irm https://raw.githubusercontent.com/a3ro-dev/qenlo-memory/main/install.ps1 | iex
```

the script installs [uv](https://docs.astral.sh/uv/) if you don't have it, installs the `qenlo-memory` CLI with it (the only dependencies are `qenlo` and `mcp`), pulls the embedding model into ollama, and runs `qenlo-memory install`. that last step writes the brain folder, `~/.qenlo-memory/brain/`, then finds every coding agent on the machine and links the skill and the always-on instruction into it, lets the agent run `qenlo-memory` without asking where it can, and adds hooks where the agent supports them. `install --mcp` also wires the MCP server into every agent. it backs up each file it edits to `<file>.bak` once, and running it again only replaces its own entries. `qenlo-memory install --dry-run` shows what it would touch without writing anything.

qenlo publishes wheels for windows x64, linux x86_64 and apple silicon macs. intel macs and linux arm aren't covered yet.

then restart your agents. run the same line again to upgrade. the upgrade rewrites the brain, and every agent sees it through its links. on windows it stops the running copies of the MCP server first, because windows won't replace files that are in use.

## what it wires up

```
~/.qenlo-memory/brain/
  RULES.md        the always-on instruction
  skill/SKILL.md  the skill
  profile.md      your long-term memories, rewritten whenever one changes
```

| agent | skill (link to brain/skill) | instructions | hooks |
| --- | --- | --- | --- |
| claude code | `~/.claude/skills` | `~/.claude/rules/qenlo-memory.md` (link) | SessionStart, UserPromptSubmit |
| codex (cli, app, ide) | `~/.agents/skills`, `~/.codex/skills` | `~/.codex/AGENTS.md` | `~/.codex/hooks.json` |
| cursor (ide, cursor-agent) | `~/.agents/skills` | `~/.cursor/rules/qenlo-memory.mdc` | `~/.cursor/hooks.json` |
| antigravity (app, ide, `agy`) | `~/.gemini/config/skills` | `~/.gemini/config/rules/qenlo-memory.md` (link) | no |
| gemini cli | `~/.agents/skills` | `~/.gemini/GEMINI.md` | SessionStart, BeforeAgent |
| opencode | `~/.agents/skills` | `~/.config/opencode/AGENTS.md` | no |
| kiro, qwen, qoder, windsurf | where supported | where supported | no |
| vs code, claude desktop | MCP only, with `install --mcp` | | |

files that are only ours are links into the brain. files you write in too (`AGENTS.md`, `GEMINI.md`...) get a short marked block that points at the brain instead, because a link would take the whole file. directories are symlinks, or junctions on windows, which need no admin. file links need developer mode on windows; without it you get a copy that the next install refreshes.

claude code and gemini cli get `qenlo-memory` added to their shell allowlist, so recalls don't ask for permission. codex keeps the MCP server even without `--mcp`: its sandbox blocks network by default, and the CLI reaches the daemon over localhost. running `install` without `--mcp` takes the old MCP entries out of every other agent.

it only touches agents whose config directory already exists.

the hooks do two things. at session start they load your long-term memories and the latest ones for the current project into the agent's context. on every prompt they log what you asked as an episodic memory. agents without hooks get the same behavior from the skill and the instruction, as long as they actually follow it. codex asks you to trust new hooks before it runs them, so open codex once after installing and approve the two qenlo-memory hooks.

## how it works

```
claude code ─┐ shell   qenlo-memory recall/remember   (thin client)
cursor ──────┤ ─────>        │
gemini ──────┤               │
codex ───────┤ stdio   qenlo-memory mcp          (codex, and anything wired with --mcp)
antigravity ─┘ ─────>        │ http, 127.0.0.1:7437, token in ~/.qenlo-memory/token
hooks ────────────>          v
                     qenlo-memory serve          (one daemon, started on first use)
                       ├─ ollama                 embeddings, on the GPU
                       ├─ qenlo collection       vectors + search
                       └─ sqlite                 text, kind, agent, project, time
```

the daemon exists because of how qenlo works. it takes an exclusive process lock on a collection. if every agent launched its own server against the same store, the first one would win and the rest would get `collection is already open by another handle or process`. so there's exactly one owner. everything else is a thin client that starts the daemon if it isn't already running.

attribution comes from the shell. agents set environment variables for the commands they run: `AI_AGENT` is becoming the shared one (claude code sets `claude-code_<version>_agent`), and there are per-harness ones like `CLAUDECODE` and `GEMINI_CLI`. when none match, the memory is written `by cli` and the instruction asks the agent to pass `--agent`. over MCP, the name comes from the handshake instead: every client sends its own name (`codex-mcp-client`, `cursor-vscode`, ...), and the server maps that to a short label.

### four kinds of memory

| kind | what goes in it | example |
| --- | --- | --- |
| `episodic` | things that happened, timestamped. the hooks log every prompt here | "fixed the windows npm dll path, 2026-09-26" |
| `semantic` | facts about you, a project, a tool | "the qenlo python sdk needs `==0.1.0a11`, plain pip skips pre-releases" |
| `procedural` | how to do something | "release: tag, run sdk-release.yml, verify SHA256SUMS" |
| `long_term` | durable facts about you, loaded into every session | "prefers the smallest diff that works" |

each kind is stored in qenlo's `user_id` field. qenlo can only filter on `user_id` and `timestamp`, so kind filters and time filters run inside the vector search itself. agent and project filters over-fetch and then trim.

### the model

`snowflake-arctic-embed:22m` through ollama, pulled automatically on first start. it has 22M parameters and 384 dimensions, it's Apache-2.0, and it takes 39MB of VRAM on my rtx 4050. its model card reports 50.15 NDCG@10 on MTEB retrieval, which is a lot for something that small. set `QENLO_MEMORY_MODEL` to any ollama embedding model. if it isn't an arctic model, also set `QENLO_MEMORY_QUERY_PREFIX=""`. a model with a different dimension needs a fresh `~/.qenlo-memory/vectors.qenlo`.

i started with torch and sentence-transformers in the daemon. that meant a 2GB CUDA download to run a 22M-parameter model, while ollama was already sitting on the GPU. so embeddings go through ollama's local http api, and this package has no ML dependencies at all.

on the GPU: ollama runs the embeddings there when it can. qenlo's collection opens in `automatic` mode, which searches on the GPU through wgpu once more than 4,096 memories match a query and uses the CPU below that. qenlo made that call because moving a small matrix to the GPU costs more than searching it. `qenlo-memory stats` shows where both actually ran.

## numbers

measured on my laptop (rtx 4050, ollama 0.34.4) with 1,000 memories from four agents:

| | p50 | p95 |
| --- | --- | --- |
| `recall`, end to end (embed the query, qenlo search, sqlite join) | 11.7 ms | 18.7 ms |
| qenlo exact search alone | 0.18 ms | |
| `remember`, including the durable WAL write | 23 ms (mean) | |

almost all of a recall is the embedding call to ollama. the vector search is exact, not approximate, and at this size it's a rounding error. these are in-process numbers, and the MCP hop from an agent adds a local http request on top.

## use it yourself

```bash
qenlo-memory recall "how do we release qenlo"
qenlo-memory recent -n 10 --agent codex
qenlo-memory remember "the user's laptop has an rtx 4050" --kind semantic
qenlo-memory forget 12
qenlo-memory stats
qenlo-memory stop
```

## what building it on qenlo taught me

qenlo is alpha, and this project ran into three of its edges. two are design choices, one was a bug.

- records hold only an id, a `user_id`, a timestamp and a vector. there's no payload, so the text and provenance live in sqlite next to the collection and the ids line up.
- deleted ids can never be reused. sqlite's `AUTOINCREMENT` has the same rule, so the two agree for free.
- every write is its own WAL file, and `open` replays them. in alpha.10, `flush()` was supposed to fold them into a snapshot but returned early after every commit, so the WAL only ever grew. building this is how i found it, and [alpha.11](https://github.com/a3ro-dev/qenlo/releases/tag/sdk-v0.1.0-alpha.11) fixes it. qenlo-memory calls `flush()` at startup and every 256 writes. it still rebuilds the collection from sqlite if the two ever disagree, for example after a crash between the two commits.

## limits

- it's local and single-user. the daemon only listens on 127.0.0.1 and wants a token from your home directory, but any process running as you can read your memories.
- the antigravity app and the antigravity ide send the same client name. `agy` is told apart by its parent process. the other two both show up as `antigravity`.
- vs code copilot also reads hooks from `~/.claude/settings.json`, so its prompts get logged as `claude-code`.
- memories written from a shell whose harness sets none of the known variables show up as `by cli` unless the agent passes `--agent`.
- secret redaction is a regex for common key formats. it's a seatbelt, not a guarantee.
- there's no consolidation or decay yet. i'll add them when plain similarity plus recency stops being enough.

## license

Apache-2.0, same as qenlo.
