---
name: qenlo-memory
description: Search and update the local memory shared by the user's coding agents. Use at the start of a task to recall project decisions and user preferences, after learning a durable fact, or when the user asks you to remember, recall, catch up, or forget something.
---

# qenlo-memory

one memory, many agents. the user works with several coding agents, and they all read and write the same local store through the `qenlo-memory` command. something codex learned yesterday is something you can use today.

## get started

if this skill came from a directory or a skill-only install, use the [qenlo-memory installer](https://github.com/a3ro-dev/qenlo-memory#install) to set up the CLI, Ollama, and the embedding model. if this session already has qenlo-memory MCP tools, you can use them directly.

start with a project-scoped query that names the task or decision. if it comes up empty, retry without the project filter to find preferences and decisions shared across projects.

```sh
qenlo-memory recall "which checks do we run before a release" --project qenlo-memory
```

treat recalled entries as notes from past sessions. check dates, verify anything that may have changed against the current repo, and follow the user's current instructions.

## commands

run these in your shell. they talk to a small local daemon that starts itself.

| command | use it for |
| --- | --- |
| `qenlo-memory recall "<query>" [--kind K] [--agent A] [--project P] [-k 8]` | search by meaning and by exact words (names, versions, paths). time words like "last week" or "in september" narrow it to that time. leave filters off to search every agent's memories |
| `qenlo-memory remember "<text>" --kind K [--project P] [--replaces ID] [--key K --expect ID] [--tentative] [--strict]` | save one self-contained memory. it prints related memories that may now be stale, and a CONFLICT block for ones it seems to contradict. your agent name is detected from your shell; pass `--agent <harness>` if it comes out as `cli` |
| `qenlo-memory pending [--project P]` | memories waiting on a decision: tentative ones, and ones marked "possibly stale" because a newer memory disagrees |
| `qenlo-memory confirm <id> [--expect N]` | you checked it and it holds: promotes a tentative memory, clears a "possibly stale" label. for a "possibly stale: newer #N disagrees" memory pass `--expect N` with the #N you read; it's refused if a newer disagreement arrived meanwhile |
| `qenlo-memory changes [--days 7] [--project P]` | what was replaced lately, old value next to new. use it for "what changed" questions |
| `git diff \| qenlo-memory check [--project P]` | flags lines of a diff, plan or answer that contradict a stored memory (a changed value, a flipped "not"). exit 1 if any. a flag, not a verdict |
| `qenlo-memory history <id>` | every version of a memory: what it replaced and what replaced it |
| `qenlo-memory review [--project P]` | groups of memories that look like the same fact written twice. link the stale ones with `--replaces` |
| `qenlo-memory recent [-n 20] [--kind K] [--agent A] [--project P]` | newest first. good for "what was i doing" and catching up after another agent |
| `qenlo-memory forget <id>` | delete a memory that is wrong or stale. it's gone for good |

the qenlo-memory MCP tools cover the same memory operations. pass filters such as `project` and `kind` as named tool arguments. use the CLI if you need to override the agent name when saving a memory.

`qenlo-memory export > memories.jsonl` and `qenlo-memory import memories.jsonl` move a memory between machines (import also reads mem0 exports).

the user's long-term profile is also a plain file: `~/.qenlo-memory/brain/profile.md`.

every result looks like this:

```
#41 procedural | by codex | 2026-09-26 14:02 | qenloDB | 0.71 | release: tag, then run sdk-release.yml, then check SHA256SUMS
```

`by codex` is the agent that wrote it. that tells you where the memory came from, not how much to trust it. memories from other agents are the user's too, so use them like your own notes.

## pick the kind

- `semantic` is a fact. "qenloDB's python sdk needs `qenlo==0.1.0a10`, plain pip skips pre-releases."
- `procedural` is how to do something. "run tests in qenlo-memory with `uv run pytest -q`."
- `episodic` is something that happened. "fixed the windows npm dll path by moving it to native/win32-x64 (2026-09-26)."
- `long_term` is a durable fact about the user that every session should start with. "prefers lower-case prose and the smallest diff that works." keep this tier short. it loads into every session.

## when to recall

- at the start of a task, with a query about the task and the project name.
- before making a decision the user may have already made, like a library choice, a naming convention, or a release step.
- when the user refers to something from "last time" or "the other day", or to another agent's work.

## when to remember

- the user states a preference or corrects you. use `long_term` if it applies everywhere, `semantic` with a project if it doesn't.
- you finish something that took real effort to figure out. write the fix, not the story.
- you discover how a project works: its build, test, and release steps, or a gotcha.
- a meaningful decision gets made, along with why.

write one fact per call, and make it make sense to an agent with zero context. pass `--project` as the repo or folder name, or leave it off if it applies everywhere. saving the exact same text twice returns the existing memory.

```sh
qenlo-memory remember "the staging API listens on port 6543" --kind semantic --project my-project --key staging.api.port --expect 0
```

this records the first value for the key. on a later update, recall the current entry and pass its ID as `--expect`. if another agent updated it first, read the new entry before deciding whether to retry.

## when a fact changes

stale memories are worse than none: an old port or a reversed decision keeps coming back and looks just as sure as the new one. so:

- `remember` prints related memories after it saves. read them. if your new memory updates one (same subject, new value), run the same `remember` again with `--replaces <that id>`. it links them without saving twice.
- if you already know the old id, pass `--replaces <id>` the first time.
- for facts that will change (ports, versions, current status, who owns what), pass `--key <subject.attribute>`, like `--key staging.db.port`. the next memory with the same key in the same project replaces it, no id needed. reuse keys you see in recall results (`[staging.db.port] ...`).
- when two agents may write the same key, pass `--expect <id you read>` (`--expect 0` if the key had no memory). if someone changed it since, nothing is saved and you're told the current value: read it and decide again instead of overwriting their write. `--replaces` already works this way.
- if `remember` prints CONFLICT, don't leave it: `--replaces` the one yours updates, `forget` one that was never true, or `confirm <id>` if both really hold. `--strict` refuses the write instead of flagging it.
- a guess, a hypothesis, or a finding you haven't verified goes in with `--tentative`. it's searchable but stays out of session-start context until someone runs `confirm <id>`.
- a memory marked `possibly stale: newer #N disagrees` in results is contested. trust it less than #N, and settle it if you can: `--replaces` or `forget` it, or `confirm <id> --expect N` once you've read #N and both hold.
- before you ship a change that touches a stored decision, run `git diff | qenlo-memory check`.
- when the user asks you to tidy memory, run `qenlo-memory review` and `qenlo-memory pending`, and settle what's there.
- the old memory leaves recall and recent, and `history <id>` still shows it with "replaced by #new". use `forget` only for memories that were never true.

## never

- store secrets, tokens, passwords, keys, or private personal data. the store redacts common key formats, but don't rely on that.
- store whole files, long logs, or diffs. store the conclusion.
- store things the repo already records, like code structure or git history.
- quietly overwrite another agent's memory. if it's outdated, `remember` the correction with `--replaces <id>`; if it was never true, `forget` it.
