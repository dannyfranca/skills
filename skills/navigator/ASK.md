# Ask

The `ask` role brings the parked batch of one wave to the user and records the answers. The user comes to this session cold, possibly days after the kickoff, so each memo must stand alone.

1. **Collect the batch**: each open ticket of the wave with the label `navigator:parked`, with its memo. Put first the ticket that blocks the most other tickets. A ticket that the driver parked as blocked has no memo: write one that says what failed.
2. **Bring each memo up to date.** The "What you have not seen" part lists each decision that the memo depends on and that has the label `navigator:unseen` now. Done when each memo matches the labels of this moment.
3. **Add a video where words are too many.** When the "What you have not seen" part of a memo is longer than `memo_video_words`, run `explainer-video` in a subagent, with those unseen decisions as the subject. Save the video in `wave-<n>/` and link it at the top of the memo.
4. **Tell the user the size of the batch** in one line: how many decisions, and how many of them are prototypes to judge.
5. **Give one memo for each message**, and wait for the answer. Answer each question of the user from the tickets. For a UI prototype, give the link of each variant.
6. **Record each answer** on its ticket as [`DECIDE.md`](DECIDE.md) says, with `Decided by: user`. Remove the label. Close the ticket when the `Remaining` line of the memo is `none`. When the answer changes the atlas (the Goal, the Non-goals, a node, or a cross-node decision), replace the label with `navigator:shape` and keep the ticket open: the re-plan takes it.

Complete when no ticket of the wave has the label `navigator:parked`.
