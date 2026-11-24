# Decide

How a session answers a question that `atlas`, `wayfinder`, `grilling`, or any other skill tells it to ask the user. Take each question through the four parts in order: precedent, tier, record, and (for a reserved decision) park. A fifth part, change, is for a decision that the user corrects after its ticket closed.

## Precedent

A question that is already answered stays answered. Before you choose a tier, look for the answer in the principles, the charter, the ADRs and the glossary of the repo, and the closed tickets of each map in this repo (label `wayfinder:map`). When one of them answers the question, that answer is the decision: record it with `Decided by: precedent` and the link.

## Tier

The user changes the plan on few questions, and the question alone does not tell which ones. Thus the user decides only what the plan builds, because a wrong answer there sends the work in a wrong direction. The wave review shows all other decisions before a ticket is published, and a correction there costs sessions and not the time of the user. [`SHADOW.md`](SHADOW.md) holds the measurement behind these tiers.

**Reserved**: the user decides. When you cannot tell whether a question is in one of these classes, it is.

1. **Scope**: the answer changes the Goal, the Non-goals, the Outcome or the Done signal of a node, or it adds or removes a capability, or it moves the size of the node by one ticket (700 to 1000 changed lines) or more.
2. **Listed**: the charter lists the decision as reserved.

**Session**: all other questions. Take your own recommendation, and record its **kind**: the kind sets the rank of the decision in the wave review. Take the first kind that applies:

1. `shape`: the answer changes a cross-node decision, the Needs or the Gives of a node, or a core domain term that other decisions will depend on.
2. `stakes`: the answer sets a recurring cost, a security or data exposure, a legal or compliance position, a price, or a promise to a customer.
3. `assumption`: the answer depends on a fact that is not in the repo, the tracker, the principles, or a public source. State the fact that you assumed in the `Why` line.
4. `routine`: names, the choice of a library or a tool, formats, flags, test approach, and ticket mechanics.
5. `plain`: all others.

## Record

The record is the ledger. Post it as the resolution comment of the ticket, with one block for each decision that the ticket made:

```markdown
### <the decision in a few words>

- **Answer**: <one or two lines>
- **Why**: <one line>
- **Rejected**: <option: reason; option: reason>
- **Decided by**: user | session | precedent (<link>)
- **Kind**: shape | stakes | assumption | routine | plain (only with `Decided by: session`)
- **Depends on**: <each ticket whose answer this one builds on, by name with its link> | none
```

When you close a ticket that has a decision with `Decided by` other than `user`, add the label `navigator:unseen`. The wave review removes it. A ticket with this label holds a decision that the user has not seen.

After the kickoff, only the re-plan writes the atlas file. A decision that changes it (the Goal, the Non-goals, a cross-node decision, or a node entry) waits for the re-plan on an open ticket with the label `navigator:shape`. Add the label together with the line `Remaining: none | <the work that the ticket needs after the re-plan>`: the re-plan applies the decision, and closes the ticket when the line says `none`. A session that decides adds the label after it did all other work of the ticket. A parked decision gets the label from the `ask` session, after the answer of the user.

## Park

Park a reserved decision.

1. Post the memo as a comment on the ticket.
2. Add the label `navigator:parked`. Keep the ticket open.
3. Decide each other question of the ticket that does not depend on the parked one. Then end the session.

The memo is for a reader who was not in the session and has not seen the decisions behind it. Use this order:

```markdown
## Memo: <the decision in a few words>

**Why it matters**: <what this decision changes for the Goal, in one or two lines>

**What you have not seen**: <each decision that this one depends on and that has the label `navigator:unseen`: its name with its link, and its gist in one line> | nothing

**Options**:
- **A: <name>**: <its effect on scope, cost, and later decisions>
- **B: <name>**: <its effect>

**Recommendation**: <the option, and the reason in one line>

**Remaining**: none | <the work that this ticket still needs after the answer>
```

## Change

When the user changes the decision of a closed ticket, all that was built on the old answer must run again.

1. Post the answer of the user on the ticket as a new ledger block, with `Decided by: user`, and change its line in the Decisions so far of the map. When the change needs new work on the ticket, open the ticket again with a comment that states the work. When the answer changes the atlas, open it again with the label `navigator:shape`, as the Record part says.
2. Find each ticket whose `Depends on` names the changed ticket, directly or through other tickets, in each map of the initiative. Open each one again, with a comment that states the change.
3. Where one ticket that is open again depends on another, add a blocking edge between them, so that they run in order.
4. Delete `spec.md` and `split.md` of the node of the changed ticket, and of each node that has a ticket opened again.

Done when each ticket that depends on the change is open and blocked in order, and no spec of an affected node remains.
