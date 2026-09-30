---
name: graceful-pause
description: Pause work without wasting tokens. In-flight multi-shot reviews finish; all other work stops.
disable-model-invocation: true
---

# Graceful Pause

Pause now. Tokens are the scarce resource. CPU and wall-clock time are cheap.

For each piece of running work, ask one question: **does a stop now waste tokens that were already spent?**

- **Yes: finish it.** An ephemeral session that cannot resume loses all of its tokens when it stops. The main case is `multi-shot-review`: its classifier, review waves, and judges.
- **No: stop it.** Builds, tests, dev servers, and other CPU work. Research. Subagents that can resume.

## Steps

1. Start no new work that spends tokens: no new review wave, fix round, subagent, or research.
2. Stop each stop-class job that you started. Kill background processes by PID.
3. Apply the question to each subagent:
   - It runs finish-class work, or its own subagents can: send it the path to this file, so it applies the same pause to its own tree.
   - It can resume and runs no finish-class work: stop it. Record its resume handle.
   - You cannot tell: send it the path. A relay costs little; a wrong stop wastes the tokens of a whole review.
4. Join each finish-class job until it exits. Record where its output is (for a multi-shot review, the `REVIEW_DIR`). Leave the results for the resume: the next wave or fix round waits.
5. Report in a short list:
   - Finished: each job and its output location.
   - Stopped: each job and its resume handle or rerun command.
   - Resume: the next step.

The pause is complete when no process or agent in your tree spends tokens, and each finish-class job has exited with its output recorded.
