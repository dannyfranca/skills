# Existing hill-climb and goal skills

Ticket: [#63](https://github.com/dannyfranca/skills/issues/63). Map: [#59 Hill-climb skill](https://github.com/dannyfranca/skills/issues/59).

Date of research: 2026-11-09.

## Question

Which published agent skills, plugins, or prompt packs already do hill climbing, autoresearch, optimization loops, or goal loops? What can `hill-climb` borrow from them?

## Short answer

- Many tools do the "change, measure, keep or revert" loop on git. The pattern comes from `karpathy/autoresearch`. Most other tools copy it.
- Claude Code and Codex now have a native `/goal` command. Each native goal is a "keep working until a condition is true" loop. Neither native goal runs a verifier command by itself.
- No tool ratchets the goal after success. No tool has a "what blocks you?" check-in that starts a new climb. No tool rejects a verifier that only a human can run.
- The best parts to borrow are: a script that owns keep/revert and logs, a guard that is separate from the metric, a session file that lets a fresh agent resume, native goal adapters, and a plateau escape.

## Method

- Searched skills.sh, GitHub, `anthropics/skills`, `anthropics/claude-plugins-official`, and `mattpocock/skills`. Used `gh api` and the firecrawl CLI.
- Read the SKILL.md, command files, scripts, and official docs of each strong candidate. Did not use blog summaries as the source of a claim.
- Each claim below links to the file that owns it.

## Candidates

### 1. karpathy/autoresearch

Source: <https://github.com/karpathy/autoresearch/blob/master/program.md>

- **What it does:** A prompt (`program.md`) that tells an agent to improve an LLM training script overnight without help.
- **Intake:** The agent and the human agree on a run tag. The agent makes the branch `autoresearch/<tag>`, reads the in-scope files, and makes `results.tsv`. Only `train.py` is editable. `prepare.py` and the eval are read-only. New dependencies are not permitted.
- **Loop:** Edit `train.py`, commit, run for a fixed 5-minute budget, and grep `val_bpb` from `run.log`. If `val_bpb` is lower, keep the commit. If not, do `git reset`. The first run is the baseline. The loop runs about 12 times each hour.
- **State files:** `results.tsv` (not tracked by git). Columns: commit, val_bpb, memory_gb, status (`keep`, `discard`, `crash`), description.
- **Stop rules:** None. "NEVER STOP." The agent must not ask the human. If it has no ideas, it must think harder. A run that takes more than 10 minutes is killed.
- **Verifier handling:** One fixed numeric metric from a read-only eval. A crash is fixed if the fix is simple. If not, the crash is logged and the agent continues.
- **Borrow:** The simplicity criterion. If a change deletes code and the result is equal, it is a win. A small gain that adds much complexity is not worth it.

### 2. uditgoenka/autoresearch

Source: <https://github.com/uditgoenka/autoresearch> (v2.3.0). Files: `SKILL.md`, `.claude/commands/autoresearch.md`, `references/behavior-preserving-optimization.md`, `references/reason-judge-protocol.md`, `references/fork-and-flush.md`.

- **What it does:** A large skill pack for Claude Code, Codex, and OpenCode. Install with `npx skills add uditgoenka/autoresearch`. It has a classic metric loop, an orchestrator for free-form goals, and 13 subcommands (for example `debug`, `fix`, `security`, `ship`, `reason`, `evals`, `regression`).
- **Intake:** Fields: Goal, Scope, Metric, Direction, Verify (a shell command that prints a number), Guard (a command that must always pass), and Iterations. If fields are missing, the skill asks one batched question. If no field is given, a setup wizard starts. Preconditions: a git repo, a clean tree, and a guard that passes at baseline. A safety screen blocks dangerous Verify commands (`rm -rf`, `curl | sh`, credential access).
- **Loop:** Review the TSV tail and `git log` as memory. Make ONE atomic change. Commit with the prefix `experiment:`. Run Verify, then Guard. Decide: `keep`, `discard`, `crash`, `no-op`, `hook-blocked`, or `metric-error`. A discard uses `git revert`. Log the result.
- **State files:** A run directory `autoresearch/{subcommand}-{YYMMDD}-{HHMM}/` with a TSV log. Columns: iteration, timestamp, commit, metric, delta, guard, guard-metric, status, description. The orchestrator writes `orchestrator-state.json`. `handoff.json` chains one run to the next.
- **Stop rules:** 25 iterations by default. `Iterations: unlimited` opts in to an unbounded run. The orchestrator stops on `CONVERGED`, `CEILING` (50 cycles), `PLATEAU` (2 fork rounds with no progress), or `BLOCKED`.
- **Verifier handling:**
  - The orchestrator turns a free-form goal into a Success predicate: an exact command and its expected output. The human confirms it once. After that, the predicate is pinned and is not derived again.
  - Before `DONE`, the orchestrator runs a held-out or adversarial verify step.
  - `behavior-preserving-optimization.md` keeps the search signal separate from acceptance. A behavior check must reject at least one known-bad change. "If a guard was chosen mainly because it is easy to keep green, replace it."
  - The `reason` mode uses cold-start roles (Author-A, Critic, Author-B, Synthesizer) and a judge panel with random labels and a majority vote. It converges when the same incumbent wins 3 rounds.
- **Borrow:** Fork-and-flush. At 33% and 66% of the budget, or after 5 iterations with no keep, start K fresh-context branches from the best commit. Half the branches are random, and a stateless advisor guides the other half. The doc cites arXiv:2610.07447 and reports 0.47 for a single agent against 0.78 for fork-and-flush. (I did not do an independent check of that number.)

### 3. davebcn87/pi-autoresearch

Source: <https://github.com/davebcn87/pi-autoresearch>. Files: the extension source, the `autoresearch-create` skill, and `site/content/revisiting-discards.md`.

- **What it does:** An extension and skill for the `pi` coding agent. Tools: `init_experiment`, `run_experiment`, `log_experiment`.
- **Intake:** The skill asks for, or infers, Goal, Command, Metric and direction, Files in scope, and Constraints. It makes the branch `autoresearch/<goal>-<date>`. It writes `.auto/prompt.md` and `.auto/measure.sh`, commits them, runs the baseline, and then starts the loop immediately.
- **Loop:** Run `measure.sh`, run `checks.sh`, and log. A `keep` commits automatically. A `discard`, `crash`, or `checks_failed` reverts automatically, but `.auto/` stays.
- **State files (`.auto/`):**
  - `prompt.md`: objective, metrics, how to run, scope, off-limits files, constraints, and "What's Been Tried". A fresh agent can resume from this file alone.
  - `measure.sh`: prints `METRIC name=value`. For a noisy benchmark shorter than 5 seconds, use the median of several runs.
  - `log.jsonl`: append-only log. Each entry can have `asi` notes that record what the agent learned. This is important for failures, because the reverted code is lost.
  - `ideas.md`: a backlog of ideas.
  - `checks.sh`: backpressure checks. A failure blocks a keep. It has its own timeout (300 s). The last 80 lines of output go back to the agent.
  - `config.json`: `maxIterations`, `workingDir`.
  - `hooks/before.sh`, `hooks/after.sh`: their stdout goes to the agent as a steer.
- **Stop rules:** "LOOP FOREVER, NEVER STOP", unless `maxIterations` is set. If the user sends a message, the agent first completes the current experiment.
- **Verifier handling:** A confidence score: |best improvement| / MAD (median absolute deviation) of the runs. A value of 2.0× or more means the gain is probably real. The score is advisory only.
- **Borrow:**
  - Rehydration after auto-compaction. The extension tells the agent to read `prompt.md`, the log tail, `ideas.md`, and `git log` again.
  - Revisiting discards. After each log, the agent checks if the new result removes the reason for an earlier discard. A retry is marked "↻ Revisiting #N".
  - `/autoresearch finalize`. It groups kept experiments into independent branches from the merge-base, with no shared files, so a human can review each branch.

### 4. drivelineresearch/autoresearch-claude-code

Source: <https://github.com/drivelineresearch/autoresearch-claude-code>

- **What it does:** A pi-autoresearch port for Claude Code AND Codex. It has one shared skill and standard-library Python helpers. It does not need an MCP server.
- **Intake:** The agent sets the scope, makes an experiment branch, prepares a fixed benchmark, calibrates noise, and logs a baseline.
- **Loop:**
  - On Claude Code, a Stop hook blocks the end of a turn while a valid session has budget.
  - On Codex, a supervisor (`codex_loop.py`) runs bounded `codex exec` turns. Each turn must log exactly one result.
  - A keep requires strict improvement above the noise floor and passing correctness checks.
- **State files:** Config and result JSONL, with validation, locks, and atomic writes. Also a worklog and a dashboard. A Claude PreCompact hook takes a snapshot, and SessionStart gives the context back.
- **Stop rules:** Caps on runs, time, and target for the current segment. Codex also has caps on turns and seconds. The supervisor stops on no progress, a failure, a changed config, a changed branch, or changed protected files. A `.autoresearch-off` file pauses the loop.
- **Verifier handling:** `--protect path/to/scorer.py` hashes the scorer files between Codex turns. A change to the eval definition starts a new **segment** with a new baseline. The README says this is not a sandbox against a hostile agent.
- **Borrow:** Segments. When the eval definition changes, start a new segment with a new baseline and do not compare across segments. This is the closest existing mechanism to a goal ratchet.

### 5. leo-lilinxiao/codex-autoresearch

Source: <https://github.com/leo-lilinxiao/codex-autoresearch>

- **What it does:** An autoresearch skill for Codex only. A control script `scripts/autoresearch.py` owns measurement, commits, rollback, and events. Commands: `init`, `finish`, `status`, `history`, `report`, `block`, `resume`, `archive`, `launch`, `stop`.
- **Intake:** One confirmation block with the fields Goal, Scope, Metric (baseline, target, direction), Verify, Guard, Mode, and Stop. One run has one repo, one numeric metric, one target, and path scopes.
- **Loop:**
  - Foreground mode uses the native Codex goal (`create_goal`).
  - Background mode uses a detached controller that runs one `codex exec` worker for each experiment.
- **State files:** `run.json` (immutable config) and `events.jsonl` (append-only). The skill says: never repair these files by guessing. An HTML report is also available.
- **Stop rules:** Statuses: `active`, `complete` (only when the target is verified), `stopped`, `blocked` (needs external information, credentials, data, or hardware), and `error`.
- **Verifier handling:** The last stdout line of Verify is a number, or JSON with `--metric-key`. Verify must exit 0 even when the result is bad, and it must not change project files. Guard must pass at baseline.
- **Borrow:** The script, not the model, does keep, revert, and logging. The model cannot change the record of what happened.

### 6. Ralph loop (anthropics/claude-plugins-official)

Source: <https://github.com/anthropics/claude-plugins-official/tree/main/plugins/ralph-loop>

- **What it does:** A Claude Code plugin. `/ralph-loop "<prompt>" --max-iterations N --completion-promise TEXT`. A Stop hook (`hooks/stop-hook.sh`) blocks the exit and gives the SAME prompt again.
- **Intake:** One prompt, an iteration cap, and a promise string.
- **Loop:** The same prompt each turn. The agent sees its earlier work in the files and in git.
- **State files:** `.claude/ralph-loop.local.md` with frontmatter: `iteration`, `max_iterations`, `completion_promise`, `session_id`.
- **Stop rules:** The agent prints `<promise>TEXT</promise>` (exact match), or the loop reaches `max_iterations`, or the user runs `/cancel-ralph`. The README says `max_iterations` is the primary safety net. One promise string cannot express both "success" and "blocked".
- **Verifier handling:** None. The agent decides when the promise is true. The rule is "ONLY output it when TRUE".
- **Borrow:** Session isolation. The hook does not block a session whose `session_id` is different from the one in the state file. Thus, a loop in one session does not trap other sessions in the same repo.

### 7. jthack/claude-goal

Source: <https://github.com/jthack/claude-goal>

- **What it does:** A Codex-style `/goal` for Claude Code, made before the native command. It is a skill plus a user-level Stop hook.
- **Intake:** `/goal <text>`, optional `--tokens 250K`.
- **Loop:** The Stop hook blocks stopping while a goal is active. The continuation prompt wraps the goal in `<objective>` and requires a completion audit before the agent marks the goal complete.
- **State files:** `~/.claude/goal/goals.sqlite`.
- **Stop rules:** `/goal pause`, `/goal clear`, `/goal complete`. A runaway guard allows 500 continuations (`CLAUDE_GOAL_MAX_STOP_CONTINUES`).
- **Verifier handling:** A completion audit in the prompt. There is no command check.
- **Borrow:** Token budgets are "soft" because a skill cannot read live token use. `hill-climb` must use the same honest label for any budget that it cannot enforce.

### 8. Claude Code native `/goal`

Source: <https://code.claude.com/docs/en/goal>

- **What it does:** A session-scoped Stop hook that uses a prompt. After each turn, a small fast model reads the transcript and returns one of three results: not yet met (the reason becomes guidance for the next turn), met, or impossible.
- **Intake:** A condition of up to 4000 characters. The docs recommend one measurable end state, a stated check, and constraints. The condition can include "or stop after 20 turns".
- **Loop:** The agent works. The evaluator judges. Background work delays the evaluation.
- **State files:** Session state. The goal comes back on resume. `/goal` shows turns, tokens, and the last reason. `/goal clear` removes it. `-p` runs it headless.
- **Stop rules:** Met, impossible, or a turn cap in the condition. The goal stops after several turns with no tool use. Unrecoverable errors (auth, credits, context overflow, model unavailable) clear the goal. Rate or usage limits pause it. Check-ins start after 30 minutes (`CLAUDE_CODE_GOAL_CHECKIN_MINUTES`), with a maximum of 3 idle check-ins.
- **Verifier handling:** The evaluator does NOT run tools. It judges only what the transcript shows. Thus, the agent must run the verifier and print the result where the evaluator can see it.
- **Borrow:** Use `/goal` as the continuation engine on Claude Code. Write the condition so that it points to the verifier output line, for example "the last `HILLCLIMB STATUS` line says `met`, or `blocked`".

### 9. Codex native goals

Sources: <https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex>, <https://simonwillison.net/2026/Apr/30/codex-goals/> (Codex 0.128.0).

- **What it does:** `/goal`, `/goal pause`, `/goal resume`, `/goal clear`. The goal is persisted state on the thread. States: active, paused, complete, budget-limited.
- **Intake:** The cookbook gives a 6-part goal: Outcome, Verification surface, Constraints, Boundaries, Iteration policy, and Blocked stop condition. Template: "<end state> verified by <evidence> while preserving <constraints>. Use <boundaries>. Between iterations, <policy>. If blocked…, <report>." Codex can help write the goal.
- **Loop:** Codex continues only when the thread is idle. If a turn makes no tool call, continuation stops.
- **State files:** Thread state, managed by Codex.
- **Stop rules:** The model can mark a goal complete only with evidence. Only the user or the system controls pause, resume, and budget. When the budget is reached, the goal is NOT complete. The agent summarizes progress and blockers.
- **Verifier handling:** The "verification surface" is part of the goal text. The model must cite evidence.
- **Borrow:** The 6-part goal contract as the intake form. "Budget reached" is a separate status from "complete".

### 10. anthropics/skills `skill-creator`

Source: <https://github.com/anthropics/skills/tree/main/skills/skill-creator> (`scripts/run_loop.py`).

- **What it does:** Improves a skill description so that it triggers correctly.
- **Intake:** An eval set of trigger and non-trigger queries.
- **Loop:** Split the eval set 60/40 into train and held-out test, stratified. Run each query 3 times. Claude proposes a better description from the train failures. The history that Claude sees does not show test results.
- **State files:** `iteration-N/eval-ID/` directories.
- **Stop rules:** A maximum of 5 iterations.
- **Verifier handling:** The final `best_description` is chosen by the test score, not the train score, to prevent overfitting. The skill also has blind A/B comparator and analyzer agents.
- **Borrow:** The train/held-out split. Choose the winner on data that the climber never saw.

### 11. mattpocock/skills

Source: <https://github.com/mattpocock/skills>

- **What it does:** No hill-climb skill. Three parts are relevant.
- `diagnosing-bugs`, Phase 1 "Build a feedback loop": 10 ways to build a loop. Make it faster, sharper, and deterministic. "Done" means one tight command that can go red. If no loop can be built, stop and ask.
- `in-progress/software-factory`: "Start slow" brakes (automated checks, automated review, human review). "Cap every loop of runs at a few tries before a human." `EXAMPLE-TASKS.md` has a **ratchet task**: each day, take one file off an exception list, fix it, and open a PR. "The check only ever gets stricter."
- `in-progress/loop-me`: workflow specs with Trigger, Checkpoint, Push right, and Brief.
- **Borrow:** The intake step "build a tight, red-capable feedback loop first". If the agent cannot build the loop, it stops and asks. This matches the rule that intake rejects a verifier that only a human can run.

### 12. Other sources (brief)

- **muratcankoylan/Agent-Skills-for-Context-Engineering, `self-improvement-loops`** (<https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering>): Design guidance, not a loop runner. Useful rules:
  - "The loop optimizes whatever signal it is given, including the signal's own weaknesses."
  - Keep the evaluator, budget, and permissions outside the surface that the loop can change.
  - Treat a detected exploit as a failed candidate.
  - Track edit diffs, because a broken improver that returns its input looks like stability.
- **Addy Osmani, agent-skills loops** (<https://skills.addy.ie/loops/>):
  - "Grant only as much autonomy as you can verify."
  - A lights-out loop is safe when the check is cheap, frequent, and hard to fake.
  - The maker and the checker must be different agents.
- **wanshuiyin/auto-claude-code-research-in-sleep (ARIS)** (<https://github.com/wanshuiyin/auto-claude-code-research-in-sleep>): Research-paper loops (`auto-review-loop`, `auto-paper-improvement-loop`, `dse-loop`, `meta-optimize`). They are specific to a domain. I did not read them in depth.

## Top 5 borrow list

1. **Script-owned ratchet and log.** A small script does commit, measure, keep or revert, and appends to the log. The config is immutable, and the event log is append-only. The model reads the log but does not write the decisions. (codex-autoresearch `run.json` + `events.jsonl`; pi `log.jsonl`; Karpathy `results.tsv`.)
2. **Guard separate from the metric, plus held-out confirmation.** The metric steers the search. A separate guard or checks command blocks a keep. Before "met", run a held-out or adversarial check. Replace a guard that is easy to keep green. Hash or lock the scorer files. (uditgoenka `behavior-preserving-optimization.md`; pi `checks.sh`; skill-creator 60/40 split; driveline `--protect`.)
3. **Resumable session file.** One file holds the objective, the verifier, the scope, "What's Been Tried", and the idea backlog. After compaction or in a new session, the agent reads this file, the log tail, and `git log` again. Record what each failure taught, because the revert removes the code. (pi `.auto/prompt.md`, `ideas.md`, `asi`; driveline PreCompact snapshot.)
4. **Native goal adapters and clear end statuses.** On Claude Code, use `/goal` as the continuation engine. On Codex, use `create_goal` or `/goal`. Fill the 6-part Codex goal contract at intake. Use different statuses for `met`, `blocked`, `budget-reached`, and `stopped`. Print a status line that the Claude evaluator can read, because that evaluator does not run tools. (Claude Code goal docs; Codex cookbook; codex-autoresearch statuses.)
5. **Plateau escape and noise awareness.** After N iterations with no keep, start fresh-context forks from the best commit. Look again at earlier discards when a new result removes their reason. Do not keep a gain that is inside the noise floor. (uditgoenka fork-and-flush; pi revisiting discards and MAD confidence; driveline noise floor.)

Also worth a small place: the Karpathy simplicity criterion, Ralph `session_id` isolation, pi `finalize` into reviewable branches, and the uditgoenka blind judge panel for the LLM-judge tier.

## Gaps that no candidate fills

1. **Goal ratchet.** No tool raises the target or adds the next objective after success (for example: correct first, then faster). driveline segments come closest, but a human must start each new segment.
2. **"What blocks you?" check-in.** Claude Code `/goal` has idle check-ins, and Codex reports blockers when the budget ends. No tool asks for blockers during the run, or turns a blocker into a new climb or a tool request.
3. **Verifier tier ladder and intake rejection.** No tool has the order programmatic, then reference diff, then LLM judge panel. No tool rejects a verifier that only a human can run. Most tools need a number. uditgoenka `reason` mode is the only judge-panel loop, and it is a separate mode.
4. **Tool and budget intake.** Intake asks for a metric and a scope. It does not ask which tools, credentials, or services the climber can use. Budget is mostly an iteration count. Token and time budgets are soft or absent (jthack says this directly).
5. **Harness-agnostic with native goals.** driveline supports Claude Code and Codex, but with its own Stop hook and supervisor, not the native goals. codex-autoresearch uses the native Codex goal but supports Codex only. No tool uses the native goal on both harnesses.
6. **Non-numeric outcomes.** Almost all loops need one number. A pass/fail suite, a diff against a reference artefact, or a judged artefact is not a first-class verifier type.
