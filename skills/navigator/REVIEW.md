# Review

The `review` role shows the user one wave and takes the corrections. It is the moment when the user sees each decision that was made without them, so the session is complete only when the user understands the wave and signs it off, or when corrections are open for the driver.

The wave directory tells the mode. With no `report.html`, this is the **first review**. With one, this is a **delta review**: its subject is only what changed after the newest report.

1. **Collect the wave**: the ledger record of each closed ticket of each map of the wave, the `spec.md` and `split.md` of each node, the link of each prototype, and the atlas file as it was at the `atlas_commit` of the wave in `run.toml`. For a delta review, collect only the ledger blocks that were posted after the newest report, and the splits that were written after it.
2. **Rank the decisions.** The score of a decision is (1 + the number of decisions that depend on it) × the weight of its kind: 2 for `shape`, `stakes`, and `assumption`, 0 for `routine`, and 1 for all others. The **top cards** are the `review_top_cards` decisions with the highest scores that have `Decided by` other than `user`.
3. **Build the report and the video** in two parallel subagents. The report follows [`REPORT.md`](REPORT.md). The video comes from `explainer-video`: its subject is the top cards and the ticket split, and the report embeds it. A delta review builds a delta report and no video.
4. **Open the report for the user**, and say in one line what it holds.
5. **Take the verdict on the shape, then quiz the top cards.** Ask the user to look at section 3 of the report, and to name each card to change. Then give one top card for each message. Ask a question that the user can answer only when they understand the trade-off of the decision. Correct a wrong answer from the ticket, then ask for the verdict: keep or change.
6. **Take the questions and the nitpicks of the user** on all other parts: the other decisions, the split, the prototypes. Answer from the tickets. Continue until the user says that they have no more.
7. **Apply each correction** as the Change part of [`DECIDE.md`](DECIDE.md) says. A correction of the split is a decision too: record it on a new closed ticket of the map with `Decided by: user`, and delete `spec.md` and `split.md` of that node, so that the next split keeps the correction. A correction of the atlas that no ticket holds is a decision too: record it on a new `wayfinder:grilling` ticket of the map of a node that it changes, with `Decided by: user`, and give that ticket the label `navigator:shape` as `DECIDE.md` says.
8. **Offer principles.** A correction is an answer that only the user had, and a principle gives that answer to each later session. For each correction, write the preference behind it as one line, and ask the user to approve it. Add each approved line to `docs/agents/principles.md` when it is about the product, and to `~/.agents/principles.md` when it is about how the user works.
9. **Close the session.**

   | State | Action |
   | --- | --- |
   | A correction was applied in this session | Tell the user that a delta review follows, and end the session. The driver runs the corrections. |
   | No correction was applied, and the user signs off | Remove `navigator:unseen` from each ticket of the wave. For each node, publish `spec.md` as `to-spec` says, and publish `split.md` with the ticket skill of the tracker (`to-tickets-gh` or `to-tickets-local`) from its publish step: this review replaced its quiz. Then write the file `signed-off` in the wave directory, with one line for each node: its name, and the links of its spec and its tickets. |

   In the two states, do the wake step, as the last step: the user must not have to do it.
