---
name: graceful-pause
description: Pause work without wasting tokens. In-flight multi-shot reviews finish; all other work stops.
disable-model-invocation: true
---

# Graceful Pause

Pause now. Tokens are the scarce resource. CPU and wall-clock time are cheap.

For each piece of running work, ask one question: **does a stop now waste tokens that were already spent?**

- **Finish**: an ephemeral session that cannot resume. A stop loses all of its tokens. The main case is `multi-shot-review`: its classifier, review waves, and judges. Standalone `codex exec` or `claude -p` runs are the same.
- **Stop**: builds, tests, dev servers, and other CPU work. Research. Workflow runs.
- **Detach**: a job that you can recover by an ID, such as a remote research task. Let it run. Record its ID.

## Steps

1. Start no new work that spends tokens.
2. Stop each stop-class job that you started. Kill background processes by PID. Leave partial edits as they are.
3. Send the path to this file to each subagent that you started. Each subagent applies the same pause to its own work.
4. Join each finish-class job until it exits. Record where its output is (for a multi-shot review, the `REVIEW_DIR`). Leave the results unread until the resume.
5. Wait for the pause report of each subagent. Then report in a short list:
   - Finished: each job and its output location.
   - Stopped: each job and its rerun command.
   - Detached: each job and its ID.
   - Partial edits: each file.
   - Resume: the next step.

The pause is complete when no process or agent in your tree spends tokens, and your report includes each subagent report. Then stay idle until the user resumes.
