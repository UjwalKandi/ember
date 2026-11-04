# EMBER: Edge Mesh for Burn Early-warning & Reporting

**Existing wildfire networks detect. EMBER investigates, and re-programs its own sensors while doing it.**

EMBER is an **agentic sensor mesh**. Cheap XIAO ESP32-S3 Sense nodes (camera + microphone) detect flame, smoke, crackling and motion on-device and send 30-byte coded packets over a multi-hop, Meshtastic-class radio mesh. A **fine-tuned Muse Glimmer** at the command center doesn't just read that telemetry. It runs a closed loop through the radio:
- **interrogates** nodes (re-sample this one, probe the downwind neighbor),
- **pays for every question in LoRa airtime**,
- decides **ALERT / WATCH / DISMISS**,
- **re-programs the nodes' on-device detectors** so the same false alarm doesn't happen again,
- writes the incident report.

> Hackathon track: **Push the Frontier**: a language model in the control loop of a physical sensing network.

## What's new

| | Existing systems | EMBER |
|---|---|---|
| **Sensing loop** | Passive: sensor → threshold/ML → alert (e.g. Dryad Silvanet's LoRaWAN gas-sensor mesh, AI camera towers) | **Active perception:** the model chooses which node to question next, using wind physics (smoke drifts downwind, so ask the downwind neighbor) |
| **LLM + Meshtastic** | Chatbots: people text an LLM over the mesh (radio-llm, LORACLE and ~45 similar repos) | The LLM **operates the mesh**: reads raw telemetry, sends commands, gets answers back over the radio |
| **Bandwidth** | Ignored, or handled by fixed reporting intervals | **Airtime-aware agent:** every query is priced with the Semtech LoRa time-on-air formula × relay hops, against a 1% duty-cycle budget. The model learns value of information: under tight budgets it asks one question or uses free history |
| **Edge behavior** | Fixed firmware thresholds; false alarms repeat | **The model re-programs the edge:** fog → re-baseline haze swarm-wide; glare-prone node → require flame flicker; faulty sensor → mute it. Measured: in held-out sim episodes, after-action false-alarm events drop 126 → 0 (fog) and 20 → 1 (glare) when the right retune is sent |
| **Wire format** | JSON / protobuf telemetry | **Ember Link Protocol**, co-designed so the model reads raw packets natively: 2.6× smaller than equivalent JSON |
| **Training data** | Human-labelled incidents (rare, expensive) | **Physics digital twin → specialist model:** fire spread + smoke plume + false-alarm simulator generates ground-truth agent trajectories; zero human labels |

## How it works

```
 [XIAO node]~~ESP-NOW flood mesh (≤200 B, TTL 3)~~[XIAO node]~~...~~[gateway XIAO]==USB==[laptop: command center]
  camera → flame ratio, haze index, motion                              │  gateway/  (Python, stdlib only)
  mic    → sound level, crackle transients                              │   swarm state + airtime ledger
  E|n3|s42|FLM|c81|hz34|snd12                                           │   tools: confirm / ping / history /
  C|*|q9|SET|rebase1   ◄── edge re-programming ──────────────────────── │          tune_detectors / alert
                                                                        ▼
                                                           Muse Glimmer (fine-tuned, vLLM on Modal)
                                                           → airtime-priced tool calls over the mesh
                                                           → submit_assessment + SITREP
```

**The core idea:** ESP32s can't run an LLM and a LoRa-class link can't carry images. So the nodes reduce the world to the compact **Ember Link Protocol** ([protocol/SPEC.md](protocol/SPEC.md)), and we **supervised-fine-tune Muse Glimmer to think and act in that language**. It reads raw packets, knows which question is worth its airtime, separates real fires from look-alikes (campfires, sun glare, vehicle tail-lights and dust, fog, stuck sensors, dead nodes), and fixes the look-alikes at the source by re-tuning the nodes.

**Meshtastic-ready:** the boards we have don't carry LoRa radios, so the transport is ESP-NOW with Meshtastic's constraints (≤200 B payloads, managed flooding, hop limit 3, duplicate suppression), and airtime is priced as if on LoRa LongFast. Swapping in a Wio-SX1262 + Meshtastic only replaces `firmware/src/mesh.cpp`; nothing above it changes.

## Training Muse Glimmer

| step | what | where |
|---|---|---|
| simulate | 14 scenarios (wildfire variants, campfire, sun glare, vehicle, dusty road, fog, fire-in-fog, stuck sensor, dying node, …) on random node grids with wind, packet loss and multi-hop | `gateway/sim.py` |
| label | ground truth from the simulator; airtime-priced, value-of-information tool-use skeleton from an observation-only policy; edge re-programming actions | `training/gen_data.py` |
| format | OpenAI `messages`, one tool call per assistant message (Muse Glimmer's format), arguments as dicts, + tool schemas; ~2.4k trajectories | `data/*.jsonl` |
| train | Axolotl QLoRA on 1×H100 (Modal): cookbook hyperparameters on Axolotl's text-only tool-calling path, 8k context | `training/modal_app.py` |
| serve | merged checkpoint on the `vllm/vllm-openai:muse-glimmer` image, `muse_glimmer` tool + reasoning parsers | `training/modal_app.py` |
| evaluate | 420 held-out episodes: verdict/cause accuracy, false alarms, misses, localization, node-health F1, accuracy under tight airtime, false-alarm events in the 5 min after acting (vs a no-action replay of the same seed) | `training/eval.py` |

### Results (held-out, 420 episodes; 1/3 under tight or exhausted airtime budgets)

`wildfire_plus_stuck` and `wildfire_in_fog` are **never seen in training**. They test generalization to new combinations. The **oracle** is the SFT teacher with access to simulator ground truth; it is an upper bound, not a competitor.

| metric | rule-based baseline | Muse Glimmer (base) | **EMBER (fine-tuned)** | oracle (upper bound) |
|---|---|---|---|---|
| verdict accuracy | 0.907 | _pending_ | _pending_ | 1.000 |
| cause accuracy | 0.848 | | | 1.000 |
| false-alarm rate | 0.125 | | | 0.000 |
| missed fires | 0.000 | | | 0.000 |
| accuracy under tight airtime | 0.814 | | | 1.000 |
| false-alarm events after acting (reduction) | 9.7% | | | 67.6% |
| correct decisions per airtime-second | 0.18 | | | 0.21 |
| unseen compositions | 1.00 (rules don't train) | | | 1.00 |
| fog / dusty-road / vehicle cases | 0.00 / 0.67 / 0.70 | | | 1.00 / 1.00 / 1.00 |

Regenerate: `python -m training.eval --report results/*.json`

## Quickstart

Full step-by-step runbook (setup, accounts, hardware, training, eval, demo, plus how to reuse this blueprint): **[docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md)**. Hardware bring-up results and camera calibration: [docs/HARDWARE_BRINGUP.md](docs/HARDWARE_BRINGUP.md). Demo flow: [docs/demo-script.md](docs/demo-script.md). Browser flasher + serial console: https://ujwalkandi.in/ember/

```bash
# 1. Command center on a fully simulated swarm (no hardware, no GPU)
python3 -m gateway.server --sim wildfire --auto --show-truth      # http://localhost:8080 (click map to ignite)

# 2. With real nodes: flash from the browser at https://ujwalkandi.in/ember/ (Chrome/Edge), set ids in the console,
#    then plug a gateway or a single node into the laptop (see docs/SETUP_GUIDE.md Phase C)
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
firmware/               XIAO ESP32-S3 Sense: one image, runtime role/id, ESP-NOW flood mesh, flicker-gated flame + haze + mic detectors, USB console
gateway/                command center: protocol, sim, swarm state, tools, agent loop, dashboard server
training/               dataset builder, Modal QLoRA + vLLM app, evaluation harness
config/nodes*.json      demo node layouts (real + virtual; nodes-single.json for one board)
site/                   browser flasher (ESP Web Tools) + Web Serial console, served on GitHub Pages
.github/workflows/      firmware cloud build → merged image → Pages deploy
docs/                   SETUP_GUIDE (runbook), HARDWARE_BRINGUP (test log + calibration), demo-script
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
