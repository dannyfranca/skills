# Harness

How to launch a session, how to name it, how to archive it, and how a session wakes the driver.

## Session

A **session** has a fresh context window and all the capabilities of a session that the user starts by hand. It can start subagents: the reports and the videos depend on this.

Launch each session with its model from the `[models]` table of the config and its reasoning effort from the `[effort]` table. State the two in each launch: a launch that leaves one out gets the default of the harness, and not the config value. In T3 Code, the effort is the model option `effort`: `options: [{"id": "effort", "value": "<effort>"}]`. Give the `proto` and `task` roles their own worktree. All other roles work in the repo root.

| Harness | A session is | Wait | Archive |
| --- | --- | --- | --- |
| T3 Code | A top-level thread. The user asked for these threads when they invoked navigator. | End the turn, as the [Wake](#wake) part says. | The thread organize tool, with the action `archive` and the thread id. |
| A harness whose subagents can start subagents | A subagent | The exit notification of the subagent | No step |
| All others | A session that the user starts | The user says that it ended | No step |

In T3 Code, the run of a session stopped when its thread has no active run: the thread read tool shows this. The thread wait tool returns after a short time, also while the run continues, so a wait that returned says nothing about the session. Use it for one case only: a session that woke the driver can be in its last turn, and the wait holds the driver until that turn ends.

An archived thread leaves the thread list of the user. It stays readable, and the user can bring it back. `DRIVER.md` says when the driver archives a thread: never while the session waits for the user, and never while its last turn runs.

In a harness that is not T3 Code, find first whether a subagent can start its own subagents. Read the documentation of the harness for the setting: it is frequently off by default. When the setting exists and is off, tell the user the exact setting and its value, and wait until the user has enabled it. Use the last row only when the harness has no such setting: print the name and the brief of each session, and ask the user to start each one in a new session.

## Wake

A work session can run for an hour, and an `ask` or a `review` session waits for the user for hours or for days. The driver must spend no turn in that time. Thus the driver ends its turn, and each session wakes it.

| Harness | The driver, when it waits | Each session, as its last step |
| --- | --- | --- |
| T3 Code | Ends its turn with no timer: the wake line of a session starts the next turn. When an `ask` or a `review` session waits for the user, the driver first tells the user the name of that thread. | Sends the line `<role> <target> ended` to the `driver_thread` of `run.toml`, with the thread send tool. The target is the target of the brief, and `replan` has none. |
| All others | Waits as the Session part says | No step |

The line is a signal and gives no instruction: the driver that gets it takes the pass again from the tracker state.

## Name

Each thread name has the same shape, so that the user can tell navigator threads from their own:

```
🧭 <initiative name> | <role> | <ticket name or node name>
🧭 <initiative name> | DRIVER
```

The role is the role name from `SKILL.md`. `ask` and `review` use `wave <n>` as the last part, and `replan` has no last part.

A driver that the user started has a name that the user or the harness gave. It gives itself the driver name as its first step, so that the user finds it by the same sign. In T3 Code, the thread update tool does this, with the action `rename`.

## Brief

The brief is the full prompt of a session. The skill holds each rule, so the brief holds only the role, the atlas path, and the target:

```
/navigator <role> <atlas path> <target>
If the line above loaded no skill, read <absolute path of the navigator skill directory>/SKILL.md first, and take the role of the line above.
```

The target is the node name for `plan`, the wave number and the node name for `spec`, the ticket id or URL for `decide`, `research`, `proto`, and `task`, and the wave number for `ask` and `review`. The driver has no role word: `/navigator <atlas path>`.
