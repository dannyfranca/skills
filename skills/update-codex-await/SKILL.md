---
name: update-codex-await
description: Update and install Danny's Codex fork whose empty write_stdin waits use the configured background terminal timeout.
disable-model-invocation: true
---

# Update Codex Await

1. Run `scripts/update.sh prepare`.
2. If it reports `UP_TO_DATE`, report the versions and stop.
3. Inspect the new stable tag and the patch diff. Preserve this contract:
   - Empty `write_stdin` waits for `background_terminal_max_timeout`, ignoring the model-requested yield time.
   - Process exit returns early.
   - Non-empty stdin keeps upstream interactive bounds.
4. If upstream now satisfies the contract without the patch, run `scripts/update.sh abort`. Report that the fork patch is obsolete; do not change the installed CLI.
5. If cherry-pick conflicts exist, resolve only what is needed for the contract. Keep one patch commit atop the stable tag, then run `git cherry-pick --continue`.
6. Run `scripts/update.sh finish`.

Never stash, reset user work, change Codex config, target prereleases, or run the full workspace test suite.
