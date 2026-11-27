# Reports

The three views that navigator builds for the user. The user scans and does not read: each view must give its message through shape, colour, and position before text.

## Rules for each view

- **One HTML file** that opens with no network and with no file beside it: inline CSS, inline SVG for each diagram and chart, and the video inside. A prototype is a link to a local file or a URL.
- **A card** is one gist line and at most three bullets. All other detail is one link away, on the ticket.
- **Refer by name.** A ticket, a node, or a decision shows its name, and the name holds the link.
- **Lead each section with a visual** when its data has a shape: a graph for dependencies, a chart for counts and sizes, a badge for a state.
- **One look.** Copy `assets/report.html` to the path of the view. Replace the `<title>` and all between the comments `content` and `/content`, and change nothing else. The shell gives the two themes, the section menu, the filter, and links that open in a new tab. Build each part from the classes that the template shows.
- **Each section** is a `section.sec` with a short name in `data-nav`: the menu reads it. The sections and their order are fixed, so that the user knows where to look.
- **Each colour is a token**: a `var(--name)` from the top of the template, also in an SVG attribute and in an inline style. Text on a coloured fill uses `--on`. A hex value stays the same in the dark theme and becomes unreadable.

A view is done when each item of its source shows one time, each link opens, the file opens with the network off, and the content has no hex colour.

## Checkpoint view

File: `checkpoint.html` in the run directory. Source: the nodes that the chart session settled.

1. **The graph** of the nodes, the edges, and the fog.
2. **One card for each node**: the outcome, the done signal, why it is its own node, which merge was rejected, and the rough size in tickets of 700 to 1000 changed lines. Give the badge "possible over-split" to a node of two tickets or less, and the badge "new UI" where it applies.

## Wave report

File: `wave-<n>/report.html`. Source: what the review session collected and ranked.

1. **The video**, embedded at the top.
2. **The wave at a glance**: the atlas graph with the nodes of the wave marked, and the count of decisions for each `Decided by` value and for each `Kind` value.
3. **The shape**: the atlas graph of now against the graph at the start of the wave: one colour for what was there before, and one for what is there now. Under the graph, one card for each `shape` decision of a session, in rank order: the node or the edge that it changes, before, after, the rejected options, and how many decisions depend on it. A card for a domain term has no mark in the graph. The user must be able to agree or disagree from the graph and the gist line.
4. **Decisions that need your eyes**: one card for each top card that is not in section 3, in rank order. The card names who decided, the kind, the rejected options, and how many decisions depend on it.
5. **All other decisions**: one scan list for each node, one line for each decision that is not in sections 3 and 4, with badges for `Decided by` and `Kind`. The rejected options are collapsed.
6. **The ticket split**: one dependency diagram for each node, with the size of each ticket.
7. **Prototypes**: a gallery with one link for each prototype and each variant, and the verdict.

## Delta report

File: `wave-<n>/delta-<k>.html`. Source: what changed after the newest report.

1. **What you changed**: one card for each correction of the user.
2. **What followed**: one card for each decision that ran again, with the answer before and the answer after.
3. **The ticket split**: only the nodes whose split changed, with the tickets that are new, changed, or gone.
