# Work sessions

A work session does the work of one target and then ends. It never talks to the user: each question that a skill tells it to ask the user goes through [`DECIDE.md`](DECIDE.md). The driver learns the result from the tracker and the run directory only, so the session is complete when its completion state below is true there.

Work in the repo root and change no code there. The `proto` and `task` roles change code only in the worktree that the driver gave them.

## plan

Target: one node of the atlas. Result: the Wayfinder map of the node.

Run `wayfinder` in its "Chart the map" mode, with these changes:

1. The **destination** is "a spec and a ticket split for the node `<node name>`". Take the scope from the node entry in the atlas file and from each node that needs what this node gives.
2. The **title** of the map is the one that `SKILL.md` defines. When a map with this title exists already, an earlier session did not finish it: continue that map.
3. The **Notes** hold these lines:
   - `This map runs under navigator. Each session on it reads SKILL.md of the navigator skill first, and takes the work role of its ticket type.`
   - `Atlas: <atlas path>. Plan this node only. The atlas is read-only.`
4. **Each decision is a ticket**, also when the way is clear, because the ledger is on the tickets. A node with no fog gets a map with few tickets.
5. A node with `New UI: yes` gets a UI `prototype` ticket first. That ticket blocks each ticket that decides the behaviour or the data behind that UI.
6. Create the `research` tickets and leave them open. The driver starts them.
7. When the map cannot keep the Outcome, the Done signal, the Needs, or the Gives of the node entry, create the map with that one question as its only ticket, park it with the label `navigator:shape`, because only the re-plan can change the node entry, and end the session. The driver launches this role again after the re-plan.
8. As the last act, add the label `navigator:charted` to the map.

Complete when the map has the label `navigator:charted`, or when it has one ticket with the label `navigator:shape`.

## decide, research, proto, task

Target: one ticket. `decide` is for a `wayfinder:grilling` ticket, `proto` is for `wayfinder:prototype`, and `research` and `task` are for the types with the same name.

Run `wayfinder` in its "Work through the map" mode for the ticket in the brief, with these changes:

| Role | Change |
| --- | --- |
| `decide` | Build the design tree as `grilling` does. Each question goes through `DECIDE.md` in place of the user. |
| `research` | No change. A fact is not a decision, so the resolution has no ledger block. |
| `proto`, logic | Build the prototype. The verdict is a session decision: take it from the result of the prototype. |
| `proto`, UI | Build the variants. The verdict is a session decision, unless the charter lists it. Give the link of each variant in the ledger block, or in the memo when you park it. |
| `task` | Do the work. When only a human can do a step, park the ticket with the exact checklist as the memo. |

When the answer conflicts with the atlas (a cross-node decision, the Needs or the Gives of the node), park it as a Shape decision.

Create each new ticket with the label `navigator:draft`, and remove the label after its blocking edges exist. The driver starts no ticket that has it.

A ticket can hold an answer from the user to an earlier memo. That answer is the decision: record it with `Decided by: user` and continue with the work that remains.

Complete when the ticket is closed with its ledger record and the map is updated as `wayfinder` says, or when the ticket is open with a memo and a park label.

## spec

Target: one wave number and one node whose map has no open ticket. Result: the draft spec and the draft ticket split, in the directory of the node in the run directory. Nothing goes to the tracker before the wave review signs off.

1. **Write `spec.md`.** Use the template of `to-spec`. The source is the Decisions so far of the map and the ledger records behind them. Done when each closed ticket of the map shows in the spec or in its Out of Scope.
2. **Write `split.md`.** Follow steps 1 to 3 of `to-tickets` with the spec as the plan. Size each ticket at 700 to 1000 changed lines, tests excluded. Use the ticket template of `to-tickets`, one section for each ticket, and add a `Size:` line with the estimate. Done when each Implementation Decision of the spec belongs to a ticket, and each ticket names its blockers.

A question in either step goes through `DECIDE.md`. Record its decision on a new `wayfinder:grilling` ticket of the map, created with the label `navigator:draft`, and close that ticket, so that the ledger holds it. Remove the draft label when the ticket is closed or parked. A parked question leaves that ticket open, and the node gets its spec after the answer.

Complete when both files exist and the map has no open ticket, or when a ticket is open with a memo and a park label.
