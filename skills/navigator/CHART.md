# Chart and re-plan

The two roles that write the atlas file. Both talk to the user.

## chart

The kickoff. It is the one long live session of the initiative, so settle here all that the later sessions cannot settle without the user.

Run `atlas` in its Chart mode, with these changes:

1. **Step 1 also settles the charter.** Grill the user until each item has an answer:
   - **Appetite**: the size that the user accepts for the initiative, in tickets of 700 to 1000 changed lines.
   - **Non-negotiables**: the constraints that no decision can break.
   - **Reserved decisions**: the decisions that the user keeps, in addition to the reserved classes of [`DECIDE.md`](DECIDE.md).
   - **Core domain model**: each term that decisions in two or more nodes will depend on. Write the terms to the glossary as `domain-modeling` says.
2. **Step 2 also asks which nodes need a new UI.** A node needs one when its outcome is a screen or a flow that does not exist yet. A change to an existing screen does not count.
3. **Step 3 always gives an atlas**, also for one node. The route of each node is `wayfinder`, so that each decision becomes a ticket.
4. **Hold the node checkpoint after step 4.** Build the checkpoint view as [`REPORT.md`](REPORT.md) says and open it for the user. Merge, split, or change the nodes as the user says, and build the view again after each change. Done when the user approved each card.
5. **Step 5 adds two parts to the atlas file**: a `## Charter` section after Non-goals, and a `New UI` field in each node entry.

   ```markdown
   ## Charter

   - **Appetite**: <number of tickets>
   - **Non-negotiables**: <each constraint>
   - **Reserved decisions**: <each decision that the user keeps>
   ```

   ```markdown
   - **New UI**: yes | no
   ```
6. **In place of step 6, start the run.**
   1. Create the run directory, and move the checkpoint view into it.
   2. Create the labels `navigator:unseen`, `navigator:parked`, `navigator:shape`, `navigator:draft`, and `navigator:charted` in the tracker.
   3. When `docs/agents/principles.md` does not exist, ask the user for the principles of this product, write them one line each, and commit the file.
   4. Launch the driver as [`HARNESS.md`](HARNESS.md) says. Tell the user the thread name of the driver, and end the session.

## replan

The driver launches this role after a build, and when a ticket has the label `navigator:shape`.

Run `atlas` in its Re-plan mode, with these changes:

1. **Step 1 has two more sources.** Each open ticket with the label `navigator:shape` is a finding: its memo states the conflict. Each `wave-<n>/signed-off` file lists the published tickets of each node: a node is built when each of them is closed.
2. **Step 4 is the re-plan checkpoint.** On a change of shape, grill the user as `atlas` says. Show each new or changed node as a card of the checkpoint view, and continue when the user approved each card. Each other question goes through `DECIDE.md`.
3. **Answer each shape ticket.** Post the decision of the user on the ticket as [`DECIDE.md`](DECIDE.md) says, remove the label, and close the ticket when the `Remaining` line of its memo is `none`.
4. **Bring the maps in line with the atlas.** When a node has a new name, change the title of its map. When a node is gone, close its map and its open tickets. When the change of shape changes a decision of a closed ticket, or what a planned node needs or gives, follow the Change part of `DECIDE.md`. Done when each map matches one node of the atlas, and no spec remains that was built on the old shape.
5. **In place of the briefs of step 5, end the session.** The driver continues from the atlas file.

A re-plan with no change of shape runs with no question to the user.
