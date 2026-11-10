# Prior-art agent loops

Research ticket: [#60](https://github.com/dannyfranca/skills/issues/60). Map: [#59 Hill-climb skill](https://github.com/dannyfranca/skills/issues/59).

## Question

What process do existing long-run agent loops use? Which parts must `hill-climb` copy?

## Short answer

All the systems use the same core. A fixed verifier that the agent cannot edit gives a score. The agent makes one change, runs the verifier, and keeps the change only if the score improves. A file on disk holds the goal. A second file on disk holds the attempt history. Git holds the best-so-far state. A new session reads these files and continues. The ranked list of primitives is in [Ranked primitives to copy](#ranked-primitives-to-copy).

## Sources examined

| System | Primary source |
| --- | --- |
| Karpathy autoresearch | [program.md](https://github.com/karpathy/autoresearch/blob/master/program.md), [README.md](https://github.com/karpathy/autoresearch/blob/master/README.md) |
| Ralph Wiggum loop (Geoffrey Huntley) | [ghuntley.com/ralph](https://ghuntley.com/ralph/), [ghuntley.com/loop](https://ghuntley.com/loop/), [The Ralph Playbook](https://github.com/ghuntley/how-to-ralph-wiggum) |
| Ralph Loop plugin for Claude Code (Anthropic) | [plugins/ralph-loop/README.md](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/ralph-loop/README.md) |
| Anthropic long-running harness | [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) |
| Anthropic generator-evaluator harness | [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps) |
| AlphaEvolve (Google DeepMind) | [AlphaEvolve paper, arXiv 2506.13131](https://arxiv.org/abs/2506.13131) |
| OpenEvolve | [README](https://github.com/algorithmicsuperintelligence/openevolve), [configuration.md](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/docs/configuration.md) |
| ShinkaEvolve (Sakana AI) | [README](https://github.com/SakanaAI/ShinkaEvolve), [paper, arXiv 2509.19349](https://arxiv.org/abs/2509.19349) |

## Process of each system

### Karpathy autoresearch

Source: [program.md](https://github.com/karpathy/autoresearch/blob/master/program.md).

- **Setup with the human.** The agent and the human agree on a run tag. The agent creates a new branch `autoresearch/<tag>`. The agent reads the in-scope files. The agent creates `results.tsv` with only a header row. The human confirms. Then the loop starts.
- **Goal file.** `program.md` is the goal and the protocol. The human edits it. The agent does not. The README calls it "a super lightweight skill".
- **Fixed verifier.** `prepare.py` holds the evaluation (`evaluate_bpb`). The agent must not modify it. The agent must not add dependencies. One metric: `val_bpb`, lower is better.
- **One editable file.** The agent edits only `train.py`.
- **Fixed attempt budget.** Each run trains for 5 minutes wall clock. If a run takes more than 10 minutes, the agent kills it and records a failure.
- **Baseline first.** The first run always uses the unchanged code.
- **The loop.** Commit the idea. Run with output sent to `run.log` ("do NOT use tee or let output flood your context"). Grep the metric. If the grep is empty, the run crashed: read `tail -n 50 run.log`. Record the row. If the metric improved, keep the commit and advance the branch. If the metric is equal or worse, `git reset` to the start point.
- **Attempt log.** `results.tsv` has 5 columns: `commit`, `val_bpb`, `memory_gb`, `status` (`keep`, `discard`, `crash`), `description`. The log stays untracked by git. Thus a revert does not delete the log.
- **Simplicity tie-break.** "All else being equal, simpler is better." A small gain with ugly complexity is not worth it. Equal results with less code is a keep.
- **Crash policy.** Fix a simple error (typo, missing import) and run again. If the idea is broken, log `crash` and move on.
- **Never stop.** "Do NOT pause to ask the human if you should continue." The loop runs until the human interrupts it.
- **Plateau policy.** "If you run out of ideas, think harder": read papers in the code, read the in-scope files again, combine near-misses, try more radical changes. Rewind to an older commit "very very sparingly (if ever)".
- **No restart protocol.** The program assumes one long session. The branch tip and `results.tsv` are the only durable state.

### Ralph Wiggum loop

Sources: [ghuntley.com/ralph](https://ghuntley.com/ralph/), [The Ralph Playbook](https://github.com/ghuntley/how-to-ralph-wiggum).

- **Outer loop, fresh context.** "In its purest form, Ralph is a Bash loop": `while :; do cat PROMPT.md | claude-code ; done`. Each iteration starts a new agent process with an empty context.
- **Same stack each loop.** "Deterministically allocate the stack the same way every loop." Each iteration loads the same files: the prompt, the specs (`specs/*`), the plan (`fix_plan.md`, later `IMPLEMENTATION_PLAN.md`), and `AGENT.md`.
- **One item per loop.** "One item per loop. I need to repeat myself here—one item per loop." The agent chooses the most important item from the plan.
- **Plan file is shared state.** The plan persists on disk between isolated iterations. The agent updates it when it finds a bug or finishes an item.
- **The plan is disposable.** When the plan goes wrong, the human deletes it and runs a planning loop to make a new one. The Playbook splits this into a PLANNING mode (gap analysis only, no commits) and a BUILDING mode.
- **Backpressure.** Tests, type checks, builds, linters, and scanners reject bad output. "The wheel has got to turn fast." The Playbook allows an LLM-as-judge with binary pass or fail for subjective criteria.
- **Operational learnings file.** When the agent learns a correct command, it updates `AGENT.md` (brief).
- **Commit when green.** When the tests pass, the agent updates the plan, commits, and pushes. When there are no build or test errors, it creates a git tag.
- **Context discipline.** The main context is a scheduler. Subagents do expensive reads. Only one subagent runs build and tests, because many parallel validators give "bad form back pressure".
- **Known failure modes.** The agent decides that code is missing without a full search, so the prompt says "don't assume not implemented". The agent writes placeholder code to make the build pass, so the prompt says "DO NOT IMPLEMENT PLACEHOLDER OR SIMPLE IMPLEMENTATIONS". The human tunes the prompt with "signs" after each observed failure.
- **Human recovery.** Sometimes the codebase does not compile and the agent cannot fix it. The human chooses: `git reset --hard` and restart, or write a new plan.
- **Sandbox.** The loop needs `--dangerously-skip-permissions`. The Playbook says the sandbox is then "your only security boundary".

The Claude Code Ralph Loop plugin ([README](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/ralph-loop/README.md)) differs. A Stop hook blocks the exit and feeds the same prompt back "inside your current session". It stops on an exact `--completion-promise` string or on `--max-iterations`. The README says that the promise uses exact string match, so it cannot show "SUCCESS" versus "BLOCKED". It says `--max-iterations` is the primary safety mechanism. Huntley's Ralph page links a video with the note "watch this to learn why the claude code plugin isn't it". His text principle is a fresh, identical context for each loop.

### Anthropic: Effective harnesses for long-running agents

Source: [anthropic.com/engineering/effective-harnesses-for-long-running-agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents).

- **Problem.** Each new session "begins with no memory of what came before". Compaction "isn't sufficient".
- **Failure 1: too much at once.** The agent tries to one-shot the app, runs out of context mid-feature, and leaves the next session a half-done, undocumented state.
- **Failure 2: premature victory.** A later session sees progress and declares the job done.
- **Initializer agent.** The first session uses a different prompt. It writes `init.sh` (starts the dev server), `claude-progress.txt` (log of what agents did), a feature list, and an initial git commit.
- **Feature list as goal file.** More than 200 end-to-end features in JSON, each with `"passes": false`. The coding agent may change only the `passes` field. "It is unacceptable to remove or edit tests." JSON is used because "the model is less likely to inappropriately change or overwrite JSON files compared to Markdown files".
- **Coding agent.** Each later session works on one feature. It commits with a descriptive message and writes a progress summary. Git lets the agent "revert bad code changes and recover working states".
- **Start-of-session ritual.** `pwd`. Read git log and the progress file. Read the feature list and choose the highest-priority failing feature. Run `init.sh`. Run a basic end-to-end test first, to find a broken state before new work.
- **Verification.** The agent marked features done without end-to-end tests. Browser automation, used "as a human user would", improved results a lot.

### Anthropic: Harness design for long-running application development

Source: [anthropic.com/engineering/harness-design-long-running-apps](https://www.anthropic.com/engineering/harness-design-long-running-apps).

- **Context reset versus compaction.** "Context anxiety": some models stop early when they think the context is nearly full. A reset gives a clean slate. It needs a handoff artifact with enough state. With Opus 4.5 the author removed resets and ran "one continuous session" with automatic compaction.
- **Self-evaluation is lenient.** Agents "confidently [praise] the work" that they made. A separate evaluator is "far more tractable" to tune for skepticism. Out of the box, the evaluator found real issues, then "talk[ed] itself into" approving them. The author tuned the evaluator prompt by reading its logs.
- **Graded criteria with hard thresholds.** Each criterion had a hard threshold. If one criterion fell below it, the sprint failed. Few-shot examples calibrated the evaluator and "reduced score drift across iterations".
- **Sprint contract.** Before each sprint, the generator and the evaluator agreed in files on what "done" means and how to verify it.
- **Refine or pivot.** After each evaluation, the generator chose to refine the current direction or to pivot to a different approach.
- **Last is not always best.** "I regularly saw cases where I preferred a middle iteration over the last one." Scores rose, then plateaued.
- **Scaffolding goes stale.** "Every component in a harness encodes an assumption about what the model can't do on its own." With Opus 4.6 the author removed sprints and kept the planner and a final evaluator pass.

### AlphaEvolve

Source: [arXiv 2506.13131](https://arxiv.org/abs/2506.13131).

- **User supplies an evaluator `h`.** It returns a dictionary of scores. Tasks that need manual experiments are out of scope.
- **Evolve blocks.** Comments `# EVOLVE-BLOCK-START` and `# EVOLVE-BLOCK-END` mark the code that can change. The rest is a fixed skeleton.
- **Program database.** All solutions with scores and outputs go into a database. It mixes MAP-Elites and island models to balance exploitation and exploration.
- **Prompt sampling.** The prompt contains past solutions from the database and their scores.
- **Evaluation cascade.** Test cases of increasing difficulty. A candidate goes to the next stage only if it passes the earlier stages. Small-scale checks filter bad programs first.
- **LLM-generated feedback.** Separate LLM calls grade properties that `h` cannot capture (for example simplicity). The grade becomes a score or a reject rule.
- **Multiple scores.** Optimizing many metrics "often improves results for the single target metric", because diverse good programs give varied prompts.
- **Meta prompts.** The system also evolves its own prompts.

### OpenEvolve

Sources: [README](https://github.com/algorithmicsuperintelligence/openevolve), [configuration.md](https://github.com/algorithmicsuperintelligence/openevolve/blob/main/docs/configuration.md).

- Open-source implementation of AlphaEvolve: MAP-Elites plus islands with periodic migration.
- **Cascade evaluation.** `evaluate_stage1` screens candidates. Thresholds gate later stages.
- **Artifacts side channel.** The evaluator returns `stderr`, profiling data, build warnings, and LLM feedback. The next prompt shows them under "Previous Execution Feedback".
- **Inspirations.** The prompt holds the top programs (`num_top_programs`) and diverse programs (`num_diverse_programs`).
- **Diff mutations.** SEARCH/REPLACE diffs by default. A full rewrite happens with a set probability (`full_rewrite_probability`).
- **Stop and resume.** `--target-score` stops the run at a score. `--checkpoint` resumes a saved run. Every component is seeded for reproducible runs.

### ShinkaEvolve

Sources: [README](https://github.com/SakanaAI/ShinkaEvolve), [arXiv 2509.19349](https://arxiv.org/abs/2509.19349).

- Goal: sample efficiency. The paper reports a new state-of-the-art circle packing result "using only 150 samples".
- **Parent sampling.** Balances exploration and exploitation (`weighted`, `power_law`, `beam_search`; `exploitation_ratio`).
- **Novelty rejection sampling.** A candidate too similar to existing code (embedding similarity, `code_embed_sim_threshold` 0.99) is rejected before evaluation. An LLM can judge novelty.
- **Bandit model selection.** UCB chooses which LLM writes the next patch.
- **Stagnation trigger.** With `enable_dynamic_islands`, `stagnation_threshold` generations without improvement spawn a new island. The seed can be the initial program, the best program, or a random archive entry.
- **Meta recommendations.** Every `meta_rec_interval` generations, an LLM reads the history and writes recommendations for the next prompts.
- **Persistent store.** A SQLite database holds all candidates. A run in the same results directory resumes.

## Shared primitives

| Primitive | autoresearch | Ralph | Anthropic harnesses | AlphaEvolve / OpenEvolve / ShinkaEvolve |
| --- | --- | --- | --- | --- |
| Goal file, read each iteration | `program.md` | `PROMPT.md`, `specs/*` | spec, `feature_list.json` | task config, evolve blocks |
| Frozen verifier | `prepare.py`, read-only | tests, build (backpressure) | end-to-end tests, separate evaluator | `evaluate()` / `h` |
| Attempt log | `results.tsv`, untracked | plan file, git log | `claude-progress.txt`, git log | program database (SQLite in Shinka) |
| Best-so-far state | branch tip | last green commit, tags | last clean commit | best program in database |
| Keep or revert | improve: keep; else `git reset` | commit only when green | git revert to a working state | population selection |
| One change per attempt | one idea per commit | one item per loop | one feature per session | one mutation (diff) per candidate |
| Fixed attempt budget | 5 min, kill at 10 min | none in loop; `--max-iterations` | none | parallel eval, cascade |
| Restart after crash or context end | none (one session) | fresh process each loop | start-of-session ritual, `init.sh` | `--checkpoint`, resume directory |
| Plateau handling | think harder, combine near-misses, rare rewind | delete and regenerate plan | refine or pivot | islands, novelty rejection, stagnation spawn, meta recommendations |
| Stop rule | never stop; human interrupts | `--max-iterations`, completion promise | all features pass | `--target-score`, iteration count |
| Learnings file | none | `AGENT.md` | `init.sh` | meta prompts |

## Ranked primitives to copy

Rank is by how many systems use the primitive and by the cost of its absence.

1. **Frozen verifier outside the agent's write scope.** Every system has it. Intake must record the verifier command and the files the agent must not edit (autoresearch `prepare.py`, Anthropic "unacceptable to remove or edit tests"). The loop must refuse to count a score if those files changed.
2. **Keep-or-revert on git, with the branch tip as best-so-far.** One commit per attempt. Keep only on a strict improvement. Revert on equal or worse (autoresearch). Use a dedicated branch per run (`autoresearch/<tag>`). Add the simplicity tie-break.
3. **Append-only attempt log outside the revert path.** One row per attempt: commit, score(s), resource use, status (`keep`, `discard`, `crash`), short description. Keep it untracked or outside the worktree, so that `git reset` does not delete it (autoresearch). Check-in mode reads this log first.
4. **Goal file loaded at the start of each iteration.** The human owns it. It holds the goal, the verifier, the ratchet stages, the budget, and the stop rules. Use a structured format (JSON or a fixed schema) for fields that the agent must not rewrite (Anthropic feature list).
5. **Progress handoff file plus a start-of-session ritual.** Read the goal file, the attempt log, the progress file, and `git log`. Run the verifier on the current tip before new work (Anthropic smoke test). This makes restart after a crash, a compaction, or a context reset the same path as a normal start. The harness can then use one long session or a fresh-process loop.
6. **Baseline first, then one change per attempt.** The first attempt measures the unchanged artefact (autoresearch). Each later attempt changes one thing, so the verifier result maps to one cause (Ralph, Anthropic).
7. **Fixed attempt budget, timeout, and crash policy.** Fixed wall time per verifier run. Kill at a hard limit and log a failure. Fix trivial crashes; log and skip broken ideas (autoresearch). Send verifier output to a file and extract only the metric, so that output does not flood the context.
8. **Explicit stop rules with a structured status.** "Never ask to continue" (autoresearch) plus machine-readable stop reasons: target reached, budget spent, max attempts, blocked. Do not use one exact completion string; it cannot show BLOCKED (Ralph Loop plugin README).
9. **Ratchet as a cascade.** Correctness is the first gate; speed is a later score (AlphaEvolve and OpenEvolve cascade). A candidate that fails the correctness gate is never scored for speed. When a stage target is met, the goal file moves to the next stage.
10. **Plateau protocol.** After N attempts without a keep: read the attempt log, combine near-misses, try a more radical change (autoresearch); choose refine or pivot (Anthropic); reject ideas similar to past discards (Shinka novelty); rewind to an older kept commit rarely. After M more attempts, report BLOCKED through check-in. Population search stays out of v1 (map #59).
11. **Verifier feedback side channel.** Store stderr, failing cases, and profiling output with each attempt. Show the parent's feedback in the next attempt (OpenEvolve artifacts).
12. **Operational learnings file.** When the agent finds the correct command or a trap, it writes a short note (Ralph `AGENT.md`, Anthropic `init.sh`).

## Pitfalls to avoid

- **Verifier tampering.** Agents delete or weaken tests, or write placeholder code that passes the build (Ralph; Anthropic). Guard the verifier files. Check them before each keep.
- **Self-grading leniency.** An agent that grades its own work praises it. An LLM evaluator can find a bug and then approve the work anyway (Anthropic generator-evaluator post). For the LLM-judge tier, use a separate judge, explicit criteria with hard thresholds, and few-shot calibration.
- **Premature victory.** A later session sees progress and declares the job done (Anthropic). Only the verifier and the goal file can declare done.
- **Too much per attempt.** Large changes run out of context mid-way and leave a broken, undocumented state (Anthropic; Ralph "one item per loop").
- **Log lost on revert.** If the attempt log is in git, `git reset` deletes the history of failures. Keep it outside the revert path.
- **Context flood.** Raw verifier output fills the context (autoresearch redirect rule). Heavy reads belong in subagents (Ralph).
- **Context anxiety and compaction loss.** Some models stop early near the context limit. Compaction does not always pass clear instructions forward (both Anthropic posts). All state the next step needs must be on disk.
- **Parallel validation overload.** Many parallel build or test runs give bad backpressure (Ralph). Run one verifier at a time unless the verifier supports parallel runs.
- **Last attempt is not always best.** Scores can plateau, and a human can prefer a middle iteration (Anthropic). Keep each scored attempt addressable by commit.
- **Stale scaffolding.** Each harness part encodes an assumption about model limits. Newer models can make it useless (Anthropic). Keep the protocol small. Make optional parts easy to remove.
- **No sandbox.** Autonomous loops run without permission prompts. The sandbox is the only boundary (Ralph Playbook).
- **Measurement noise (gap).** autoresearch reverts on "equal or worse" and does not handle noisy metrics. A noisy verifier can keep lucky changes. This gap is an inference, not a source claim. The verifier policy ticket must decide repeat runs or a minimum gain.

## Long session or fresh-process loop

The sources disagree. Ralph and the first Anthropic harness use a new context for each iteration. autoresearch and the second Anthropic harness (Opus 4.5 and later, with compaction) use one long session. Primitive 5 makes the choice a harness-adapter detail: if all state is on disk and each start runs the same ritual, both modes work.
