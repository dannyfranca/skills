# Scene style

Write each chapter the way 3Blue1Brown teaches: the viewer discovers the idea, the video does not announce it.

## Teaching

- Open with the hook question from `plan.md`. Answer it at the aha moment.
- Show a concrete case first. Give the general rule after the viewer has seen it work.
- Teach one concept per chapter. Build each new concept on the previous chapter.
- End the video with a recap chapter that repeats the plan questions with their short answers.

## Narration

- Write as you speak: short sentences, "you" and "we", one idea per line.
- Merge lines of fewer than about 15 words. Kokoro sounds flat on very short lines.
- Show the visual first, then let the voice describe it. The viewer must not search for the thing the voice names.
- `say(text)` speaks and captions one line. The caption is TeX. When TeX reads badly, give the words in `spoken=`.
- `voice(text)` speaks with no caption. Use it for the title and the recap, where the screen already shows the words.

## Visuals

- Each colour keeps one meaning for the whole video. Define the colours once at the top of a scene file, or in a shared module in `scenes/`.
- Keep at most 6 active elements on screen. Dim the context (`mob.animate.fade(0.7)`) when focus moves.
- Each motion has a meaning: an object moves because it changes state, place, or owner.
- Keep the bottom caption band free. Keep all content inside the frame.
- Start a chapter with `chapter("N. Title")`. End it with `clear_all()`.

## Code

Each scene file has one `Narrated` subclass, `from common import *`, and `TITLE = "..."` at module level. `TITLE` becomes the chapter name in the final video.

Helpers in [scripts/common.py](scripts/common.py):

- Text: `mono`, `sans`, `tag`.
- Shapes: `Node` (box with title, subtitle and a state tag; `node.to(state, color)` animates the tag), `card`, `folder`, `character`, `lock`, `check`, `cross`, `halo`.
- Scene: `say`, `voice`, `unsay`, `chapter`, `clear_all`.

Gotcha: to resize about an edge, use `mob.animate.scale_to_fit_width(w, about_edge=LEFT)`. `set_width` ignores `about_edge`.

Examples: [examples/01_title.py](examples/01_title.py) (title with `voice`), [examples/02_buckets.py](examples/02_buckets.py) (chapter with `say`, `Node` and `tag`).
