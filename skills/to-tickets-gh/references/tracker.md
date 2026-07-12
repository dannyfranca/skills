# GitHub Issues Tracker

- Repository: the current GitHub repository unless the user supplies another.
- Container: one parent issue labeled `epic`; create it or attach to one supplied by the user.
- Tickets: leaf sub-issues using `/to-tickets`' issue template.
- Edges: native sub-issue and dependency relationships. Sub-issue order is advisory; dependencies control execution.
- Backlog: open, unassigned leaf without a workflow label; every blocked leaf belongs here.
- Ready: open, unassigned leaf labeled `ready-for-agent` whose blockers are all closed.
- Scope: GitHub Issues only; no GitHub Project.
- Mutations: authenticated `gh` commands or GitHub's API through `gh`.
