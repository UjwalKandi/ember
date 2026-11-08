# Lab 07: Preference tuning with DPO (coming next)

**Status:** outline. Starts from your Lab 04 SFT model.

**Goal:** improve the model using its *own mistakes*. Show it pairs of a good and a bad response to the same situation, and train it to prefer the good one.

## Concepts
- **Preference pairs:** for one swarm situation, `chosen` = the simulator-teacher's correct trajectory (from `training/gen_data.py`) and `rejected` = the trajectory your SFT model actually produced when it failed (from `results/*.json` / the eval).
- **DPO (Direct Preference Optimization):** a simple loss that raises the likelihood of `chosen` relative to `rejected`, while a frozen reference model (your SFT model) keeps it from drifting too far. The strength is set by `beta`.
- **Why after SFT:** SFT teaches the format and the typical behaviour; DPO sharpens decisions on the hard cases.

## Planned steps
1. A script that runs the SFT model on training episodes, keeps the failures, and pairs each with the teacher trajectory.
2. Train with TRL's `DPOTrainer` + LoRA on the Mac (small models) or Kaggle.
3. Export with `training/export_gguf.sh` and evaluate exactly as in Lab 05.
4. Compare SFT vs SFT+DPO on false alarms and the hard scenarios.

## Interview questions
- How does DPO differ from RLHF with a reward model and PPO?
- What does `beta` control? What happens if it's too small?
- Why might DPO on self-generated failures work better than on random pairs?
