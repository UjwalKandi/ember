# EMBER v2 architecture

Goals, guardrails and metrics: [OBJECTIVES.md](OBJECTIVES.md). The principle throughout: **the calculator decides, the assistant explains.**

```
 ┌──────────── sensor arm (XIAO ESP32-S3) ×3 ────────────┐
 │ Wi-Fi promiscuous, MGMT filter only    BLE adv scan   │
 │ channel-hop plan from the core                        │
 │ on-node: AP table, frame-rate counters, CUSUM,        │
 │          MAC hashing (daily salt), payloads dropped   │
 └───────────────┬───────────────────────────────────────┘
                 │ ELP v2 packets ≤200 B (digests + events)
                 ▼ transport: LoRa SX1262 flood mesh (RadioLib)  |  $0 dev: USB / time-sliced ESP-NOW
 ┌──────────── core (XIAO ESP32-S3) ─────────────────────┐
 │ deterministic engine: fingerprints · detectors ·      │
 │ fusion · localization · scheduler · decision rule     │
 │ → ALERT / WATCH / OK + probability + reason trace     │
 └───────────────┬───────────────────────────────────────┘
                 │ USB serial (newline-terminated)
                 ▼
 ┌──────────── laptop (M2) ──────────────────────────────┐
 │ gateway/: bridge, state mirror, dashboard (map,       │
 │ incidents, channel heatmap), simulator, eval          │
 │ assistant: local SLM via llama.cpp (:8081), read-only │
 │ tools over engine state; config proposals → verifier  │
 └───────────────────────────────────────────────────────┘
```

**Where the engine runs:**
- **Development:** the engine is written first in Python (`engine/`), so the simulator, eval and tests can drive it.
- **Firmware:** the parts that must be on-device are ported to the core firmware: the decision rule, detector thresholds and the hop-plan broadcast.
- **Heavier work:** localization and incident bookkeeping can stay on the laptop mirror, as long as the core's own alert decision doesn't depend on them.

## 1. Sensor arms (firmware)
- **Wi-Fi capture:**
  - `esp_wifi_set_promiscuous(true)` with `WIFI_PROMIS_FILTER_MASK_MGMT` (plus CTRL only if needed);
  - data frames are never delivered to our callback;
  - parsed from each frame: beacons, probe requests/responses, auth, assoc, deauth and disassoc;
  - fields kept: BSSID, SSID, channel, RSSI, security (RSN/WPA IEs), beacon interval, TSF timestamp, and a short hash of the ordered IE list (the "IE fingerprint").
- **BLE:**
  - passive scan only (no scan requests);
  - each advertisement yields: hashed address, address type (public/random), manufacturer ID, service UUIDs, RSSI;
  - known tracker signatures (Apple Find My, Tile, Samsung SmartTag) are flagged by manufacturer data / service UUID.
- **Channel hopping:** the arm follows a dwell plan from the core, e.g. `ch1:400ms,ch6:400ms,ch11:400ms,...`. The default plan is staggered so that 3 arms together cover channels 1–13.
- **On-node reduction:**
  - **AP table:** each AP is reported once, then only when something changes.
  - **Counters per channel and BSSID:** deauth/disassoc rate, new-SSID rate, probe-response fan-out.
  - **CUSUM** on those rates raises an event immediately.
  - **Heartbeat** every 30 s, carrying channel occupancy.
- **Privacy:** client MACs → `trunc(SHA-256(salt‖MAC), 6 B)`. The salt rotates daily and is shared mesh-wide by the core, so hashes correlate across arms but not across days.

## 2. ELP v2 packets (text, ≤200 B, like v1)
| Type | Example | Purpose |
|---|---|---|
| `H` heartbeat | `H\|n2\|s40\|up3600\|occ:1=34,6=71,11=12\|plan7` | liveness, channel load (%) |
| `A` AP digest | `A\|n2\|s41\|b:a1b2c3d4e5f6\|ss:CafeWiFi\|ch6\|sec:W2\|r-61\|bi100\|ie:9f3a` | new/changed AP |
| `E` event | `E\|n2\|s42\|DEA\|ch6\|b:a1b2c3d4e5f6\|rate240\|r-55` | detector fired (DEA deauth, BSP beacon spam, KAR probe fan-out, TSF clock jump, TRK tracker seen) |
| `B` BLE digest | `B\|n1\|s43\|h:7c19aa02\|mf:004C\|trk1\|r-70` | tracker / notable advertiser |
| `C` command | `C\|n2\|q9\|PLAN\|ch6:800,ch1:200` | core re-tasks an arm |
| `R` response | `R\|n2\|q9\|OK` | ack |

`gateway/airtime.py` prices each packet on the chosen LoRa preset, and the core enforces a duty-cycle budget per arm.

## 3. Transport
- **Interface:** one firmware interface (`transport.h`: `begin`, `send`, `loop`, `onReceive`) with three drivers:
  - **USB** (R1): a single arm prints packets on serial.
  - **Time-sliced ESP-NOW** (R3, $0):
    - the core stays on home channel 1;
    - every 5 s each arm returns to channel 1 for its TDMA slot (`slot = id × 60 ms`), sends its queued packets, then resumes hopping;
    - cost: about 5% blind time per arm, which is measured and reported.
  - **LoRa SX1262** (R4):
    - RadioLib on the Wio-SX1262, with v1's flood mesh logic (TTL 3, dedup, ≤200 B);
    - a LongFast-like preset;
    - no Meshtastic firmware, since we don't need its apps or protocol.
- Everything above the transport is identical across the three drivers.

## 4. Deterministic engine (`engine/`, Python first)
| Module | Method |
|---|---|
| `fingerprint` | per-SSID baseline profile {BSSIDs, OUI vendors, security, channels, beacon interval, IE hash, TSF rate}, learned during a quiet baseline period + user allow-list |
| `detect.evil_twin` | for a new BSSID claiming a known SSID, sum log-likelihood ratios of mismatches (security downgrade, unknown OUI, different IE hash, different beacon interval, TSF reset/jump, RSSI where the real AP is never seen) → score |
| `detect.flood` | Poisson-rate **CUSUM**: `S_t = max(0, S_{t-1} + x_t·ln(λ1/λ0) − (λ1−λ0)·Δt)`, alarm when `S_t > h`; corroborated when ≥2 arms fire within a window |
| `detect.beacon_spam`, `detect.karma` | new-SSID rate CUSUM; one BSSID answering probe requests for ≥N distinct SSIDs |
| `detect.tracker` | the same hashed tracker seen across ≥K distinct time windows and arms while the user's own phone (allow-listed) is also seen moving between them → co-occurrence score |
| `trust` | per-arm Beta(α, β) reliability (corroborated vs uncorroborated events); silent / stuck-arm tests (from v1 `gateway/policy.py`) |
| `localize` | log-distance path loss `RSSI = P0 − 10·n·log10(d) + ε`; weighted least squares over position (and P0) from ≥3 arms → estimate + covariance ellipse (from the Jacobian); per-site calibration of n and P0 by walking a known AP around |
| `schedule` | assign channel dwell to arms by greedy **expected information gain**: by default, coverage weighted by channel activity; when something is suspicious, pull ≥3 arms onto the suspect's channel so it can be localized |
| `decide` | posterior `p` from detector scores (calibrated in simulation) → Bayes rule with costs: ALERT if `p > C_fa / (C_fa + C_miss)`, WATCH band below, else OK; every decision carries a reason trace (which features, which arms, which numbers) |

## 5. Simulator and evaluation (rewrite of `gateway/sim.py`, `episode.py`, `training/eval.py`)
- **Scenarios:**
  - normal café / office traffic
  - roaming APs (mesh Wi-Fi with several BSSIDs, a legitimate case)
  - evil twin (open, and WPA2 with a matching SSID)
  - deauth flood (with and without PMF)
  - beacon spam
  - KARMA
  - a tracker following the user
  - a stationary tracker (a neighbour's tag, which shouldn't alert)
  - arm faults (silent, stuck)
  - LoRa packet loss
- **Ground truth:** comes from the simulator. Arms are placed on a floor plan, and RSSI comes from the path-loss model with shadowing.
- **Metrics:** recall and false alarms per threat, time to detect, localization error, airtime used, blind time.
- **Baselines:**
  - the rule thresholds;
  - the v1-style LLM decider (from tag `wildfire-v1`, re-prompted for RF).

## 6. Assistant (local SLM; never in the decision path)
| Role | Input → output | Check | Training (labs) |
|---|---|---|---|
| Grounded assessment / Q&A | engine state + reason trace → incident report, answers ("why is n2's alert WATCH, not ALERT?") | automatic faithfulness: every node id, hash, channel and number must exist in the state | SFT → DPO on faithful vs unfaithful pairs |
| Policy → config | "alert on anyone impersonating CafeWiFi; my printer's AP is fine; quiet hours 1–6 am" → JSON config | JSON schema + verifier (invariants: can't disable all detectors, the allow-list must name observed BSSIDs) + simulated outcome | SFT → GRPO with verifier reward |
| Novelty triage | low-likelihood pattern → tool calls (read-only queries of state/history) → hypotheses for a human | never alerts; graded on simulated novel scenarios | agent lab |

**Assistant tools are read-only**: `get_incident`, `get_ap_profile`, `get_arm_history`, `get_channel_load`, `propose_config`. Proposals go through the verifier, and a human applies them.

## 7. Reuse map
- **Keep and adapt:**
  - `firmware/src/mesh.*` → becomes the flood logic behind `transport.h`
  - `settings.*`, `roles.h`, `main.cpp` (roles: `arm`, `core`)
  - `gateway/airtime.py`, `protocol.py` (ELP v2), `bridge.py`, `swarm.py` → state mirror; `server.py` + `static/index.html` → dashboard
  - `training/eval.py`, `sft_lora.py`, `sft_data.py`, `merge_lora.py`, `export_gguf.sh`, `bakeoff.py`
  - `scripts/serve_gguf.sh`, `site/` + Actions flasher
- **Replace:**
  - `sense_cam.*`, `sense_mic.*` → `sense_wifi.*`, `sense_ble.*`
  - `gateway/policy.py` heuristics → `engine/`
  - `gateway/prompts.py`, `tools.py`, `agent.py` → assistant roles
- **History:** the v1 code lives at tag `wildfire-v1`.

## 8. Roadmap
Each phase ends with a ✅ checkpoint; the runbook is [SETUP_GUIDE.md](SETUP_GUIDE.md).

| Phase | Build | ✅ Checkpoint |
|---|---|---|
| R0 | objectives, architecture, assessment docs; tag `wildfire-v1` | docs published; tests still pass |
| R1 | one arm over USB: `sense_wifi`, `sense_ble`, ELP v2 `H/A/E/B` | dashboard lists nearby APs (channel, RSSI, security) and counts deauth frames |
| R2 | RF simulator + `engine/` + eval | unit tests; engine-vs-baseline table |
| R3 | 3 arms + core over time-sliced ESP-NOW; `localize`, `schedule` | authorized self-test: your own hotspot cloned as an evil twin → detected and located |
| R4 | LoRa SX1262 transport | same demo over LoRa, airtime budget enforced |
| R5–R7 | assistant labs: grounded Q&A (SFT→DPO), policy→config (SFT→GRPO), triage agent | numbers in `docs/LEARNING_LOG.md` |
| R8 | write-up + demo video | published |
