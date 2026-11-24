# Shadow

The `shadow` role measures the Tier part of [`DECIDE.md`](DECIDE.md) against the user. It replays **rounds** that the user answered by hand: it hides each reply, gives each question its tier, and then compares the tiers with what the user did. Run it before and after a change to the tiers, on the same rounds. It changes no ticket and no file of a repo.

- A **round** is one message in which an agent asked the user one or more questions, each with a recommendation, together with the reply of the user.
- A **plan-changing override** is a question where the answer of the user is not the recommendation as it is, and the difference changes the scope, the direction, the architecture, or the cost of the plan, so that later decisions or more than one ticket of work depend on it.

## Numbers

- **Reserved**: the share of all questions that have the tier value of a reserved class. The user accepts 25% at most.
- **Caught**: the share of the plan-changing overrides that have the tier value of a reserved class.
- **Agreement**: of the questions with another tier value and an outcome other than `unclear`, the share with the outcome `accept` or `modify`.

## Steps

The run directory is `~/.atlas/_shadow/<date>/`. When it exists, add `-2` to the name, then `-3`. When the argument is the directory of an earlier shadow run, copy its `rounds.json`, `replies.json`, and `labels.json` to the new run directory and do only steps 2, 4, 5, and 6, so that the two runs measure the same rounds with the same labels.

1. **Collect the rounds** from the session transcripts of each harness that the user works in (Claude Code: `~/.claude/projects`, Codex: `~/.codex/sessions`). The `grilling` skill gives a question and its recommendation a mark each: a round is a message of a main session that has these marks, and its reply is the next message that the user typed. Tool results, notifications, and the summaries that a harness wrote are not replies. Take each round that is in no earlier run directory. Write two files:
   - `rounds.json`: a list with one object for each round: `id`, `project`, `context` (the text before the questions), and `questions`. Each question has an `id` (`<round id>.<its position>`), the `question`, and the `recommendation`.
   - `replies.json`: an object with one key for each round id: `reply`, and `next` (the two messages of the user that follow the reply, because they can show what the reply meant).

   Done when each round has a reply, and `rounds.json` holds no text of a reply.
2. **Give each question its tier, blind.** Launch subagents on the `thinking` model. Each one has a fresh context window and gets a file with 150 questions at most, the path of `DECIDE.md`, and the path of `~/.agents/principles.md`. It applies the Precedent part and the Tier part as a session does. A round has no charter, no atlas file, and no repo of a run, so that principles file is the only source of precedent, the subagent judges the Goal, the scope, and the shape of the plan from the round, and the class Listed does not apply. Its result is one object with a key for each question id and the value `precedent`, `scope`, `shape`, or the kind. Cut the question files from `rounds.json` into a directory outside the run directory, so that no subagent can read a reply. Merge the results into `tiers.json`. Done when `tiers.json` has each question id.
3. **Label each reply.** Launch subagents on the `execution` model with the rounds and their replies. The label of a question has two parts:
   - `outcome`: `accept` (the recommendation as it is), `modify` (the recommendation with a change that keeps its direction), `override` (a different option, or no to the recommendation), `redirect` (the user changed what is decided), or `unclear` (the reply does not answer the question).
   - `plan_changing`: true for a plan-changing override, and false for all others. It can be true with each outcome other than `accept` and `unclear`.

   Merge the results into `labels.json`: an object with a key for each question id and the two parts as its value. Done when `labels.json` has each question id.
4. **Count with a script**, from `tiers.json` and `labels.json`: the three numbers for the tiers of `DECIDE.md`, and for each tier value its share of the questions and its rate of plan-changing overrides. Write the result to `result.md`.
5. **Report to the user**:
   - The three numbers, each with its counts, next to those of the baseline or of the earlier run.
   - The table of the tier values.
   - Each plan-changing override that the tiers did not give to the user, in one line: the question, the recommendation, and the answer. Each one can give a principle line.

   With 65 plan-changing overrides, the Caught of the baseline can be 12 points off. Tell the user when a run has fewer.
6. **Propose a change to the tiers** only when the counts show it. List the tier values by their rate of plan-changing overrides, highest first. Leave out `precedent`, and each value with fewer than 5 plan-changing overrides: such a count is too small to move a tier. Go down the list: a value is a reserved class when its rate is above the rate of all questions and Reserved stays at 25% or below with it. Stop at the first value that fails. The class Listed is the choice of the user, and no count changes it. When the result is not the tiers of `DECIDE.md`, show the user the counts and the change. Change `DECIDE.md` only after the user approved the change.

## Baseline

2026-11-24: 200 rounds, 1,080 questions, 65 plan-changing overrides (6.0%). The files are in `~/.atlas/_shadow/2026-11-24-2/`. `~/.agents/principles.md` was written from other rounds, which are not in the baseline.

| Tier value | Share of the questions | Rate of plan-changing overrides |
| --- | --- | --- |
| `scope` | 19.8% | 12.1% |
| `shape` | 13.5% | 9.6% |
| `stakes` | 8.8% | 7.4% |
| `precedent` | 2.1% | 4.3% |
| `plain` | 35.5% | 3.4% |
| `assumption` | 3.0% | 3.1% |
| `routine` | 17.3% | 1.6% |

| Reserved classes | Reserved | Caught | Agreement |
| --- | --- | --- | --- |
| None | 0% | 0% | 90.3% |
| Scope | 19.8% | 40.0% | 91.9% |
| Scope and Shape (the tiers of `DECIDE.md`) | 33.3% | 61.5% | 92.4% |
| Scope, Shape, and `stakes` | 42.1% | 72.3% | 93.0% |

Two more results set the tiers:

- A council of four models from two providers, which answered blind, agreed with the user no more than the recommendation did. In the first version of the tiers, its votes for the user raised Reserved from 34% to 58%.
- The wording of the classes moves Reserved and Caught together. Three wordings gave a Reserved of 17% to 33%, each with a Caught of about two times its Reserved. Thus a new wording does not catch more for the same number of stops: only the choice of the reserved classes sets the number of stops.
