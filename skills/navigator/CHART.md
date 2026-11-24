# Chart and re-plan

The two roles that write the atlas file. Both talk to the user.

## chart

The kickoff. It is the one long live session of the initiative, so settle here all that the later sessions cannot settle without the user.

Run `atlas` in its Chart mode, with these changes:

1. **Step 1 also settles the charter.** Grill the user until each item has an answer:
   - **Appetite**: the size that the user accepts for the initiative, in tickets of 700 to 1000 changed lines.
   - **Non-negotiables**: the constraints that no decision can break.
   - **Reserved decisions**: the decisions that the user keeps, in addition to the scope decisions that [`DECIDE.md`](DECIDE.md) reserves.
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

1. **Step 1 has two more sources.** Each open ticket with the label `navigator:shape` holds a decision that changes the atlas. The decision is made: apply it as it is, and do not take it through `DECIDE.md` again. Each `wave-<n>/signed-off` file lists the published tickets of each node: a node is built when each of them is closed.
2. **Step 4 asks the user only for a reserved decision.** Each question that `atlas` gives to the user in this step goes through [`DECIDE.md`](DECIDE.md). For a reserved one, grill the user as `atlas` says, show each node that the answer changes as a card of the checkpoint view, and continue when the user approved each card. Record each decision that has no ticket on a new closed `wayfinder:grilling` ticket of the map of a node that it changes, so that the wave review shows it. When that node has no map, create the map: the `plan` role continues it.
3. **Release each shape ticket.** Remove the label `navigator:shape`. Then close the ticket, with the label `navigator:unseen` when [`DECIDE.md`](DECIDE.md) asks for it. Keep the ticket open when it has the label `navigator:parked`, or when the `Remaining` line that came with the shape label names work: the driver starts a ticket that is open with no label.
4. **Bring the maps in line with the atlas.** When a node has a new name, change the title of its map. When a node is gone, close its map and its open tickets. When an atlas change of this re-plan makes the decision of a closed ticket wrong, open that ticket again with a comment that states the change, and do steps 2 to 4 of the Change part of `DECIDE.md` for it. When an atlas change changes what a planned node needs or gives, delete `spec.md` and `split.md` of that node. Done when each map matches one node of the atlas, and no spec remains that was built on the old atlas.
5. **Step 5 prints no brief.** Commit and push, or close, as `atlas` says. Then end the session: the driver continues from the atlas file.

A re-plan with no reserved decision runs with no question to the user.
