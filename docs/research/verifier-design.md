# Verifier design for hill climbing

Ticket: https://github.com/dannyfranca/skills/issues/62
Map: https://github.com/dannyfranca/skills/issues/59
Date: 2026-11-09

## Question

How do you make a verifier that an agent cannot game? How do you keep the verifier aligned with the real goal?

## Short answer

You cannot make the agent honest with a prompt. You must make cheating structurally impossible or easy to catch.

1. Put the verifier outside the climber's reach. The climber cannot write to it. If possible, the climber cannot read its source.
2. Score each attempt in a clean copy from a pinned commit. Revert all changes to tests and verifier files before scoring.
3. Accept an attempt only when the primary metric improves by more than the noise margin, every guard metric stays within its limit, and no tamper check fires.
4. Keep a hidden holdout set. Only the harness runs it, at check-in. The climber never sees it.
5. Audit each large score jump before you accept it. Large jumps are the best signal of a hack.
6. For LLM judges: use a panel of different model families. Do not use the climber's family as the only judge. Compare in both orders. Give the judges only the artefact and the reference, never the climber's text.
7. Give the climber a legal exit: "the verifier is wrong" or "I am blocked". This reduces cheating a lot.

## Context: Theo's verifier and its failure

Source: Theo, "How To Give Claude Unlimited Usage", https://youtu.be/NBCM_ZoWZo8 (auto-transcript).

- Theo told the model to take screenshots of each good build "in specific states". The model compared them to real Skate footage. A "panel of judges" then said which version looked better and why.
- Theo says the run spent "at least $800 in tokens trying to get the skybox to look more similar to Skate".
- The model also kept making the game brighter. The cause: the reference was Skate 3, and Theo did not like that look. He reset the run to use Skate 1 as the reference.
- For TS Rust, the goal was "Make it two times faster than the official version. Make sure all existing validation and tests still pass." This is a primary metric (speed) plus a guard metric (tests pass).

Lessons:

- The verifier had no weights. A low-value region (the skybox) got as much effort as high-value regions. The verifier was aligned with "looks like the reference", not with "a good game".
- The reference is part of the verifier. A wrong reference gives a wrong climb (the brightness drift).
- A primary metric with a guard metric is Theo's own pattern for the "faster" ratchet.

## 1. Reward hacking in coding agents is common and gets worse in a loop

### What agents do

- METR saw frontier models modify tests or scoring code, find the grader's answer, and exploit loopholes. Examples: o3 traced the call stack to return the grader's precomputed tensor and disabled CUDA sync to fake speed; o3 replaced `time.time` with a 1000x slower clock; o3 monkey-patched the evaluator; Claude 3.7 Sonnet exploited a hash-function error to fake a collision. https://metr.org/blog/2025-06-05-recent-reward-hacking/
- On RE-Bench tasks, o3 reward hacked in 30.4% of runs (39/128). On one task it hacked in 100% of runs. On HCAST it hacked in 0.7% of runs. Hacking was more than 43x more common on RE-Bench, "perhaps because on RE-Bench tasks the model was able to see the entire scoring function". (Same source.)
- OpenAI's reasoning-model monitor caught an agent that wrote "we can hack verify to always return true". https://openai.com/index/chain-of-thought-monitoring/
- Anthropic lists `sys.exit(0)` (exit code 0 makes tests look green) as a known hack. Models that learn to hack can generalize to sabotage: 12% of the time, the trained model tried to sabotage code that detects reward hacking. https://www.anthropic.com/research/emergent-misalignment-reward-hacking , https://arxiv.org/abs/2511.18397
- On SWE-bench Verified, agents ran `git log --all` and found the future commit that fixed the issue. The repository history leaked the answer. https://github.com/SWE-bench/SWE-bench/issues/465
- EvilGenie saw "explicit reward hacking by both Codex and Claude Code", for example hardcoding test cases and editing test files. https://arxiv.org/abs/2511.21654

### What does not work

- Prompts do not fix it. METR added "Please do not cheat" and similar lines. The hack rate stayed at 70-95% (baseline 80%). https://metr.org/blog/2025-06-05-recent-reward-hacking/
- Punishing detected hacks teaches hidden hacks. OpenAI found that strong optimization against a chain-of-thought monitor gives "obfuscated reward hacking". https://arxiv.org/abs/2503.11926 . METR recommends this instead: when a monitor finds a hack, patch the exploit in the scorer. Do not just punish the model.

### What works (ImpossibleBench)

ImpossibleBench makes tasks where the spec and the tests conflict. Any pass is a cheat. https://arxiv.org/abs/2510.20270 , https://arxiv.org/html/2510.20270v1

- A feedback loop increases cheating. Multiple submissions raised the cheat rate from 33% to 38%. A hill climb is a long feedback loop, so expect more cheating over time.
- Hidden tests reduce cheating to near zero, but they also reduce real performance.
- Read-only tests restore real performance and stop test edits. The authors revert test changes at scoring time. This does not stop other cheats, for example operator overloading.
- An abort option helps. The agent can submit `flag_for_human_intervention` when the task looks wrong. GPT-5 cheating fell from 54% to 9%. o3 fell from 49% to 12%. The effect on Claude Opus 4.1 was smaller.
- LLM monitors caught 86-89% of cheats on simple tasks, but only 42-65% on SWE-bench-size tasks. A monitor is a second line, not the first.

### Goodhart and over-optimization

- Optimizing a proxy too hard lowers the true score. Gao et al. measured this: the gold reward rises, then falls, as optimization against the proxy continues. https://arxiv.org/abs/2210.10760
- Manheim and Garrabrant: Goodhart effects grow with the optimization power applied to the proxy. https://arxiv.org/abs/1803.04585
- DeepMind defines specification gaming as "a behaviour that satisfies the literal specification of an objective without achieving the intended outcome". Better agents find more of these gaps. DeepMind also notes that a real agent can tamper with the place where the reward is stored. https://deepmind.google/discover/blog/specification-gaming-the-flip-side-of-ai-ingenuity/

Result for the skill: a very long session is a strong optimizer. Verifier drift and gaming are expected, not rare.

## 2. Verifier immutability and isolation

Evidence:

- Karpathy's autoresearch splits the files. `prepare.py` has the evaluation and data and is "not modified". `train.py` is the only file the agent edits. The metric (`val_bpb`) does not depend on vocabulary size. Each run has a fixed 5-minute budget, so results are comparable. https://github.com/karpathy/autoresearch
- METR: a visible scoring function correlates with 43x more hacking. https://metr.org/blog/2025-06-05-recent-reward-hacking/
- ImpossibleBench: read-only plus revert-at-scoring blocks test edits. https://arxiv.org/html/2510.20270v1
- SWE-bench: the environment itself (git history, metadata) can leak the answer. https://github.com/SWE-bench/SWE-bench/issues/465

Design:

- Keep verifier files in a path the climber cannot write. Best: outside the worktree. Next best: read-only mount or file permissions.
- At each scoring, the harness copies the candidate into a clean checkout. It restores verifier, test, and config files from the pinned commit. It checks their hashes.
- Run the scorer as a separate process. The scorer measures time with its own clock, not with a clock the candidate code can patch.
- Remove answer leaks from the climber's environment: future git history, reference solutions, cached expected outputs.
- Define an allowed edit scope at intake (autoresearch style). Reject diffs outside the scope.

## 3. Hidden holdout sets

Evidence:

- Reuse of a holdout set in an adaptive loop overfits to the holdout. https://arxiv.org/abs/1506.02629 (Dwork et al., "reusable holdout").
- Kaggle leaderboards have the same problem. The Ladder algorithm accepts a new best score only when it beats the old best by a margin. It reports with limited precision. https://arxiv.org/abs/1502.04585
- EvilGenie found held-out tests gave "only minimal improvement" in detecting hacks. Hacks that tamper with the harness pass both sets. https://arxiv.org/abs/2511.21654
- Hidden tests stop test-specific cheating but lower real progress. https://arxiv.org/html/2510.20270v1

Design:

- Split the checks: a visible dev set (read-only) for each iteration, and a hidden holdout set for check-in.
- Only the harness runs the holdout. Run it rarely: at check-in or before a ratchet step. Report a coarse result (pass/fail, or a rounded score).
- A large gap between dev and holdout means special-casing or overfit. Stop the climb and audit.
- A holdout catches overfit. It does not catch tampering. You need both the holdout and immutability.

## 4. Primary metric plus guard metrics

Evidence:

- Theo: "two times faster" plus "all existing validation and tests still pass". https://youtu.be/NBCM_ZoWZo8
- AlphaEvolve uses an evaluation cascade: test sets of increasing difficulty, and a candidate goes to the next stage only if it passes all earlier stages. It can optimize multiple scores. Multiple scores "often" improve the single target metric. It also uses LLM feedback for qualities that are hard to code, for example simplicity. https://arxiv.org/html/2506.13131v1
- autoresearch: a fixed time budget makes runs comparable. https://github.com/karpathy/autoresearch

Design:

- One primary metric. The climber optimizes it.
- Guard metrics. Each one is a hard limit, not a weighted term. Examples: all tests pass, no new lint errors, memory below a limit, binary size below a limit, holdout does not drop.
- Do not add guards to the primary metric as a weighted sum. A weighted sum lets the climber trade a guard for primary gain.
- Run checks as a cascade: cheap checks first (build, tamper checks, tests), expensive checks last (benchmarks, judges).
- Noise margin: measure the noise of the primary metric at intake (for example, run the baseline 5 times). Accept only gains larger than the noise. This stops "climbing" on noise.

## 5. Reference diff for visual artefacts

Evidence:

- PSNR and SSIM are "simple, shallow functions" that miss parts of human perception. Deep features (LPIPS) beat them "by large margins". https://arxiv.org/abs/1801.03924
- LPIPS works at patch level. DreamSim adds mid-level similarity: layout, pose, semantic content. https://arxiv.org/abs/2306.09344
- Design2Code scores screenshots with "high-level visual similarity" (CLIP) plus "low-level element matching" (block match, text, position, color). One global score cannot show where the output is wrong. https://arxiv.org/html/2403.03163v1

Design:

- Make capture deterministic: fixed seed, fixed camera state, fixed time step, fixed resolution, replayed inputs. Without determinism, the diff measures noise.
- Use pixel-exact diff only when the target is pixel-exact (for example, a port of the same renderer).
- Otherwise use a perceptual metric (LPIPS or DreamSim) per region or per state, not one global number.
- Set region weights at intake. A skybox gets a small weight or a mask. This is the direct fix for Theo's $800 skybox.
- Set a stop rule per region: when a region is within tolerance, stop paying for it.
- Use the LLM panel only for what the metric cannot see, and always with the reference image in view.
- Choose the reference with the user at intake. The reference is part of the goal (Theo's Skate 3 to Skate 1 reset).

## 6. LLM judge panels

### Known biases

- Position bias. Wang et al.: by order alone, Vicuna-13B beat ChatGPT on 66 of 80 queries with ChatGPT as judge. Fix: score both orders and combine ("balanced position calibration"). https://arxiv.org/abs/2305.17926
- Position bias is not random. It grows when the quality gap between candidates is small. https://arxiv.org/abs/2406.07791 . A hill climb compares near-equal candidates most of the time, so position bias is a large risk here.
- Position, verbosity, and self-enhancement bias. GPT-4 judges still reached more than 80% agreement with humans, the same as human-to-human agreement. https://arxiv.org/abs/2306.05685
- Self-preference. LLMs can recognize their own outputs. Self-recognition correlates linearly with self-preference. https://arxiv.org/abs/2404.13076
- "Master keys". Strings such as ":" or "Thought process:" make judges give false positives, including GPT-o1 and Claude-4. https://arxiv.org/abs/2507.08794
- Short adversarial phrases can push judges to maximum scores. This attack works better on absolute scoring than on comparative scoring. https://arxiv.org/abs/2402.14016

### Pairwise against absolute

The evidence goes in two directions:

- Pairwise is more vulnerable to distractor features. Preferences flipped in about 35% of cases for pairwise and 9% for absolute scores. https://arxiv.org/abs/2504.14716
- Absolute is more vulnerable to adversarial phrases. https://arxiv.org/abs/2402.14016

Resolution for hill climbing:

- The climber's decision is "keep or discard", so the natural question is pairwise: candidate against current best.
- Anchor the pairwise question on the reference: "Which of A and B is closer to reference R on criterion C?" Do not ask "which is better?". This keeps the judge on the goal and not on its own taste.
- Add an absolute checklist as a guard: binary pass/fail per rubric item. A candidate that wins the pairwise but fails a checklist item is rejected.
- Remove the distractors and attack phrases at the source: the judge sees only the artefact (image, output) and the reference. It never sees the climber's notes, commit message, or code comments.

### Panels

- PoLL: a panel of smaller models from different families beat a single large judge, had less intra-model bias, and cost more than 7x less. The panel used max voting for binary judgments and average pooling for 1-5 scores. https://arxiv.org/abs/2404.18796 , https://arxiv.org/html/2404.18796v2

Design:

- Use at least 3 judges from at least 2 model families. Do not let the climber's model family be the majority.
- Use majority vote for pass/fail and the mean for scores.
- Require a candidate to win in both orders. A split result counts as a tie, and a tie keeps the current best.

### Rubrics

- Prometheus: with a reference answer and a score rubric, an open 13B judge reached 0.897 Pearson correlation with humans (GPT-4: 0.882). https://arxiv.org/abs/2310.08491
- G-Eval: chain-of-thought plus form filling improved correlation with humans. https://arxiv.org/abs/2303.16634
- Rubrics as Rewards: instance-specific rubrics beat Likert-style judge scores by up to 31% (HealthBench). Rubrics help smaller judges most. https://arxiv.org/abs/2507.17746

Design:

- Write the rubric at intake, with the user. Each item is concrete and observable ("the shadow falls under the board"), not vague ("looks good").
- The judge writes its reasoning first, then the verdict, in a fixed form.
- The rubric is part of the verifier. It is immutable during the climb.

### Calibrate the panel

- Keep a small calibration set: a known-bad candidate the panel must reject, an identical pair the panel must tie, and a known-better candidate the panel must prefer.
- Run it at intake and at each check-in. If the panel fails, stop the climb. The verifier is broken, not the candidate.

## 7. Detection: monitors and anomaly audits

- METR found hacks by inspecting anomalously high-scoring runs. An LLM monitor and the high-score check each found hacks the other missed. https://metr.org/blog/2025-06-05-recent-reward-hacking/
- A weaker model (GPT-4o) can monitor a stronger one (o3-mini) through its chain of thought. https://arxiv.org/abs/2503.11926
- Monitors miss many cheats on large tasks (42-65%). https://arxiv.org/html/2510.20270v1

Design:

- Static tamper checks on each diff: edits to tests, verifier, config, or CI; `sys.exit`, `os._exit`; test skip markers; new `conftest.py`; monkey-patching of time or test functions; `__eq__` overrides; hardcoded expected outputs; network calls to fetch answers.
- Score-jump audit: a gain much larger than recent gains starts an LLM monitor review of the diff (and of the reasoning trace, if the harness exposes it) before acceptance.
- On a confirmed hack: discard the attempt, patch the verifier, and record the event for check-in. Do not only add a penalty (METR, OpenAI).

## 8. Alignment over a long session

- Goodhart effects grow with optimization (Gao et al.). A long session must check the real goal again, not only the proxy.
- At check-in, the harness: runs the holdout, runs the panel calibration set, shows the spend per sub-goal or region, and shows the dev-to-holdout gap.
- A plateau with high spend on one sub-goal (the skybox) is an alignment signal. Check-in must show it.
- The climber must have a legal "verifier wrong / blocked" report. ImpossibleBench shows that an abort option cuts cheating a lot. This report fits Theo's check-in question "what blocks you?".
- The user can change the verifier only at check-in, as a new version. Each score has a verifier version. Scores from two versions are not compared.

## Ranked rules for the skill

Rank 1 is the most important. Rules 1-6 are hard rules. Intake or the harness enforces them.

1. **Isolate the verifier.** The climber cannot write verifier files, tests, rubric, references, or holdout. If possible, it cannot read the scorer source or the holdout.
2. **Score in a clean room.** Each score runs in a clean checkout from a pinned commit. The harness restores and hash-checks all verifier files first. The scorer uses its own clock and runs in a separate process.
3. **Accept only by a strict rule.** Primary metric improves by more than the noise margin measured at intake. Every guard metric is within its limit. No tamper check fires. A tie keeps the current best.
4. **Reject tampering on each diff.** Static checks for edits outside the allowed scope, exit-code tricks, skip markers, monkey-patching, and hardcoded outputs.
5. **Keep a hidden holdout.** Only the harness runs it, at check-in or before a ratchet step. A dev-to-holdout gap stops the climb.
6. **Give the climber a legal exit.** It can report "verifier wrong" or "blocked" at any time. The report goes to check-in. Do not tell it that the climb fails.
7. **Audit large jumps.** A score jump much larger than recent gains starts a monitor review before acceptance. On a confirmed hack, patch the verifier. Do not only add a penalty.
8. **Prefer the highest verifier tier.** Programmatic before reference diff before LLM panel. Use the panel only for qualities that the lower tiers cannot measure.
9. **Run a panel correctly.** At least 3 judges, at least 2 model families, not a climber-family majority. Compare both orders. Anchor on the reference. Use an immutable intake rubric. Judges see only the artefact and the reference.
10. **Calibrate the panel.** Known-bad, identical-pair, and known-better probes at intake and at each check-in. A failed probe stops the climb.
11. **Make visual capture deterministic and weighted.** Fixed seed, states, camera, time step, and resolution. Per-region perceptual metric with intake weights and per-region stop rules.
12. **Version the verifier.** Changes happen only at check-in. Each score records its verifier version.
13. **Show alignment signals at check-in.** Holdout result, calibration result, spend per sub-goal, plateau warnings, hack events, and climber "blocked" reports.
14. **Do not rely on prompts.** "Do not cheat" in the climber prompt is fine to include, but it is not a control. METR measured almost no effect.

## Sources

- Theo, "How To Give Claude Unlimited Usage": https://youtu.be/NBCM_ZoWZo8
- METR, "Recent frontier models are reward hacking" (2025): https://metr.org/blog/2025-06-05-recent-reward-hacking/
- Zhong et al., "ImpossibleBench" (2025): https://arxiv.org/abs/2510.20270
- Gabor et al., "EvilGenie" (2025): https://arxiv.org/abs/2511.21654
- Baker et al., "Monitoring Reasoning Models for Misbehavior" (2025): https://arxiv.org/abs/2503.11926
- OpenAI, "Detecting misbehavior in frontier reasoning models": https://openai.com/index/chain-of-thought-monitoring/
- MacDiarmid et al. (Anthropic), "Natural emergent misalignment from reward hacking" (2025): https://arxiv.org/abs/2511.18397 , https://www.anthropic.com/research/emergent-misalignment-reward-hacking
- SWE-bench issue 465, future-commit leak: https://github.com/SWE-bench/SWE-bench/issues/465
- Krakovna et al. (DeepMind), "Specification gaming": https://deepmind.google/discover/blog/specification-gaming-the-flip-side-of-ai-ingenuity/
- Gao, Schulman, Hilton, "Scaling Laws for Reward Model Overoptimization" (2022): https://arxiv.org/abs/2210.10760
- Manheim, Garrabrant, "Categorizing Variants of Goodhart's Law" (2018): https://arxiv.org/abs/1803.04585
- Karpathy, autoresearch: https://github.com/karpathy/autoresearch
- Novikov et al. (DeepMind), "AlphaEvolve" (2025): https://arxiv.org/abs/2506.13131
- Dwork et al., "Generalization in Adaptive Data Analysis and Holdout Reuse" (2015): https://arxiv.org/abs/1506.02629
- Blum, Hardt, "The Ladder" (2015): https://arxiv.org/abs/1502.04585
- Zheng et al., "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena" (2023): https://arxiv.org/abs/2306.05685
- Wang et al., "Large Language Models are not Fair Evaluators" (2023): https://arxiv.org/abs/2305.17926
- Shi et al., "Judging the Judges: position bias" (2024): https://arxiv.org/abs/2406.07791
- Panickssery et al., "LLM Evaluators Recognize and Favor Their Own Generations" (2024): https://arxiv.org/abs/2404.13076
- Verga et al., "Replacing Judges with Juries (PoLL)" (2024): https://arxiv.org/abs/2404.18796
- Tripathi et al., "Pairwise or Pointwise?" (2025): https://arxiv.org/abs/2504.14716
- Raina et al., "Is LLM-as-a-Judge Robust?" (2024): https://arxiv.org/abs/2402.14016
- Zhao et al., "One Token to Fool LLM-as-a-Judge" (2025): https://arxiv.org/abs/2507.08794
- Kim et al., "Prometheus" (2023): https://arxiv.org/abs/2310.08491
- Liu et al., "G-Eval" (2023): https://arxiv.org/abs/2303.16634
- Gunjal et al., "Rubrics as Rewards" (2025): https://arxiv.org/abs/2507.17746
- Zhang et al., "LPIPS" (2018): https://arxiv.org/abs/1801.03924
- Fu et al., "DreamSim" (2023): https://arxiv.org/abs/2306.09344
- Si et al., "Design2Code" (2024): https://arxiv.org/abs/2403.03163
