# Decide

How a session answers a question that `atlas`, `wayfinder`, `grilling`, or any other skill tells it to ask the user. Take each question through the four parts in order: precedent, tier, record, and (for a reserved or split decision) park.

## Precedent

A question that is already answered stays answered. Before you choose a tier, look for the answer in the principles, the charter, the ADRs and the glossary of the repo, and the closed tickets of each map in this repo (label `wayfinder:map`). When one of them answers the question, that answer is the decision: record it with `Decided by: precedent` and the link.

## Tier

Test the question against the reserved classes first, then against the routine list. A question that is in neither goes to the council. When two tiers are possible, take the higher one: reserved is above council, and council is above routine.

**Reserved**: the user decides.

1. **Scope**: the answer changes the Goal, the Non-goals, the Outcome or the Done signal of a node, or it adds or removes a capability, or it moves the size of the node by one ticket (700 to 1000 changed lines) or more.
2. **Stakes**: the answer sets a recurring cost, a security or data exposure, a legal or compliance position, a price, or a promise to a customer.
3. **User-only fact**: the answer depends on a fact that is not in the repo, the tracker, the principles, or a public source.
4. **Shape**: the answer changes a cross-node decision, the Needs or the Gives of a node, or a core domain term that other decisions will depend on.
5. **Customer-facing verdict**: the question is which UI or product variant the customer gets.
6. **Listed**: the charter lists the decision as reserved.

**Routine**: the session decides alone and takes its own recommendation. Names, the choice of a library or a tool, formats, flags, test approach, and ticket mechanics.

**Council**: run [`COUNCIL.md`](COUNCIL.md).

## Record

The record is the ledger. Post it as the resolution comment of the ticket, with one block for each decision that the ticket made:

```markdown
### <the decision in a few words>

- **Answer**: <one or two lines>
- **Why**: <one line>
- **Rejected**: <option: reason; option: reason>
- **Decided by**: user | council | session | precedent (<link>)
- **Doubt**: none | low confidence | debate: <the view that lost, in one line>
- **Depends on**: <each ticket whose answer this one builds on, by name with its link> | none
```

When you close a ticket that has a decision with `Decided by` other than `user`, add the label `navigator:unseen`. The wave review removes it. A ticket with this label holds a decision that the user has not seen.

## Park

Park a reserved decision, and a decision that the council could not settle.

1. Post the memo as a comment on the ticket.
2. Add the label `navigator:shape` for a reserved decision of class 4, and `navigator:parked` for all others. Keep the ticket open.
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

A memo for a council split gives one option for each position that the council held.
