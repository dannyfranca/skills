# Harness

How to launch a session, name it, and reach a model of another provider.

## Session

A **session** has a fresh context window and all the capabilities of a session that the user starts by hand. It can start subagents, and it can reach each model of the config. The council and the reports depend on this.

Launch each session with its model from the `[models]` table of the config. Give the `proto` and `task` roles their own worktree. All other roles work in the repo root.

| Harness | A session is | Wait |
| --- | --- | --- |
| T3 Code | A top-level thread. The user asked for these threads when they invoked navigator. | The thread wait tool, with the longest interval. |
| A harness whose subagents can start subagents | A subagent | The exit notification of the subagent |
| All others | A session that the user starts | The user says that it ended |

In a harness that is not T3 Code, find first whether a subagent can start its own subagents. Read the documentation of the harness for the setting: it is frequently off by default. When the setting exists and is off, tell the user the exact setting and its value, and wait until the user has enabled it. Use the last row only when the harness has no such setting: print the name and the brief of each session, and ask the user to start each one in a new session.

## Name

Each thread name has the same shape, so that the user can tell navigator threads from their own:

```
🧭 <initiative name> | <role> | <ticket name or node name>
🧭 <initiative name> | DRIVER
```

The role is the role name from `SKILL.md`. `ask` and `review` use `wave <n>` as the last part, and `replan` has no last part.

## Brief

The brief is the full prompt of a session. The skill holds each rule, so the brief holds only the role, the atlas path, and the target:

```
/navigator <role> <atlas path> <target>
If the line above loaded no skill, read <absolute path of the navigator skill directory>/SKILL.md first, and take the role of the line above.
```

The target is the node name for `plan` and `spec`, the ticket id or URL for `decide`, `research`, `proto`, and `task`, and the wave number for `ask` and `review`. The driver has no role word: `/navigator <atlas path>`.

## A model of another provider

A council member runs on its own provider. In T3 Code, use the delegate tool with the provider and the model of the member. In other harnesses, use the non-interactive mode of the command line tool of that provider, when it is installed. A member that you cannot reach has failed: `COUNCIL.md` says what to do then.
