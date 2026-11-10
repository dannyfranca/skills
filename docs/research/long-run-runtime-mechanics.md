# Long-run runtime mechanics

Research ticket: [#61](https://github.com/dannyfranca/skills/issues/61). Map: [#59 Hill-climb skill](https://github.com/dannyfranca/skills/issues/59).

Sources were read on 2026-11-09. Versions: Claude Code 2.1.310, Codex CLI 0.163.0 (latest release 0.167.0).

## Question

How can one agent session run for days on Claude Code and on Codex? How does a run survive context compaction, process restarts, and machine reboots? What does each harness give to stop a run, resume a run, and let a second thread read the state of a running run?

## Short answer

Both harnesses now have the same four parts:

1. **A goal loop.** `/goal` keeps the session working turn after turn until the goal is complete. On Claude Code, a small model judges the transcript after each turn. On Codex, the agent itself calls `update_goal` with `complete` or `blocked`, and the harness adds a continuation prompt while the goal is `active`.
2. **A Stop hook.** On both harnesses, a `Stop` hook that returns `decision: "block"` starts one more turn with the hook `reason` as the prompt. A hook can run the verifier deterministically.
3. **A background server.** Claude Code runs `claude --bg` sessions under a supervisor daemon. Codex runs sessions on a shared local app-server daemon. The session continues when the terminal closes.
4. **A message path into a running session.** Claude Code has `SendMessage` and background-session reply. Codex has `codex queue` and the app-server `turn/steer`.

Neither harness continues a run by itself after a machine reboot. After a reboot, something must send "continue" (Theo's video shows the same pattern). Compaction removes conversation detail on both harnesses. Thus the run must keep its state in files, and a `SessionStart` hook with matcher `compact` must put that state back into context.

Recommendation: keep all run state in repo files. Make each adapter a thin layer with five verbs: `start`, `continue`, `stop`, `status`, `message`. See [Adapter shape](#recommended-adapter-shape).

## Claude Code

### Keep the session working

**`/goal <condition>`** ([goal](https://code.claude.com/docs/en/goal), added in v2.1.139 per the [changelog](https://code.claude.com/docs/en/changelog)):

- The condition has a maximum of 4,000 characters. After each turn, a small fast model (the Haiku model, or `ANTHROPIC_DEFAULT_HAIKU_MODEL`) reads the transcript. It returns "met", "not yet met", or "impossible". The judge has no tools. It cannot run the verifier. It sees only what the transcript shows.
- One goal per session. `/goal` shows status. `/goal clear` removes it. `/clear` also removes it.
- It works with `-p`: `claude -p "/goal ..."`.
- These errors clear the goal: an authentication failure, no credit, a context overflow that auto-compact cannot fix, and a model that is not available.
- Other errors keep the goal. A transient error (overload, dropped connection) retries 3 times, then pauses. A rate limit, a claude.ai usage limit, or a hook that ended the turn pauses the goal. A paused goal waits for the next message. Automatic retry needs an interactive session on v2.1.269 or later.
- Background work delays the judge. A check-in comes after 30 minutes, then 1 hour, then every 2 hours (`CLAUDE_CODE_GOAL_CHECKIN_MINUTES`; `0` stops check-ins and retries). An interactive session starts a maximum of 3 idle check-ins between your prompts. A `-p` session gets check-ins only at the end of a turn.
- The goal stops if Claude makes no tool call for several turns.
- For unattended runs, the docs recommend auto mode.

**Stop hook** ([hooks](https://code.claude.com/docs/en/hooks)):

- `decision: "block"` with a `reason`, or `hookSpecificOutput.additionalContext`, makes Claude continue.
- Input fields include `stop_hook_active`, `last_assistant_message`, `background_tasks[]`, and `session_crons[]`.
- A maximum of 8 consecutive blocks is permitted (`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`). A tool call resets the count.
- The Stop hook does not run when the user interrupts. An API error fires `StopFailure`, which cannot block.
- `SubagentStop` has the same control for subagents.
- An agent hook (`type: "agent"`, experimental) can use tools for a maximum of 50 turns and return `{ok}`. Thus a hook can run the verifier and decide.

**`/loop`** ([scheduled-tasks](https://code.claude.com/docs/en/scheduled-tasks)):

- Fixed interval, or self-paced (1 minute to 1 hour, through `ScheduleWakeup`).
- Recurring tasks expire after 7 days.
- A task fires only while the session runs and is idle.
- Resume restores fixed loops. Resume does not restore self-paced loops.
- Cloud routines (minimum 1 hour, no local files) and Desktop scheduled tasks are durable alternatives.

**Usage-limit auto-continue** ([interactive-mode](https://code.claude.com/docs/en/interactive-mode)):

- Only interactive sessions on a claude.ai subscription.
- Not in background sessions. Not in `-p`. Not when the reset is more than 24 hours away.
- A maximum of 3 continuations.
- Thus a multi-day run on a subscription needs an external "continue" after each usage-limit pause.

**`claude -p`** ([headless](https://code.claude.com/docs/en/headless)):

- After the turn, `-p` waits for background work. The idle limit is 10 minutes (`CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`, `0` = no limit). It also waits for pending self-paced `/loop` wakeups.
- `--max-budget-usd` and `--max-turns` set limits.
- `SIGTERM` exits with code 143. The turn stays unfinished. `SessionEnd` runs. Use `SIGINT` to end the turn cleanly.

### Survive compaction

Sources: [hooks](https://code.claude.com/docs/en/hooks), [context-window](https://code.claude.com/docs/en/context-window).

- These items survive compaction: `CLAUDE.md`, auto memory, skill descriptions, the plan, a maximum of 5 recent files, and the bodies of invoked skills. The skill body limit is 5k tokens for each skill and 25k tokens in total. Thus put the key protocol text at the top of `SKILL.md`.
- Background commands and subagents continue during compaction.
- Context that a hook added is summarized away.
- `PreCompact` can block compaction. Its stdout (maximum 10k characters) becomes the summary instructions. `PostCompact` gets `compact_summary`.
- A `SessionStart` hook with matcher `compact` adds context again after compaction. This is the correct place to put the run ledger back.

### Survive restarts and reboots

**Resume** ([sessions](https://code.claude.com/docs/en/sessions)):

- Transcripts are in `~/.claude/projects/<project>/<id>.jsonl`. The format is internal. `cleanupPeriodDays` deletes them after 30 days by default.
- Resume restores the history, model, agent, permission mode, the active goal, and session crons that did not expire. For the goal, the turn count, timer, and token baseline start again from zero.
- Resume does not restore background Bash commands or monitors. Resume does not run interrupted tools again. `CLAUDE_CODE_RESUME_INTERRUPTED_TURN=1` continues the interrupted turn.
- Give `--mcp-config`, `--settings`, `--plugin-dir`, and `--add-dir` again on resume.
- The picker does not show `-p`, SDK, or `/loop` sessions. Resume them by ID.

**Background sessions** ([agent-view](https://code.claude.com/docs/en/agent-view), research preview):

- `claude --bg --name <name> "prompt"` starts a session under a supervisor daemon. No terminal is necessary. `claude --resume <id> --bg "continue"` moves a session to the background.
- The session survives auto-update, supervisor restart, and machine sleep. The supervisor restarts a process that exits unexpectedly.
- Shutdown or reboot stops running sessions. In the first 48 hours after its last activity, the session shows "failed". A reply or `claude attach <id>` restarts it from its saved conversation. Send "continue" to restart the interrupted work. After 48 hours, the session shows "stopped". `claude attach <id>` or a reply resumes it.
- The supervisor stops an idle, unattached session after approximately 1 hour. Pin it with `Ctrl+T`.
- A self-paced `/loop` stops if its wakeup comes due while the process is down.
- State is in `~/.claude/jobs/<id>/` and `~/.claude/daemon/roster.json`. These paths are not stable. For progress files, use `$CLAUDE_JOB_DIR/tmp`.

**Agent SDK** ([agent-sdk/sessions](https://code.claude.com/docs/en/agent-sdk/sessions), [agent-sdk/session-storage](https://code.claude.com/docs/en/agent-sdk/session-storage)):

- Long-running clients: Python `ClaudeSDKClient`, TypeScript `streamInput()`.
- A `SessionStore` adapter copies transcripts to your storage. It copies only transcripts. Writes are best-effort (`mirror_error`).
- To resume on a different host, use a session store, copy the `.jsonl` file, or keep application state outside the session.

### Stop, resume, and read state from a second thread

- Stop: `/goal clear`, `Ctrl+C`, `/stop` in a background session, `claude stop <id>`, or `SIGINT` to a `-p` process.
- Resume: `claude --resume <id>`, `claude attach <id>`, `claude respawn <id>` (runs the original prompt again), or a reply to the session.
- Read state ([agent-view](https://code.claude.com/docs/en/agent-view)): `claude agents --json [--all] [--cwd]` gives `state` (working, blocked, done, failed, stopped), `status` (busy, waiting, idle), and `waitingFor`. This is the supported way for a program or a second session to read state. `claude logs <id>` shows output.
- Ask a question ([cross-session-messaging](https://code.claude.com/docs/en/cross-session-messaging), v2.1.224 and later): `ListAgents` and `SendMessage` work between sessions on the same machine through a Unix socket. Delivery occurs between tool calls, or as a new turn when the session is idle. Plain text, approximately 1M characters, queue of 50. A `-p` worker needs `crossSessionInbound: accept` to take messages unattended. Remote Control gives messaging across machines.
- Ask a stopped session ([sessions](https://code.claude.com/docs/en/sessions)): `claude -p --resume <id> --output-format json "summarize"` sends one question to a saved session. Do not use this on a session that is running: it adds a second writer to the same conversation.

## Codex

### Keep the session working

**`/goal <objective>`** ([long-running-work](https://learn.chatgpt.com/docs/long-running-work), [developer-commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli)):

- Available in the CLI, the IDE extension, and the desktop app. Feature `goals` is stable and on by default ([config-reference](https://learn.chatgpt.com/docs/config-file/config-reference)).
- Subcommands: `/goal`, `/goal edit`, `/goal pause`, `/goal resume`, `/goal clear`.
- The objective has a maximum of 4,000 characters. For longer instructions, put them in a file and point the goal at the file.
- `goals.max_goal_token_budget` sets the default and maximum token budget for a goal.
- The goal keeps the same sandbox and approval policy. It pauses when it needs a decision.
- `/side` (or `/btw`) starts a side chat for a status recap without an interrupt to the main chat.

**How the goal loop works** (source code, not stable API):

- The goal is in a local state database, not in the transcript ([state/goals_migrations](https://github.com/openai/codex/tree/main/codex-rs/state)).
- While the goal is `active` and the thread is idle, the harness starts a new turn with a continuation prompt ([ext/goal/src/runtime.rs](https://github.com/openai/codex/blob/main/codex-rs/ext/goal/src/runtime.rs), `continue_if_idle`).
- The continuation prompt tells the agent to check current state, classify the last turn as progress, verified wait, or no progress, and to audit each requirement before it claims completion ([templates/goals/continuation.md](https://github.com/openai/codex/blob/main/codex-rs/ext/goal/templates/goals/continuation.md)).
- The agent ends the goal itself with the `update_goal` tool: `complete`, or `blocked` after the same blocker occurs for 3 consecutive goal turns. Only the user can set `paused` ([ext/goal/src/spec.rs](https://github.com/openai/codex/blob/main/codex-rs/ext/goal/src/spec.rs)).
- The system sets `budgetLimited` and `usageLimited`. A turn error sets `blocked`. Repeated empty responses stop the goal.
- Thus on Codex the agent judges its own completion. On Claude Code a separate model judges. For a hill climb, neither is sufficient: the verifier must decide.

**Stop hook** ([hooks](https://learn.chatgpt.com/docs/hooks)):

- Feature `hooks` is stable. Events: `SessionStart`, `SessionEnd`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, `Stop`, `Interrupt`, `PreModelRequest`, `PostModelRequest`.
- `Stop` with `decision: "block"` (or exit code 2) makes Codex continue. The `reason` becomes a new user prompt. Input includes `stop_hook_active` and `last_assistant_message`.
- `continue: false` from any matching hook overrides a block.
- The docs give no block cap like the Claude Code cap of 8.
- Hook timeout defaults to 600 seconds. A Stop hook can run a short verifier, but not a long one.
- Non-managed hooks must be reviewed and trusted before they run.

**`codex exec`** ([non-interactive-mode](https://learn.chatgpt.com/docs/non-interactive-mode)):

- `codex exec` runs one task and exits. `--json` streams events. `--output-schema` and `-o` write a structured final answer. `--ephemeral` saves no rollout.
- `codex exec resume --last "..."` or `codex exec resume <SESSION_ID> "..."` continues a saved session.
- Source code shows that `exec` shuts down when the turn completes, and that it starts threads with `defer_goal_continuation` ([exec/src/lib.rs](https://github.com/openai/codex/blob/main/codex-rs/exec/src/lib.rs)). Thus `exec` does not run the goal loop. A script that loops `codex exec resume` is the headless option.

**Long waits inside one turn** ([config-reference](https://learn.chatgpt.com/docs/config-file/config-reference), [`skills/update-codex-await`](../../skills/update-codex-await/SKILL.md)):

- `background_terminal_max_timeout` is the maximum poll window for an empty `write_stdin` poll. Upstream default: 300,000 ms (5 minutes).
- Danny's fork (`dannyfranca/codex`, branch `await-max-timeout`) makes an empty poll always wait for `background_terminal_max_timeout` and ignore the yield time that the model asks for. Process exit still returns early. The local config sets 3,600,000 ms (1 hour).
- Thus a Codex climber can wait for a long verifier run with one poll each hour, not one poll each few seconds. Each poll costs tokens.

### Survive compaction

Sources: [hooks](https://learn.chatgpt.com/docs/hooks), [config-reference](https://learn.chatgpt.com/docs/config-file/config-reference).

- `SessionStart` with matcher `compact` runs after compaction of a root session, before the next model request. This also occurs when auto-compaction happens in the middle of a turn. Its stdout or `additionalContext` becomes developer context.
- `PreCompact` can stop compaction (`continue: false`). Its plain stdout is ignored. Thus Codex cannot change the summary from a hook. Use `compact_prompt` or `experimental_compact_prompt_file` in config to change the summary prompt.
- `model_auto_compact_token_limit` sets the auto-compaction threshold.
- The goal objective is in the state database. The continuation prompt tells the agent to call `get_goal` if the objective is missing. Thus the goal survives compaction.

### Survive restarts and reboots

**Shared daemon** ([developer-commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli), [app-server](https://learn.chatgpt.com/docs/app-server)):

- The CLI uses a shared background app-server by default (feature `daemon_auto_start`, stable). `--no-daemon` runs without it.
- `codex agents` opens a command center for all sessions on the shared daemon.
- `/daemon` restarts or updates the daemon. The docs say that a restart "may interrupt active or queued work".
- Source code shows that after a daemon restart, the daemon starts one continuation turn for each interrupted turn. The prompt is "The server restarted and interrupted the previous turn. Continue the unfinished work..." ([daemon_continuation.rs](https://github.com/openai/codex/blob/main/codex-rs/app-server/src/request_processors/daemon_continuation.rs)). It does this only if the permissions and the local environment did not change. This code is from October and November 2026 and is not documented.
- `SessionEnd` runs when a conversation is idle and no client has it open for 30 minutes. The daemon then unloads the thread.
- No source shows that the daemon starts by itself after a reboot. It starts when a Codex command needs it.

**Resume:**

- `codex resume [SESSION_ID|name] [PROMPT]`, or `--last`. `--include-non-interactive` also shows `exec` sessions. `codex fork` copies a session to a new ID.
- When the thread is resumed with an `active` goal, the goal runtime marks the goal active again ([runtime.rs](https://github.com/openai/codex/blob/main/codex-rs/ext/goal/src/runtime.rs), `restore_after_resume`).
- `codex queue --thread <id> --message "continue" --resume` queues a message and loads the session if it is not loaded. It needs a running local daemon or `--remote`. This is the scriptable "continue after reboot" command.

### Stop, resume, and read state from a second thread

- Stop: `/goal pause`, `/goal clear`, `Esc` to interrupt, or app-server `turn/interrupt` and `thread/goal/set` with status `paused`.
- Resume: `/goal resume`, `codex resume <id>`, `codex queue --resume`, or app-server `thread/resume`.
- Read state ([app-server](https://learn.chatgpt.com/docs/app-server)): connect to the daemon with `--remote unix://`. Then `thread/list` and `thread/read` give runtime `status` without a resume. `thread/goal/get` gives `objective`, `status`, `tokenBudget`, `tokensUsed`, and `timeUsedSeconds`. `thread/turns/list` and `thread/items/list` page through history. `thread/goal/updated` and `thread/status/changed` are push notifications. `codex agents` has no `--json` flag in 0.163.0.
- Ask a question: `codex queue --thread <id> --message "What blocks you?"` (text only). App-server `turn/steer` adds input to the turn in progress. In the TUI, `/side` asks without an interrupt.

## Per-harness table

| Need | Claude Code | Codex |
| --- | --- | --- |
| Keep working across turns | `/goal` (Haiku judge reads transcript) or Stop hook `decision: "block"` (cap 8 in a row, tool call resets) | `/goal` (agent calls `update_goal`; harness adds continuation while `active`) or Stop hook `decision: "block"` (no documented cap) |
| Who decides "done" | Judge model, or your Stop hook | The agent itself, or your Stop hook |
| Headless | `claude -p "/goal ..."`; waits for background work (10 min idle limit by default) | `codex exec` runs one turn, no goal loop; loop `codex exec resume` |
| Detached from terminal | `claude --bg --name <n>` under supervisor daemon | Shared local app-server daemon (default) |
| Long wait in one turn | Background task + goal check-ins (30 min, 1 h, 2 h) | Empty `write_stdin` poll up to `background_terminal_max_timeout` (fork: always the maximum, 1 h local) |
| Survive compaction | `SessionStart` matcher `compact`; `PreCompact` stdout sets summary instructions; invoked skill body kept (5k tokens) | `SessionStart` matcher `compact` (also mid-turn); `compact_prompt` config; goal kept in state database |
| Survive process or daemon restart | Supervisor restarts the process; goal and crons restored on resume; background Bash not restored | Daemon starts one continuation turn for each interrupted turn (source only) |
| Survive reboot | No. Session shows failed (<48 h) or stopped. Reply "continue" or `claude attach <id>` | No. Run `codex queue --resume --thread <id> --message "continue"` |
| Usage limit | Goal pauses. Auto-continue only in interactive mode, max 3, not in `--bg` | Goal becomes `usageLimited`. `/goal resume` or a message |
| Stop | `/goal clear`, `claude stop <id>`, `SIGINT` | `/goal pause`, `/goal clear`, `turn/interrupt` |
| Read state from second thread | `claude agents --json` (state, status, waitingFor) | App-server `thread/read`, `thread/goal/get` over `unix://` |
| Message a running session | `SendMessage` (same machine), reply in agent view | `codex queue`, app-server `turn/steer` |
| Stability | `--bg`, agent view: research preview. `/goal`, hooks: stable | `goals`, `hooks`, daemon: stable features. App-server WebSocket: experimental |

## Recommended adapter shape

### 1. Put the run state in files, not in the session

Neither harness keeps conversation detail through compaction or a reboot. Both harnesses can lose the session process. Thus the protocol must work from disk. This agrees with [#60](https://github.com/dannyfranca/skills/issues/60).

- Goal file (human-owned): goal, verifier command, budget, stop rules.
- Attempt ledger (outside git): one row for each attempt, with the verifier score.
- Blocker log: the current blocker, for the check-in thread.
- Status file: phase, best score, last verifier time, last heartbeat. The check-in thread and the adapter `status` verb read this file. It does not depend on harness state.

### 2. Make the verifier decide "done", not the harness judge

- Set the harness goal to a short text that points at the goal file: "Continue the hill climb in `<path>`. Done only when `<verifier>` passes the stop rule." Both harnesses limit the goal to 4,000 characters.
- Add a deterministic `Stop` hook that reads the status file. If the stop rule is not met and the budget is not used, it returns `decision: "block"` with "Continue. Read `<ledger>`." If the stop rule is met, it lets the turn stop. This works the same on both harnesses.
- On Claude Code, the Stop hook cap of 8 does not stop a climber that makes tool calls, because each tool call resets the count.

### 3. Put the state back after compaction

Install a `SessionStart` hook with matcher `compact|resume` on both harnesses. It prints the goal file, the last ledger rows, and the current blocker. On Claude Code, also keep the protocol at the top of `SKILL.md`, because the invoked skill body survives compaction.

### 4. Thin adapters with five verbs

| Verb | Claude Code | Codex |
| --- | --- | --- |
| `start` | `claude --bg --name hill-climb-<slug> --permission-mode auto "/goal ..."` | `codex` session with `/goal ...` on the shared daemon (or app-server `thread/start` + `thread/goal/set`) |
| `continue` | `claude attach <id>` or reply "continue" | `codex queue --resume --thread <id> --message "continue"` |
| `stop` | `/goal clear`, then `claude stop <id>` | `thread/goal/set` status `paused`, then `turn/interrupt` |
| `status` | Status file + `claude agents --json` | Status file + app-server `thread/read` and `thread/goal/get` |
| `message` | `SendMessage` or reply | `codex queue --thread <id> --message ...` |

### 5. Handle reboot and usage limits with one external "continue"

Neither harness restarts the run by itself after a reboot. Neither continues by itself after a usage limit in a detached session. Thus the check-in mode, or an optional login or timer script, runs the adapter `continue` verb when `status` shows the run is stopped, failed, paused, or `usageLimited` and the budget is not used. This is the same "continue" prompt that Theo sends by hand. v1 can keep this manual (the map lists scheduled check-ins as "maybe").

### Risks

- Claude Code background sessions and agent view are a research preview. Paths under `~/.claude/jobs/` are not stable.
- The Codex daemon-restart continuation and the `exec` goal deferral come from source code, not from docs. They can change.
- `claude -p --resume` on a running session makes a second writer. The check-in thread must use `SendMessage` or the status file, not resume.
- On Codex, the agent marks its own goal `complete`. The Stop hook must check the verifier result to prevent an early stop.
