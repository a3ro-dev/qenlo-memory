# qenlo-memory

i use claude code, codex, cursor, antigravity and gemini cli. each one starts every session knowing nothing, and none of them knows what the others figured out an hour ago. so the same release steps, the same preferences and the same "no, we tried that" get explained again, once per agent.

qenlo-memory is one local memory that all of them share. it's an MCP server plus a skill. every agent can read everything, and every memory records which agent wrote it, so `recall` might hand claude code a fix that codex found yesterday, labeled `by codex`.

it runs on [qenlo](https://github.com/a3ro-dev/qenlo), the embedded vector database i'm building, through its python sdk (`qenlo==0.1.0a10`). building a real product on it was also a way to find out where it hurts.

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

the script installs [uv](https://docs.astral.sh/uv/) if you don't have it, installs the `qenlo-memory` CLI with it (the only dependencies are `qenlo` and `mcp`), pulls the embedding model into ollama, and runs `qenlo-memory install`. that last step finds every coding agent on the machine and wires in the MCP server, the skill, a short always-on instruction and, where the agent supports them, hooks. it backs up each file it edits to `<file>.bak` once, and running it again only replaces its own entries. `qenlo-memory install --dry-run` shows what it would touch without writing anything.

qenlo publishes wheels for windows x64, linux x86_64 and apple silicon macs. intel macs and linux arm aren't covered yet.

then restart your agents.

## what it wires up

| agent | MCP server | skill | instructions | hooks |
| --- | --- | --- | --- | --- |
| claude code | `~/.claude.json` (via `claude mcp add-json`) | `~/.claude/skills` | `~/.claude/rules/qenlo-memory.md` | SessionStart, UserPromptSubmit |
| codex (cli, app, ide) | `~/.codex/config.toml` | `~/.agents/skills`, `~/.codex/skills` | `~/.codex/AGENTS.md` | `~/.codex/hooks.json` |
| cursor (ide, cursor-agent) | `~/.cursor/mcp.json` | `~/.agents/skills` | `~/.cursor/rules/qenlo-memory.mdc` | `~/.cursor/hooks.json` |
| antigravity (app, ide, `agy`) | `~/.gemini/config/mcp_config.json` | `~/.gemini/config/skills` | `~/.gemini/config/rules/` | no |
| gemini cli | `~/.gemini/settings.json` | `~/.agents/skills` | `~/.gemini/GEMINI.md` | SessionStart, BeforeAgent |
| opencode | `~/.config/opencode/opencode.jsonc` | `~/.agents/skills` | `~/.config/opencode/AGENTS.md` | no |
| kiro, qwen, qoder, windsurf, vs code, claude desktop | their MCP files | where supported | where supported | no |

it only touches agents whose config directory already exists.

the hooks do two things. at session start they load your long-term memories and the latest ones for the current project into the agent's context. on every prompt they log what you asked as an episodic memory. agents without hooks get the same behavior from the skill and the instruction, as long as they actually follow it. codex asks you to trust new hooks before it runs them, so open codex once after installing and approve the two qenlo-memory hooks.

## how it works

```
claude code ─┐
codex ───────┤ stdio   qenlo-memory mcp          (thin, starts fast)
cursor ──────┤ ─────>        │
antigravity ─┤               │ http, 127.0.0.1:7437, token in ~/.qenlo-memory/token
gemini ──────┘               v
hooks ────────────>  qenlo-memory serve          (one daemon, started on first use)
                       ├─ ollama                 embeddings, on the GPU
                       ├─ qenlo collection       vectors + search
                       └─ sqlite                 text, kind, agent, project, time
```

the daemon exists because of how qenlo works. it takes an exclusive process lock on a collection. if every agent launched its own server against the same store, the first one would win and the rest would get `collection is already open by another handle or process`. so there's exactly one owner. everything else is a thin client that starts the daemon if it isn't already running.

attribution comes from the MCP handshake. every client sends its own name (`claude-code`, `codex-mcp-client`, `cursor-vscode`, `gemini-cli-mcp-client`, ...), and the server maps that to a short label. i couldn't use a flag in each agent's config here, because several agents share one config file. cursor ide and cursor-agent read the same `mcp.json`, and codex's cli, desktop app and ide extension read the same `config.toml`.

### four kinds of memory

| kind | what goes in it | example |
| --- | --- | --- |
| `episodic` | things that happened, timestamped. the hooks log every prompt here | "fixed the windows npm dll path, 2026-09-26" |
| `semantic` | facts about you, a project, a tool | "the qenlo python sdk needs `==0.1.0a10`, plain pip skips pre-releases" |
| `procedural` | how to do something | "release: tag, run sdk-release.yml, verify SHA256SUMS" |
| `long_term` | durable facts about you, loaded into every session | "prefers the smallest diff that works" |

each kind is stored in qenlo's `user_id` field. qenlo can only filter on `user_id` and `timestamp`, so kind filters and time filters run inside the vector search itself. agent and project filters over-fetch and then trim.

### the model

`snowflake-arctic-embed:22m` through ollama, pulled automatically on first start. it has 22M parameters and 384 dimensions, it's Apache-2.0, and it takes 39MB of VRAM on my rtx 4050. its model card reports 50.15 NDCG@10 on MTEB retrieval, which is a lot for something that small. set `QENLO_MEMORY_MODEL` to any ollama embedding model. if it isn't an arctic model, also set `QENLO_MEMORY_QUERY_PREFIX=""`. a model with a different dimension needs a fresh `~/.qenlo-memory/vectors.qenlo`.

i started with torch and sentence-transformers in the daemon. that meant a 2GB CUDA download to run a 22M-parameter model, while ollama was already sitting on the GPU. so embeddings go through ollama's local http api, and this package has no ML dependencies at all.

on the GPU: ollama runs the embeddings there when it can. qenlo's collection opens in `automatic` mode, which searches on the GPU through wgpu once more than 4,096 memories match a query and uses the CPU below that. qenlo made that call because moving a small matrix to the GPU costs more than searching it. `qenlo-memory stats` shows where both actually ran.

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

qenlo is alpha, and this project hit three of its rough edges.

- records hold only an id, a `user_id`, a timestamp and a vector. there's no payload, so the text and provenance live in sqlite next to the collection and the ids line up.
- deleted ids can never be reused. sqlite's `AUTOINCREMENT` has the same rule, so the two agree for free.
- every write is its own WAL file, and nothing in the python api compacts them, so opening the collection gets slower forever. qenlo-memory rebuilds the collection from sqlite when there are more than 2,000 WAL files, and also whenever the two stores disagree (after a crash between the two commits, for example). the real fix belongs in qenlo.

## limits

- it's local and single-user. the daemon only listens on 127.0.0.1 and wants a token from your home directory, but any process running as you can read your memories.
- the antigravity app and the antigravity ide send the same client name. `agy` is told apart by its parent process. the other two both show up as `antigravity`.
- vs code copilot also reads hooks from `~/.claude/settings.json`, so its prompts get logged as `claude-code`.
- secret redaction is a regex for common key formats. it's a seatbelt, not a guarantee.
- there's no consolidation or decay yet. i'll add them when plain similarity plus recency stops being enough.

## license

Apache-2.0, same as qenlo.
