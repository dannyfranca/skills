# Driver

The driver launches sessions and waits for them. It reads the state of the run from the tracker and the run directory: labels, open and closed tickets, and which files exist. It reads no ticket body and no transcript, and it answers no question, so its context stays small for the full run. Each state has one dispatch in the table below. When a state is not in the table, stop and tell the user.

[`HARNESS.md`](HARNESS.md) says how to launch a session, how to name it, how to wait for it, how to archive it, and how a session wakes the driver.

## Run state

`run.toml` in the run directory is the memory of the driver. Only the driver writes it. Write it before and after each launch, so that a new driver session can continue from it.

```toml
atlas = "<absolute path of the atlas file>"
driver_thread = "<thread id of this driver session, in a harness that has threads>"

[[wave]]
n = 1
nodes = ["<node name>"]
atlas_commit = "<the newest commit of the atlas file when the wave started>"

[[session]]
role = "decide"
target = "<node name | ticket id | wave number>"   # the same target as in the brief
thread = "<thread id>"
attempt = 1
state = "running"   # running | ended
```

## Start

1. Read the config and `run.toml`. A missing `run.toml` is a new run: create it. Write the id of this thread as `driver_thread`: in T3 Code, the capabilities tool gives it as `parentThreadId`. Give this thread the driver name, as the Name part of `HARNESS.md` says.
2. For each session with `state = "running"`, ask the harness for its state. Wait for those that still run. For each of the others, do the part [When a session ends](#when-a-session-ends).
3. When there is no wave, or the newest wave has a `signed-off` file, go to [Next wave](#next-wave). In all other cases, go to [The loop](#the-loop).

## Next wave

1. When the newest wave has a `signed-off` file, a build can have changed the atlas: launch `replan` and wait for it.
2. When the atlas directory is gone, the initiative is complete. Tell the user, and stop.
3. The new wave is each frontier node of the atlas that is in no earlier wave. When there is none, give the user the **build list**, and stop.
4. Add the wave to `run.toml` with its `atlas_commit`, create `wave-<n>/`, and go to the loop.

## The loop

`SKILL.md` says which issue is the map of a node, and which tickets belong to the wave. A map is **charted** when it has the label `navigator:charted`. A ticket is **held** when it has the label `navigator:parked` (it waits for the user) or `navigator:shape` (it waits for the re-plan). A ticket **can start** when it is open, its map is charted, each ticket that blocks it is closed, and it is not held and has no `navigator:draft` label.

Start each pass with the sessions. When a wake line started the turn, the session that sent it can be in its last turn: wait until its run stopped, as `HARNESS.md` says. Then ask the harness for the state of each session with `state = "running"`, and do the part [When a session ends](#when-a-session-ends) for each one whose run stopped. Thus the driver also finds a session that sent no wake line.

A row is true only for a target that no running session has. Then take the first row that is true:

| State | Dispatch |
| --- | --- |
| A node of the wave has no charted map, and no ticket of its map is held | `plan` for each such node |
| A ticket can start | One session for each such ticket: `decide` for `wayfinder:grilling`, `proto` for `wayfinder:prototype`, `research` and `task` for their types. Launch `proto` sessions first. |
| A session is running | Wait for the next session to end |
| A ticket has the label `navigator:shape` | `replan` |
| A ticket has the label `navigator:parked` | `ask` for the wave |
| A node of the wave has no `split.md`, and its map is charted and has no open ticket | `spec` for each such node |
| Each node of the wave has a `split.md`, and the wave has no `signed-off` file | `review` for the wave |
| The wave has a `signed-off` file, and an epic of the wave is open | Give the user the **build list**, and stop. |
| The wave has a `signed-off` file | Go to [Next wave](#next-wave). |

The **build list** is the first part of the message. The published spec of each node is an epic: the `signed-off` files have the links. Use this template, with one line for each open epic. Each epic runs in its own session, so each line has its own command:

```markdown
**Build**: one session for each epic
- [#<n> <epic title>](<epic URL>): <node> · `/<implement skill> #<n>`

Say "launch" to start one 🔨 session for each epic, or build by hand and run `/navigator <atlas path>` after.
```

The implement skill is the one of the tracker: `implement-issues-github` or `implement-tickets-local`.

When the user says launch, launch one `build` session for each open epic, with the `implementation` model and effort. Its target is the epic number. Its brief:

```
/<implement skill> #<n>
When the epic is closed or blocked, send "build #<n> ended" to <driver_thread> with the thread send tool.
```

Keep at most `max_parallel_sessions` sessions running. At that number, wait for the next session to end, and then take the pass again. A session that waits for the user is still running, as [When a session ends](#when-a-session-ends) says.

A `replan` can add, remove, or rename nodes. After each one, write the nodes of the wave again: each frontier node of the atlas that is in no earlier wave.

The table also carries a correction from the wave review. The review opens the tickets that must run again and deletes the splits that depend on them, and then the rows above bring the wave back to `review`.

## When a session ends

The harness says when the run of a session stopped, as `HARNESS.md` says. The time that a session runs says nothing: a session can run for an hour, so the driver never stops a session. A run that stopped has three causes: the session ended, it asks the user a question, or it failed. The completion state of the role tells them apart: the map is charted or one of its tickets is held (`plan`), the ticket is closed or held (`decide`, `research`, `proto`, `task`), `split.md` exists or a ticket of the map is held (`spec`), no ticket of the wave has the label that the session was launched for (`ask`, `replan`), the epic is closed (`build`), the wave has a `signed-off` file, or a ticket of the wave is open, or a node has no `split.md` (`review`).

| Role | Completion state | The session | Do |
| --- | --- | --- | --- |
| All | True | Ended | Set its state to `ended`, and archive its thread as `HARNESS.md` says |
| `ask`, `review`, `build` | False | Waits for the user | Keep its state and its thread as they are, and do the Wake part of `HARNESS.md` |
| All others | False, `attempt = 1` | Failed | Set its state to `ended`, archive its thread, and launch the session one more time with `attempt = 2` |
| All others | False, `attempt = 2` | Failed two times | Set its state to `ended`, and keep its thread active: the user reads it to find the fault. Add the label `navigator:parked` to the ticket, with a comment that says "Blocked: the session failed two times". For a role whose target is not a ticket, stop and tell the user. |

A launch from the loop table is always `attempt = 1`. Thus the active list of the user shows only the threads that need the user.
