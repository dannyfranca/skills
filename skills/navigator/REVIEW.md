# Review

The `review` role shows the user one wave and takes the corrections. It is the moment when the user sees each decision that was made without them, so the session is complete only when the user understands the wave and signs it off, or when the user says that the review is finished, with corrections open for the driver.

The wave directory tells the mode. With no `report.html`, this is the **first review**. With one, this is a **delta review**: its subject is only what changed after the newest report.

1. **Collect the wave**: the ledger record of each closed ticket of each map of the wave, the `spec.md` and `split.md` of each node, the link of each prototype, and the atlas file as it was at the `atlas_commit` of the wave in `run.toml`. For a delta review, collect only the ledger blocks that were posted after the newest report, and the splits that were written after it.
2. **Number the flagged decisions.** A **flagged decision** has the kind `shape`, `stakes`, or `assumption`, and `Decided by` other than `user`. Give each one a number: the `shape` decisions first, then the others ticket by ticket. The ticket with the most decisions that depend on it comes first.
3. **Build the report and the video** in two parallel subagents. The report follows [`REPORT.md`](REPORT.md). The video comes from `explainer-video`, and the report embeds it. Its subject is each flagged decision, then the ticket split: one chapter for the shape, then one chapter for each ticket. Each decision shows its number, and takes two sentences: the answer and its trade-off. A delta review builds a delta report and no video.
4. **Open the report for the user**, and say in one line what it holds. In a first review, give the link of `video.mp4` in the same message: a phone gets only the HTML file, so the video in the report does not play there.
5. **Take the verdict** in the same message: ask the user to keep all, or to give the number of each card to change. `Keep all` with no question signs the wave off. **Quiz only on request**: when the user asks for it, give one flagged decision for each message, ask a question that the user can answer only when they understand the trade-off of the decision, correct a wrong answer from the ticket, then ask for the verdict: keep or change.
6. **Answer the questions and the nitpicks of the user** on all other parts: the other decisions, the split, the prototypes. Answer from the tickets.
7. **Apply the corrections when the user says that the review is finished.** Until then, note each correction and continue the review. Apply each one as the Change part of [`DECIDE.md`](DECIDE.md) says. A correction of the split is a decision too: record it on a new closed ticket of the map with `Decided by: user`, and delete `spec.md` and `split.md` of that node, so that the next split keeps the correction. A correction of the atlas that no ticket holds is a decision too: record it on a new `wayfinder:grilling` ticket of the map of a node that it changes, with `Decided by: user`, and give that ticket the label `navigator:shape` as `DECIDE.md` says.
8. **Offer principles.** A correction is an answer that only the user had, and a principle gives that answer to each later session. For each correction, write the preference behind it as one line, and ask the user to approve it. Add each approved line to `docs/agents/principles.md` when it is about the product, and to `~/.agents/principles.md` when it is about how the user works.
9. **Close the session.**

   | State | Action |
   | --- | --- |
   | A correction was applied in this session | Tell the user that a delta review follows, and end the session. The driver runs the corrections. |
   | No correction was applied, and the user signs off | Remove `navigator:unseen` from each ticket of the wave. For each node, publish `spec.md` as `to-spec` says, and publish `split.md` with the ticket skill of the tracker (`to-tickets-gh` or `to-tickets-local`) from its publish step: this review replaced its quiz. Then write the file `signed-off` in the wave directory, with one line for each node: its name, and the links of its spec and its tickets. |

   In the two states, do the wake step, as the last step: the user must not have to do it.
