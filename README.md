# EMBER: Edge Mesh for Burn Early-warning & Reporting

**A swarm of $15 camera nodes on a Meshtastic-style mesh, commanded by a fine-tuned Muse Glimmer that investigates, decides, and writes the incident report.**

Wildfires start where there is no cell coverage and no one watching. EMBER puts cheap XIAO ESP32-S3 Sense nodes (camera + microphone) across a forest or field. Nodes detect flame, smoke, crackling and motion **on-device** and send tiny coded packets over a multi-hop, low-bandwidth mesh. At the command center, **Muse Glimmer** (fine-tuned for this protocol) reads the packets, **interrogates the swarm through tools** (re-sample a node, check the downwind neighbor, ping a silent node), decides between **ALERT / WATCH / DISMISS**, broadcasts alerts back into the mesh, and writes a situation report for the operator.

> Hackathon track: **Push the Frontier**: give a machine network a new capability by putting Meta's model in the loop of a physical sensing system.

## How it works

```
 [XIAO node]~~ESP-NOW flood mesh (≤200 B, TTL 3)~~[XIAO node]~~...~~[gateway XIAO]==USB==[laptop: command center]
  camera → flame ratio, haze index, motion                              │  gateway/  (Python, stdlib only)
  mic    → sound level, crackle transients                              │   swarm state + compact context
  E|n3|s42|FLM|c81|hz34|snd12                                           │   tools: confirm / ping / set rate / alert
                                                                        ▼
                                                           Muse Glimmer (fine-tuned, vLLM on Modal)
                                                           → tool calls back over the mesh
                                                           → submit_assessment + SITREP
```

**The core idea:** ESP32s can't run an LLM and a LoRa-class link can't carry images. So the nodes reduce the world to the compact **Ember Link Protocol** ([protocol/SPEC.md](protocol/SPEC.md)), and we **supervised-fine-tune Muse Glimmer to think in that language**: reading raw packets, knowing which follow-up questions to ask the swarm, and separating real fires from look-alikes (campfires, sun glare, vehicle tail-lights and dust, fog, stuck sensors, dead nodes).

**Meshtastic-ready:** the boards we have don't carry LoRa radios, so the transport is ESP-NOW with Meshtastic's constraints (≤200 B payloads, managed flooding, hop limit 3, duplicate suppression). Swapping in a Wio-SX1262 + Meshtastic only replaces `firmware/src/mesh.cpp`; nothing above it changes.

## Training Muse Glimmer

| step | what | where |
|---|---|---|
| simulate | 14 scenarios (wildfire variants, campfire, sun glare, vehicle, dusty road, fog, fire-in-fog, stuck sensor, dying node, …) on random node grids with wind, packet loss and multi-hop | `gateway/sim.py` |
| label | ground truth from the simulator; tool-use skeleton from an observation-only policy | `training/gen_data.py` |
| format | OpenAI `messages`, one tool call per assistant message (Muse Glimmer's format), arguments as dicts, + tool schemas; ~2.4k trajectories | `data/*.jsonl` |
| train | Axolotl QLoRA on 1×H100 (Modal): cookbook hyperparameters on Axolotl's text-only tool-calling path, 8k context | `training/modal_app.py` |
| serve | merged checkpoint on the `vllm/vllm-openai:muse-glimmer` image, `muse_glimmer` tool + reasoning parsers | `training/modal_app.py` |
| evaluate | 420 held-out episodes: verdict/cause accuracy, false alarms, misses, localization, node-health F1 | `training/eval.py` |

### Results (held-out, 420 episodes)

`wildfire_plus_stuck` and `wildfire_in_fog` are **never seen in training**. They test whether the model generalizes to new combinations of situations it did see.

| metric | rule-based baseline | Muse Glimmer (base) | **EMBER (fine-tuned)** |
|---|---|---|---|
| verdict accuracy | 0.921 | _pending_ | _pending_ |
| cause accuracy | 0.876 | | |
| false-alarm rate | 0.125 | | |
| missed fires | 0.006 | | |
| unseen compositions | 1.00 (rules don't train) | | |
| fog / dusty-road / vehicle cases | 0.00 / 0.67 / 0.70 | | |

Regenerate: `python -m training.eval --report results/*.json`

## Quickstart

```bash
# 1. Command center on a fully simulated swarm (no hardware, no GPU)
python3 -m gateway.server --sim wildfire --auto --show-truth      # http://localhost:8080 (click map to ignite)

# 2. With real nodes: flash firmware (see firmware/README.md), plug in the gateway XIAO
pip install pyserial
python3 -m gateway.server --serial /dev/cu.usbmodem1101 --nodes config/nodes.json --sim quiet --auto
#    ids marked "real" in config/nodes.json are hardware; the rest are simulated alongside them

# 3. Use Muse Glimmer instead of the rule-based baseline
export EMBER_LLM_URL=https://<workspace>--ember-serve-ember.modal.run/v1 EMBER_LLM_MODEL=ember EMBER_LLM_KEY=...
python3 -m gateway.server --sim fog --agent llm

# 4. Train / serve / evaluate (see training/modal_app.py docstring for the full pipeline)
python3 -m training.gen_data --out data
modal run training/modal_app.py::download && modal run training/modal_app.py::train
python3 -m training.eval --agent llm --url $EMBER_LLM_URL --model ember --key $EMBER_LLM_KEY

# tests
python3 -m unittest discover -s tests -v
```

## Repository map

```
protocol/SPEC.md        Ember Link Protocol: frame, packet grammar, serial bridge
firmware/               XIAO ESP32-S3 Sense: ESP-NOW flood mesh, camera + mic detectors, node & gateway roles
gateway/                command center: protocol, sim, swarm state, tools, agent loop, dashboard server
training/               dataset builder, Modal QLoRA + vLLM app, evaluation harness
config/nodes.json       demo node layout (real + virtual)
tests/                  stdlib unittest suite
```

## Built during the hackathon

Everything in this repository was written during the Meta AI Developer Hackathon (see commit history): the protocol, firmware, simulator, command center, dashboard, dataset, training pipeline and evaluation.

Prior work and third-party material:
- **[A.I.D.E](https://github.com/UjwalKandi/A.I.D.E)** (the author's earlier project, on-device log diagnostics) inspired the "local model writes the diagnostic report" idea. **No code is reused.**
- The QLoRA hyperparameters are the Muse Glimmer cookbook's Axolotl config ([meta-models/meta-oss-cookbook](https://github.com/meta-models/meta-oss-cookbook), Apache-2.0), loaded at train time and overridden for EMBER. The vLLM flags follow the cookbook's `inference-server/vllm.md`.
- All training data is synthetic, generated by `gateway/sim.py`.

## License

MIT
