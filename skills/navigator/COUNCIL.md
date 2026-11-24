# Council

The council decides a question that is not reserved and not routine. Each **member** is a model from the `council` list of the config. A member answers **blind**: it sees the question and the facts, and it sees no recommendation and no answer of another member.

1. **Write the prompt** from the template below. One prompt can hold several questions when no question depends on another. A question that depends on an open one waits for it. Done when a reader with only the prompt and the repo can answer each question.
2. **Ask each member in parallel.** Each member is a subagent with a fresh context window, on its own provider and model, with read access to the repo. [`HARNESS.md`](HARNESS.md) says how to reach a model of another provider. Done when each member answered or failed. The council is valid when two or more members from two or more providers answered. When it is not valid, park the question with the reason "council not available".
3. **Compare the answers.** `USER` counts as an option. When all members gave the same option, the round is settled.
4. **Run one debate round** when the first round is not settled. Send each member all the answers and reasons of the first round, without the model names, and ask for its final answer in the same reply format. This is the only debate round.
5. **Close.**

   | Result | Action |
   | --- | --- |
   | Settled on an option | The decision is made. `Decided by: council`. |
   | Settled on `USER` | Park the question. |
   | Not settled after the debate | Park the question. The memo gives each position as an option. |

   Set `Doubt` to `debate` and the view that lost when the debate round settled the question. Set it to `low confidence` when the first round settled it and a member gave `Confidence: low`. Set it to `none` in all other cases.

## Prompt template

Fill each part. Paste the reserved classes from [`DECIDE.md`](DECIDE.md) where the template names them, because a member cannot read this skill.

```markdown
You are one member of a council that decides a planning question. You answer alone, and no other member sees your answer. You can read the repo. Change no file.

## Context

<the Goal of the initiative; the Outcome of the node; the gist of each decision that this question builds on; the facts from the repo or from research, with the paths of the files that hold them>

## Principles

<each line of the principles files and of the charter that applies to the question>

## Question <n>

<the question>

Options, in no order of preference:

- **A**: <option and its effects>
- **B**: <option and its effects>

## When the user must decide

Answer `USER` when the question is in one of these classes:

<the six reserved classes>

## Reply format

For each question:

Question <n>
Answer: <the letter of an option | a new option, stated in one line | USER and the number of the class>
Reason: <three lines at most>
Confidence: high | low
```

List the options in a random order, and give your own recommendation no mark.
