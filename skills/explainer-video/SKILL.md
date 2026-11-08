---
name: explainer-video
description: "Make a narrated 3Blue1Brown-style explainer video about a subject you describe."
disable-model-invocation: true
---

# Explainer video

Make a 3Blue1Brown-style video that teaches the subject the user describes. Use Manim for the visuals and Kokoro for the voice. The target is teaching: the viewer must understand the subject at the end. The subject sets the length.

Call: `/explainer-video <subject> [voice=af_heart] [captions=off] [chapters-file]`.

- Get the audience from the user text. If the user names no audience, teach a smart newcomer.
- `voice=` sets `EXPLAINER_VOICE`. `captions=off` sets `EXPLAINER_CAPTIONS=off`. `chapters-file` sets `EXPLAINER_CHAPTERS_FILE=on`: a YouTube chapter list goes next to the video. Export them for every `render.sh` command.
- The work dir `VIDEO_DIR` is `$TMPDIR/<slug>-video/`. Keep scenes in `VIDEO_DIR/scenes/`.
- `SKILL_DIR` is the directory of this file.

## Steps

1. Run `SKILL_DIR/scripts/setup.sh`. Done when it prints `ready`. If it prints `missing:`, tell the user what to install and stop.
2. Research the subject from primary sources until you can answer every question a curious viewer asks.
3. Write `VIDEO_DIR/plan.md`:
   - The audience.
   - The hook: a concrete question the viewer wants answered. Not a definition.
   - The aha moment.
   - Chapters, from concrete to abstract. Each chapter has one concept and one visual metaphor.
   - 3 to 5 questions the viewer can answer after the video.

   Done when each question maps to a chapter that answers it.
4. Read [STYLE.md](STYLE.md). Write one file per chapter: `VIDEO_DIR/scenes/NN_slug.py`. Done when the narration answers every plan question.
5. Run `SKILL_DIR/scripts/render.sh preview VIDEO_DIR`. Look at every contact sheet in `VIDEO_DIR/build/sheets/`. Fix overlaps, cut-off content, crowded frames, and visuals that do not match the voice. Re-render only the changed files: `render.sh preview VIDEO_DIR scenes/NN_slug.py`. Done when every sheet is clean.
6. Run `SKILL_DIR/scripts/render.sh final VIDEO_DIR <slug>` in the background. Done when it prints `video:`. The video goes to `~/Videos/explainer-video/<YYYYMMDD-HHMM>-<slug>.mp4`.
7. Give the user the embedded video, the chapter list, and the voice.
