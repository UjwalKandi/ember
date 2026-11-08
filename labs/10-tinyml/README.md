# Lab 10: Train a TinyML attack classifier and run it on the ESP32

**Status:** outline. Built alongside roadmap phase **L2** (the project's novelty). It needs R1's feature windows and L1's simulator.

**Goal:** the whole "train a model, then implement it locally" loop for a model small enough to run on a $10 microcontroller. You define the features and the labels, train a classifier from scratch in PyTorch, quantize it to int8, write (or generate) the C inference code, and prove the on-device model computes the same answers as the Python one. Then you measure whether it beats hand-tuned thresholds.

Labs 04–08 teach fine-tuning a model someone else pre-trained. This lab is the other half of the job: **supervised learning from scratch** on data you collected yourself, under a hard memory budget.

## Concepts (why)
- **Features before models.** The arm already reduces each channel dwell to a fixed vector: the `W` packet (`protocol/SPEC.md` §4, `gateway/airspace.py: W_FIELDS`). That's the input. Rates (counts ÷ dwell), log-scaling of counts and per-channel normalization matter more than the architecture.
- **Labels.** Classes: `benign / deauth-flood / beacon-spam / evil-twin-suspect / tracker`.
  - The L1 simulator gives perfectly labelled windows.
  - Your own recordings (`captures/*.log`, recorded with `--record`) give real benign traffic.
  - Your own authorized tests (rebooting your own router, a beacon test from your own ESP32) give real positives.
- **Sim-to-real gap.** A model trained only on simulated data learns the simulator. Mix in real captures, hold some real data out, and report both scores separately.
- **Class imbalance.** Attacks are rare. Use class weights or resampling, and judge by recall and false alarms per hour of normal traffic, not accuracy (99% "benign" is a useless 99%).
- **Tiny architectures.** An MLP with 22 inputs → 32 → 16 → 5 has about 1.3k weights: ~1.3 KB in int8. A 1D-CNN over the last N windows adds temporal context. Every layer has to fit the arm's RAM and its 300 ms per-window budget.
- **Post-training quantization (int8).** Map float weights and activations to 8-bit integers with a scale and zero-point per tensor, calibrated on representative windows. Same idea as GGUF Q8_0 in Lab 01, but done by hand so you see every number.
- **Bit-exact parity.** The C implementation must match a Python reference of the *quantized* model exactly on a test set. This is the same pattern as `firmware/test_host` (host-compiled C++ tests).
- **Fusion, not replacement.** The engine combines the classifier's score with the deterministic detectors. The model raises or lowers the likelihood but never alerts alone (OBJECTIVES guardrail 5).

## Planned steps
1. **Dataset builder** (`training/tinyml/dataset.py`): sim windows (L1) + `captures/*.log` → `X` (normalized W vectors, optionally stacked over time), `y`; split by *session*, not by window, to avoid leakage.
2. **Baseline first:** the R1 thresholds (`DEA_MIN`, `BSP_MIN_NEW` in `firmware/src/arm.cpp`) scored on the same test set. This is the number to beat.
3. **Train** (`training/tinyml/train.py`): a PyTorch MLP; class-weighted cross-entropy; early stopping on validation recall at a fixed false-alarm rate; loss curves with `training/plot_metrics.py`.
4. **Quantize** (`training/tinyml/export.py`): int8 weights + per-layer scales → `firmware/src/tinyml_model.h` (generated, `const` arrays in flash).
5. **Run on the arm** (`firmware/src/tinyml.{h,cpp}`): integer-only inference per window. Report flash, RAM and µs per inference over USB.
6. **Parity test:** the host-compiled C vs the Python int8 reference on 1,000 windows must give identical class scores (added to `tests/test_rf.py`).
7. **Evaluate:** TinyML vs thresholds on held-out sim scenarios, look-alikes (a roaming mesh AP, a neighbour's stationary tag) and real captures: recall, false alarms/hour, time to detect.

## Exercises
| Run | Model | Inputs | Params | Int8 size | Recall (sim) | False alarms/h (real benign) | µs/inference |
|---|---|---|---|---|---|---|---|
| thresholds | rules | 1 window | 0 | 0 | | | |
| mlp-small | 22→16→5 | 1 window | | | | | |
| mlp | 22→32→16→5 | 1 window | | | | | |
| mlp-ctx | (22×4)→32→5 | last 4 windows | | | | | |

1. How much recall does int8 lose vs float32? Where does it lose it?
2. Train on sim only, then on sim + real. How do real-capture false alarms change?
3. Which features matter? Drop one group (sequence/TSF anomalies, broadcast deauths) and retrain.

## Checkpoint
- [ ] The dataset is split by session, with a real-capture holdout
- [ ] Float model beats thresholds on held-out data (or you can explain why not)
- [ ] The int8 model runs on the arm; flash/RAM/µs are logged
- [ ] The C ↔ Python int8 parity test passes
- [ ] Numbers are in `docs/LEARNING_LOG.md`

## Interview questions
- Why split by session instead of shuffling windows? What is leakage?
- How do you pick the decision threshold for a rare-event detector?
- Explain int8 quantization: scale, zero-point, and why the accumulator is int32.
- Your model scores 99% accuracy. Why might it still be useless?
- How would you detect that a deployed model has drifted from its training data?
