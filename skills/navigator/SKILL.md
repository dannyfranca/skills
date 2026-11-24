---
name: navigator
description: "Plan a big initiative with few stops for the user: one live kickoff, decisions by a council of models, and one review for each wave."
disable-model-invocation: true
---

In a planning session, the user confirms most of the agent's recommendations and changes only a few. **Navigator** moves the user's time to the few: a live kickoff, the decisions that only the user can make, and one review for each **wave**. All other decisions are made without the user and recorded, so that the review can change them later.

Navigator adds no planning method. `atlas` charts the initiative as nodes, and `wayfinder` finds the way through each node as decision tickets. Navigator runs both and gives the answers that those skills expect from the user. The end of each wave is always a spec and a ticket split for each node.

## Words

- **Charter**: what the user settled at the kickoff. It is the Goal, the Non-goals, and the `## Charter` section of the atlas file.
- **Tier**: who decides a question. A **reserved** decision belongs to the user. A **routine** decision belongs to the session. All others belong to the **council**: models from two or more providers that answer blind.
- **Park**: set a decision aside for the user and continue with all work that it does not block.
- **Memo**: the text that brings one parked decision to the user.
- **Ledger**: the record on each ticket of who decided what. It tells each session what the user has seen.
- **Wave**: the frontier nodes of the atlas that are planned together, from map to ticket split, and reviewed one time.
- **Driver**: the session that launches all other sessions. It makes no decision.

## The stops

A session talks to the user only at these stops:

| Stop | Session | When |
| --- | --- | --- |
| Kickoff and node checkpoint | `chart` | One time, at the start |
| Parked batch | `ask` | When no ticket of the wave can start without the user |
| Re-plan checkpoint | `replan` | Only on a change of shape |
| Wave review | `review` | One time for each wave, and again for each delta |

In all other sessions, a question for the user becomes a parked decision.

## Where things are

- **Atlas file**: `docs/initiatives/<slug>/README.md` in the repo, as `atlas` defines it.
- **Run directory**: `~/.atlas/<repo name>/<slug>/`. It holds the run state, the checkpoint view, and one `wave-<n>/` directory with the specs, the splits, the reports, and the videos. It is not committed.
- **Principles**: `docs/agents/principles.md` in the repo (what each plan for this product must respect) and `~/.agents/principles.md` (how the user works in all repos). A principle answers a question in the same way as an answer from the user.
- **Config**: [`navigator.toml`](navigator.toml) holds the defaults. A `.agents/navigator.toml` in the repo or in the home directory replaces them key by key, and the repo file wins.
- **Tracker**: the tracker doc of the repo says how labels, blocking, and child issues work. Where the tracker has no labels, a `Labels:` line in the ticket holds them.

**Run a skill** means: call the Skill tool with its name. When the tool does not offer the skill, read its `SKILL.md` in the skills directory and follow it.

## Invocation

| Arguments | Role | Read |
| --- | --- | --- |
| A loose idea | `chart` | [`CHART.md`](CHART.md) |
| An atlas path | `driver` | [`DRIVER.md`](DRIVER.md) |
| `replan <atlas path>` | `replan` | [`CHART.md`](CHART.md) |
| `plan`, `decide`, `research`, `proto`, `task`, or `spec`, then `<atlas path> <target>` | The named role | [`WORK.md`](WORK.md) |
| `ask <atlas path> <wave>` | `ask` | [`ASK.md`](ASK.md) |
| `review <atlas path> <wave>` | `review` | [`REVIEW.md`](REVIEW.md) |

Each session has one role and one target. Before the first step of the role, read the config, the two principles files, and the Goal, Non-goals, and Charter of the atlas file. The `chart` role writes the atlas file, so it reads only the config and the principles. The `driver` role reads only the config.
