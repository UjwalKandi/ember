# Lab 08: RL with verifiable rewards (GRPO) (coming next)

**Status:** outline. Likely needs a free Kaggle GPU for speed (generation-heavy).

**Goal:** train with rewards instead of examples. EMBER's simulator knows the correct answer for every situation, so it can score the model's decisions automatically: a *verifiable reward*.

## Concepts
- **Task:** a single-step decision. Given the swarm status and the evidence (tool results), output `submit_assessment`. One step keeps RL cheap and the reward clean.
- **Reward:** from simulator ground truth, e.g. +1 correct verdict, +0.5 correct cause, a penalty for invalid JSON or format, a bigger penalty for a missed fire.
- **GRPO (Group Relative Policy Optimization):** sample several answers per prompt, score them, and push the model towards the above-average ones. No separate value model is needed. Implemented in TRL's `GRPOTrainer`.
- **KL penalty:** keeps the policy close to the starting (SFT) model so it doesn't collapse.
- **Reward hacking:** the model finds ways to score without being right (e.g. always choosing the most common verdict). Watch per-scenario scores and the false-alarm rate, not just mean reward.

## Planned steps
1. Build the single-step dataset from `gateway/sim.py` episodes (prompt = context + evidence).
2. Write the reward function using `training/eval.py` scoring logic.
3. Run GRPO from the Lab 04 adapter: a small model (0.6–1.7B) on Kaggle T4, or the Mac if fast enough.
4. Evaluate the full multi-turn agent as in Lab 05: does single-step RL transfer?

## Interview questions
- What makes a reward "verifiable", and why is that attractive?
- GRPO vs PPO: what does GRPO drop and why?
- Give an example of reward hacking in this task and how you'd detect it.
