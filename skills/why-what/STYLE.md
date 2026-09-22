# Why / What style

Write a TL;DR for a reader who needs the summary before reading the diff.

## Shape

- Use `Why` and `What` headings.
- Fit heading depth to the surrounding document. Use `##` when no depth exists.
- Use bullets in every section.
- Treat `Notes` as exceptional. Add it only when essential context cannot fit `Why` or `What`.

## Content

- `Why`: State the problem or need.
- `What`: State the main change and its effect.
- `Notes` holds a reviewer-critical dependency, prior change, blocker, or exception.
- Keep ticket identity in the title or existing links.

## Tightness

- Use one short bullet per section by default. Use a second only for a separate, essential point.
- Group related edits by their shared purpose. Do not list individual edits, files, functions, or test cases.
- Include technical detail only when its omission would make the summary misleading.
- Put detailed implementation and verification information elsewhere.
