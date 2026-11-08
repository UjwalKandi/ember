# Why EMBER pivoted (2026-11-08)

An honest review of EMBER v1 (wildfire camera/mic mesh, fine-tuned LLM as the decision maker) before investing more time. The v1 code is preserved at git tag `wildfire-v1`.

## Verdict on v1
**With the LLM making the fire / no-fire call, v1 was partly a gimmick.** The engineering underneath was real: mesh, protocol, firmware, simulator, eval harness, flicker-gated flame detection. But the LLM sat exactly where deterministic, probabilistic methods are more accurate, cheaper, faster and easier to audit.

## Evidence from our own measurements
1. **Circular training signal.**
   - The SFT data came from our simulator plus a rule-based policy, so a fine-tuned model could at best imitate rules we had already written.
   - The rule baseline already scored **0.907 verdict accuracy**, and the oracle 1.000.
   - The LLM added latency (seconds per turn on the M2) and nondeterminism to approximate a function we could compute exactly.
2. **Wrong tool for the job.**
   - "Is there a fire, where, how sure, which sensor is faulty?" is sensor fusion, an estimation problem.
   - Bayesian inference gives calibrated probabilities and locations with uncertainty, all reproducible. An LLM gives a guess with a confidence number it made up.
3. **The "novel" features were textbook optimization.**
   - Airtime-budgeted questioning is value-of-information / Bayesian experimental design.
   - Edge retuning is adaptive thresholding.
   - Both have deterministic solutions that beat a model's intuition.
4. **Hardware realism.**
   - A 160×120 camera and a MEMS mic detect flame at **centimetre-to-metre range** (a lighter at 10–15 cm in bring-up).
   - Wildfire detection needs tens to hundreds of metres, which is why Dryad uses gas sensors and Pano uses tower cameras.
5. **What worked was already math.** Flame detection only became reliable with scene-relative thresholds and a median frame-to-frame flicker statistic.

## Principle adopted: calculator + assistant
- **Calculator:** deterministic code makes every decision. It detects changes, fuses evidence, computes probabilities and applies an explicit cost of mistakes. Same input, same answer, and it shows its working.
- **Assistant:** a small local language model is used only where language is genuinely needed:
  - explaining decisions grounded in the engine's state;
  - turning plain-English policies into configs that code verifies;
  - triaging unusual patterns for a human.
- **The assistant proposes; the calculator verifies and decides.**

## Why wireless security instead of another environmental domain
- **The ESP32's best sensor is its radio.** Passive 802.11 management-frame capture and BLE scanning work at room-to-building range, which fixes v1's range problem with no extra sensors.
- **The threats are concrete and measurable:** evil twins, deauth floods, beacon spam, tracker following. Ground truth is easy to stage safely with your own devices.
- **Spatial fusion actually matters.** Several arms corroborate a detection and locate its source, which a single Kismet box can't do cheaply.
- **LoRa backhaul is now justified technically, not just for the story.** ESP-NOW shares the 2.4 GHz radio and pins it to one channel, which breaks channel-hopping sniffing. LoRa leaves that radio free.
- **The LLM learning targets become meaningful:**
  - faithfulness-checked explanations (SFT → DPO);
  - policy compilation with a verifier reward (GRPO);
  - tool-using novelty triage.

  None of them imitates our own rules.

## What v1 contributes to v2
- Flood mesh: TTL, dedup, ≤200 B.
- Airtime model: `gateway/airtime.py` already prices LoRa presets.
- Wire protocol, swarm state, dashboard and serial bridge.
- The simulator → eval harness → training pipeline pattern.
- GitHub Actions firmware build and browser flasher.
- Labs 00–02.

The v1 LLM decider survives as a **baseline to beat** in the eval.
