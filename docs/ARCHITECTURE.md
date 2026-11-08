# EMBER v2 architecture (lean)

Goals, guardrails and metrics: [OBJECTIVES.md](OBJECTIVES.md). The principle throughout: **the calculator decides, the models assist and explain.**

Lean v2 is one sensor arm over USB, plus a laptop that runs the engine, the response playbook and a fine-tuned local model that writes the incident report. Every model in it is trained and deployed locally by the builder: that is half the point of the project (see the learning track in [labs/README.md](../labs/README.md)).

```
 ┌──────────── sensor arm (XIAO ESP32-S3) ───────────────┐
 │ Wi-Fi promiscuous (MGMT filter only) · passive BLE    │
 │ channel hopping 1–13 @ 300 ms                         │
 │ on-node: AP table, feature window per dwell (W),      │
 │          TinyML attack score (L2), address hashing,   │
 │          payloads never seen                          │
 └───────────────┬───────────────────────────────────────┘
                 │ USB serial: ELP v2 H/A/W/E/B (≤200 B, text)
                 ▼
 ┌──────────── laptop (M2) ──────────────────────────────┐
 │ gateway/  bridge · airspace mirror · dashboard        │
 │           (/airspace) · capture record / replay       │
 │ engine/   detectors · TinyML fusion · decision        │
 │           → response playbook → incident record       │
 │ local SLM (llama.cpp, fine-tuned): incident record →  │
 │           report; checker scores every report         │
 └───────────────────────────────────────────────────────┘
```

**The two models (both trained here, both run locally):**

| Tier | Model | Job | Decides? | Where / how it's trained |
|---|---|---|---|---|
| Edge | **TinyML classifier** (int8, a few KB: small MLP over feature windows) | per-window attack score: benign / deauth-flood / beacon-spam / evil-twin-suspect / tracker | no: evidence for the engine, never alerts alone | on the arm; trained from scratch in PyTorch on simulator windows + your own captures, int8 C ([Lab 10](../labs/10-tinyml/README.md)) |
| Laptop | **small LM** (0.6–4B, GGUF on llama.cpp) | writes the **incident report**: what was detected, the evidence, and the response the playbook chose | no: documents decisions already made | LoRA SFT (distilled from a larger local model, checker-filtered) → DPO → optional GRPO (labs 02–05, 07, 08) |

## 1. Sensor arm (firmware, done in R1)
- **Wi-Fi capture:** `esp_wifi_set_promiscuous` with `WIFI_PROMIS_FILTER_MASK_MGMT`, so data frames never reach the firmware. Each management frame is parsed for kind, addresses, sequence number, SSID, channel, security (RSN/WPA IEs, PMF), beacon interval, TSF and an IE fingerprint (`firmware/src/frames.*`).
- **BLE:** passive scan only. Addresses are hashed on the arm; Apple Find My, Tile, Samsung SmartTag and Google Find My trackers are flagged.
- **Feature window per channel dwell** (`firmware/src/features.*`): frame counts by kind (exact even under a flood), broadcast and spoof-suspect deauths, distinct BSSIDs/SSIDs/senders/probers, sequence/TSF/IE anomalies on known APs, RSSI. This is the `W` packet and the TinyML input.
- **AP table:** each AP reported on first sight, on change and every 60 s while seen.
- **Privacy:** BLE addresses → `trunc(SHA-256(salt‖addr), 4 B)` with a per-boot salt; only counts of client devices leave the arm.

## 2. ELP v2 packets
Text, pipe-delimited, ≤200 B, the same grammar and serial bridge as v1. Authoritative field list: [protocol/SPEC.md §4](../protocol/SPEC.md).

| Type | Example | Purpose |
|---|---|---|
| `W` feature window | `W\|n1\|s44\|ch6\|dt300\|bcn12\|…\|dea0\|…\|sqa0\|tsj0\|iec0\|rx-41\|ra-67` | one per dwell; TinyML input and training data |
| `A` AP digest | `A\|n1\|s41\|b:a1b2c3d4e5f6\|ss:Home%20Net\|ch6\|sec:W2\|pmf1\|r-61\|bi100\|ie:9f3a` | new / changed / refreshed AP |
| `E` event | `E\|n1\|s42\|DEA\|ch6\|b:a1b2c3d4e5f6\|cnt20\|rate66\|bc20\|spf20\|r-40` | arm-side detector fired (R1 thresholds now; TinyML events in L2) |
| `B` BLE tracker | `B\|n1\|s43\|h:7c19aa02\|trk1\|mf:004C\|rnd1\|r-70` | tracker sighting |
| `H` heartbeat | `H\|n1\|s40\|up3600\|ap23\|ble14\|trk1\|drp0\|occ:1=34,6=71` | liveness, table sizes, channel load |

## 3. Engine (`engine/`, Python, L1)
Input is the airspace mirror (`gateway/airspace.py`), so the live arm, replayed captures and the simulator all go through one path.

| Module | Method |
|---|---|
| `fingerprint` | per-SSID baseline {BSSIDs, security, PMF, channel, beacon interval, IE hash} learned during a quiet period + `config/own_network.json` |
| `detect.flood` | Poisson-rate **CUSUM** on deauth/disassoc counts per window: `S_t = max(0, S_{t-1} + x_t·ln(λ1/λ0) − (λ1−λ0)·Δt)`, alarm at `S_t > h` |
| `detect.beacon_spam` | CUSUM on never-seen BSSIDs per window |
| `detect.evil_twin` | for a BSSID claiming a known SSID (or a known BSSID showing `tsj`/`sqa`/`iec` anomalies): sum of log-likelihood ratios of mismatches (security downgrade, PMF dropped, IE fingerprint, beacon interval, clock/sequence anomalies) |
| `detect.tracker` | an unknown tracker seen persistently over many windows inside your space (a neighbour's stationary tag is the look-alike) |
| `fuse` | the arm's TinyML score (L2) moves the likelihood but **never triggers an alert alone**, which keeps a verifiable floor |
| `decide` | posterior from detector scores → ALERT if `p > C_fa / (C_fa + C_miss)`, WATCH band below, else OK, with a reason trace (which features, which windows, which numbers) |

## 4. Response playbook (`engine/response.py`, L1): deterministic, never transmits
For each decision, a fixed playbook (threat × your AP's security × `config/own_network.json`) chooses the response and records it:
- **actions taken:** alert level on the dashboard, an evidence record (timestamped incident record + the raw windows) written to disk;
- **recommendations for you:** e.g. "your AP has no PMF: enable WPA3/WPA2-PMF", "a network is impersonating HomeNet: forget it on your devices and don't join it", "unknown tracker present for 20 min: search the room".

It never transmits on Wi-Fi or BLE. Changing your router through its API is deferred, and the docs don't claim enforcement EMBER doesn't have.

## 5. Incident record and report (`engine/incident.py`, L3)
- The **incident record** (JSON) holds the timeline, threat type, BSSIDs/SSIDs/channels/counts, confidence, reason trace, actions taken and recommendations. It is the single source of truth.
- The **fine-tuned local model** turns it into a readable report: summary, evidence, what EMBER did, what you should do.
- The **checker** (`training/report_check.py`) scores every report automatically:
  - **faithfulness:** every BSSID, SSID, channel, number and time must exist in the record;
  - **coverage:** the required facts are present;
  - **no invented actions:** claimed actions ⊆ actions taken;
  - **format:** the required sections are present.

  The checker is the eval metric, the rejection-sampling filter for distillation, the DPO pair source and the GRPO reward.

## 6. Simulator and evaluation (`rfsim/`, L1)
- **Scenarios:**
  - normal home;
  - roaming mesh AP (benign look-alike);
  - deauth flood (PMF / no PMF);
  - beacon spam;
  - evil twin (cloned BSSID, or a new BSSID with the same SSID and weaker security);
  - tracker brought inside;
  - a neighbour's stationary tag (benign).
- **Output:** emits the same ELP v2 lines as the firmware, with per-window labels and incident ground truth. Benign traffic is calibrated from your captures.
- **Metrics:** recall, false alarms per hour of normal traffic, time to detect.
- **Baselines:** the R1 arm thresholds, then the engine without TinyML.

## 7. Reuse map
- **Kept from v1:** `settings.*`, `roles.h`, `main.cpp` (roles `arm`/`node`/`gateway`); `protocol.py`, `bridge.py`, `server.py` + dashboards; `sft_lora.py`, `sft_data.py`, `merge_lora.py`, `export_gguf.sh`, `plot_metrics.py`, `bakeoff.py`; `scripts/serve_gguf.sh`, `gateway/llm.py`; `site/` flasher + console.
- **Keep until L3 (labs 03–05 depend on them):** `gateway/sim.py`, `policy.py`, `agent.py`, `tools.py`, `prompts.py`, `episode.py`, `training/gen_data.py`. L1 builds `rfsim/` and `engine/` *beside* them; once L3 re-targets the labs to the report task, the wildfire code can go (tag `wildfire-v1` keeps it).

## 8. Roadmap
Each phase ends with a ✅ checkpoint and numbers in [LEARNING_LOG.md](LEARNING_LOG.md); the runbook is [SETUP_GUIDE.md](SETUP_GUIDE.md).

| Phase | Build | You learn | ✅ Checkpoint |
|---|---|---|---|
| L0 | scope reset; ship **R1** (one arm over USB, `/airspace`, record/replay) | ESP32 radio capture, FreeRTOS, CI flashing | on-board: selftest PASS, APs listed with correct security/PMF, deauths counted during your own router reboot, a capture recorded |
| L1 | `rfsim/` + `engine/` + response playbook + incident record; `training/rf_eval.py` | detection math (CUSUM, likelihood ratios), evaluation design (Lab 06) | engine beats the R1 thresholds; replayed real captures raise no false incidents |
| L2 | **TinyML detector** (the novelty): dataset → train → int8 → arm firmware → fusion | supervised training from scratch, quantization, on-device inference ([Lab 10](../labs/10-tinyml/README.md)) | C/Python int8 parity; TinyML vs thresholds table; µs/flash/RAM on the arm |
| L3 | **incident-report model**: checker, distilled dataset, bake-off, SFT, DPO, (GRPO), GGUF, dashboard "Write report" | SFT, DPO, GRPO, local serving (labs 02–05, 07, 08) | base vs SFT vs DPO: faithfulness, coverage, 0 invented actions; live report via llama.cpp |
| L4 | write-up + 2–3 min demo | presenting measured results | published |

## 9. Deferred (designed, not built)
- **Multiple arms + core:** time-sliced ESP-NOW (each arm returns to a home channel for a TDMA slot), per-arm trust (Beta reliability), a daily salt shared by the core, corroboration across arms.
- **Zone localization:** log-distance path loss + weighted least squares over ≥3 arms → a zone label ("near the north wall"), never a point.
- **LoRa SX1262 transport** (RadioLib, v1's flood logic behind a `transport.h` interface), priced by `gateway/airtime.py`.
- **Router / Home Assistant actions:** blocking an unknown client on your own router via its API, dry-run, logged, reversible, refusing unregistered targets.
- **Assistant extras:** grounded Q&A, policy → config, novelty triage.
- **Research extras:** RL active-sensing scheduler, RTL-SDR wideband survey, RF fingerprinting, sub-GHz.
