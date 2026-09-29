---
name: qenlo-memory
description: Shared long-term memory across every coding agent the user runs (Claude Code, Codex, Cursor, Antigravity, Gemini CLI, opencode, Kiro, and others), stored locally in qenlo. Use at the start of any task to recall what other agents already learned about the user or the project, and whenever you learn something worth keeping past this session. Also use when the user says "remember", "recall", "what do you know about", "what was i doing", or "forget that".
---

# qenlo-memory

one memory, many agents. the user works with several coding agents, and they all read and write the same local store through the `qenlo-memory` command. something codex learned yesterday is something you can use today.

## commands

run these in your shell. they talk to a small local daemon that starts itself.

| command | use it for |
| --- | --- |
| `qenlo-memory recall "<query>" [--kind K] [--agent A] [--project P] [-k 8]` | search by meaning. leave filters off to search every agent's memories |
| `qenlo-memory remember "<text>" --kind K [--project P]` | save one self-contained memory. your agent name is detected from your shell; pass `--agent <harness>` if it comes out as `cli` |
| `qenlo-memory recent [-n 20] [--kind K] [--agent A] [--project P]` | newest first. good for "what was i doing" and catching up after another agent |
| `qenlo-memory forget <id>` | delete a memory that is wrong or stale. it's gone for good |

if you have `qenlo-memory` MCP tools (`recall`, `remember`, `recent`, `forget`), they are the same four with the same arguments. use whichever you have.

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

write one fact per call, and make it make sense to an agent with zero context. pass `--project` as the repo or folder name, or leave it off if it applies everywhere. saving the exact same text twice returns the existing memory. if a fact changed, `forget` the old one.

## never

- store secrets, tokens, passwords, keys, or private personal data. the store redacts common key formats, but don't rely on that.
- store whole files, long logs, or diffs. store the conclusion.
- store things the repo already records, like code structure or git history.
- quietly overwrite another agent's memory. if it's wrong, `forget` it and `remember` the correction.
