# Driver

The driver launches sessions and waits for them. It reads the state of the run from the tracker and the run directory: labels, open and closed tickets, and which files exist. It reads no ticket body and no transcript, and it answers no question, so its context stays small for the full run. Each state has one dispatch in the table below. When a state is not in the table, stop and tell the user.

[`HARNESS.md`](HARNESS.md) says how to launch a session, how to name it, and how to wait for it.

## Run state

`run.toml` in the run directory is the memory of the driver. Only the driver writes it. Write it before and after each launch, so that a new driver session can continue from it.

```toml
atlas = "<absolute path of the atlas file>"

[[wave]]
n = 1
nodes = ["<node name>"]

[[session]]
role = "decide"
target = "<node name | ticket id | wave number>"
thread = "<thread id>"
attempt = 1
state = "running"   # running | ended
```

## Start

1. Read the config and `run.toml`. A missing `run.toml` is a new run: create it.
2. For each session with `state = "running"`, ask the harness for its state. Wait for those that still run, and set the others to `ended`.
3. When there is no wave, or the newest wave has a `signed-off` file, go to [Next wave](#next-wave). In all other cases, go to [The loop](#the-loop).

## Next wave

1. When the newest wave has a `signed-off` file, a build can have changed the atlas: launch `replan` and wait for it.
2. When the atlas directory is gone, the initiative is complete. Tell the user, and stop.
3. The new wave is each frontier node of the atlas that is in no earlier wave. When there is none, tell the user which published tickets to build first, and stop.
4. Add the wave to `run.toml`, create `wave-<n>/`, and go to the loop.

## The loop

A ticket **can start** when it is open, each ticket that blocks it is closed, it has no park label, and no running session has it as target. The tickets of the wave are the child tickets of the maps of its nodes. The map of a node is the issue with the label `wayfinder:map` and the title `<initiative name>: <node name>`.

On each pass, take the first row that is true:

| State | Dispatch |
| --- | --- |
| A node of the wave has no map | `plan` for each such node |
| A ticket can start | One session for each such ticket: `decide` for `wayfinder:grilling`, `proto` for `wayfinder:prototype`, `research` and `task` for their types. Launch `proto` sessions first. |
| A session is running | Wait for the next session to end |
| A ticket has the label `navigator:shape` | `replan` |
| A ticket has the label `navigator:parked` | `ask` for the wave |
| A node of the wave has no `split.md` and its map has no open ticket | `spec` for each such node |
| The wave has no `signed-off` file | `review` for the wave |
| The wave has a `signed-off` file | Tell the user that the wave is signed off, which tickets are published, and that `/navigator <atlas path>` continues the run after the build. Stop. |

Keep at most `max_parallel_sessions` sessions running. A session that waits for the user is still running: wait again.

The table also carries a correction from the wave review. The review opens the tickets that must run again and deletes the splits that depend on them, and then the rows above bring the wave back to `review`.

## When a session ends

Set its state to `ended`, and check the completion state of its role: the map exists (`plan`), the ticket is closed or has a park label (`decide`, `research`, `proto`, `task`), `split.md` exists or a ticket of the map has a park label (`spec`), no ticket of the wave has the label that the session was launched for (`ask`, `replan`), the wave has a `signed-off` file, or a ticket of the wave is open, or a node has no `split.md` (`review`).

When the completion state is false, launch the session one more time with `attempt = 2`. When it is false again, add the label `navigator:parked` to the ticket, with a comment that says "Blocked: the session failed two times". For a role with no ticket, stop and tell the user.

Leave each thread as it is when it ends. The user reads them.
