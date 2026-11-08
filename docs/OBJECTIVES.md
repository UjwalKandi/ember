# EMBER v2 objectives

EMBER v2 is a **distributed, passive wireless intrusion detection system (WIDS)** built from $10 microcontrollers.
- **Sensor arms:** ESP32 nodes spread across a space listen to Wi-Fi management traffic and Bluetooth LE advertisements.
- **Core node:** fuses what the arms report over a LoRa mesh, detects and locates wireless attacks, and makes every decision with deterministic, auditable math.
- **Analyst assistant:** a small language model running locally on a laptop explains the findings, answers questions and turns plain-English policies into verified settings.

Why we moved away from wildfire: [ASSESSMENT_V2.md](ASSESSMENT_V2.md). How it's built: [ARCHITECTURE.md](ARCHITECTURE.md).

## 1. Problem
Shared Wi-Fi spaces are exposed to cheap, well-known wireless attacks, and nobody there can see them happening:

| Threat | What it does | How common / cheap |
|---|---|---|
| **Evil twin / rogue AP** | a fake access point copies a real network's name to intercept logins or traffic | an ESP8266 or a phone hotspot is enough |
| **Deauthentication flood** | forged deauth/disassoc frames knock clients offline, often to push them onto an evil twin | one-click in many open-source tools |
| **Beacon spam / KARMA** | hundreds of fake networks, or an AP that answers every probe request with "yes, that's me" | trivial on an ESP32 |
| **BLE tracker following** | an AirTag/Tile-style tag planted on a person or a bag | ~$30 tags |

**Places affected:** cafés, co-working spaces, university labs, small offices, clinics, homes.

**Why existing tools don't reach them:**
- **Enterprise WIPS** (Cisco, Aruba, Juniper Mist) costs thousands of dollars and needs their managed access points.
- **Kismet / nzyme** are excellent, but each sensor needs a Linux box and a monitor-mode USB adapter, and they offer no cheap way to spread sensors across a space and fuse what they see.

## 2. Users
- **Primary:** builders: security students, researchers, makers and small-site IT people who want an affordable sensor network they understand and can extend.
- **Secondary:** owners of a small site who want an alert ("someone is impersonating CafeWiFi near the back room") and a plain-language explanation.
- **Not a user:** anyone monitoring spaces or networks they don't own or aren't authorized to monitor (see the guardrails).

## 3. What EMBER v2 offers
- **Distributed sensing for about $10 a node:**
  - several sensor arms cover a whole space;
  - they're coordinated so that between them they watch all 2.4 GHz channels.
- **Detection plus location:**
  - threats are confirmed by more than one node;
  - the source is located from signal strength, with an uncertainty ellipse.
- **Offline, low-bandwidth backhaul:**
  - arms report over LoRa, so the core needs no Wi-Fi network, internet or cloud;
  - each arm's 2.4 GHz radio stays free to listen full time.
- **Deterministic decisions:**
  - every alert comes with a probability, the evidence and a reason trace;
  - the same input always gives the same decision.
- **A local analyst assistant:**
  - explains incidents and answers questions strictly from the engine's state (faithfulness is checked automatically);
  - compiles policies to configs that a verifier checks;
  - investigates unusual patterns for a human.

## 4. Guardrails (non-negotiable)
1. **Passive receive only.** Sensor arms never transmit on Wi-Fi or BLE. No deauth "countermeasures" and no attacks of any kind; active countermeasures are illegal in most jurisdictions.
2. **Metadata only.** Only 802.11 management/control frames and BLE advertisements are parsed. Data frames and payloads are dropped on the node and never stored or forwarded.
3. **Privacy:**
   - Client device MACs are hashed on the node with a salt that rotates daily, and the raw MAC is never transmitted.
   - Access point BSSIDs and SSIDs are kept, because APs broadcast them publicly in beacons.
   - The core keeps a rolling window (default 24 h) and nothing more.
4. **Authorized use only.**
   - Deploy only on premises and networks you own or have written permission to monitor.
   - Live attack tests use **your own** access points and devices only, and the test procedure is documented.
5. **The LLM never decides.**
   - Alerts come only from the deterministic engine.
   - The assistant proposes; code verifies.

## 5. Success metrics (portfolio-ready)
| Area | Metric | Target |
|---|---|---|
| Detection (simulation) | recall / false-alarm rate per threat type on held-out scenarios | recall ≥ 0.95, false alarms ≤ 0.05 per hour of normal traffic |
| Detection (live, authorized) | evil twin of your own hotspot, beacon spam from your own ESP32, a tracker you own | each detected; time from the arm's report to the alert ≤ 10 s (LoRa airtime counted separately) |
| Localization | median error across a room or flat with 3 arms | measured and reported honestly (RSSI indoors is typically 2–5 m) |
| Engine vs LLM decider | accuracy, false alarms, latency on the same eval | the engine wins on all three (repeats v1's lesson) |
| Assistant: grounded Q&A | faithfulness % (every node, hash, number in the answer exists in engine state) | fine-tuned > base, measured |
| Assistant: policy → config | verifier pass rate; simulated outcome reward | fine-tuned > base; RL > SFT |
| Portfolio | public repo, architecture write-up, 2–3 min demo video, learning log with real numbers | all published |

## 6. Non-goals
- **Not offensive.** No deauth, jamming, packet injection or credential capture. EMBER is the defensive counterpart to tools like ESP32 Marauder.
- **No 5 GHz / 6 GHz.** The ESP32-S3 radio is 2.4 GHz only, and the docs say so. Many evil-twin and deauth tools target 2.4 GHz because clients fall back to it.
- **No traffic inspection.** EMBER doesn't decrypt, look inside packets or track people's browsing.
- **No identifying people.** Phones randomize their MACs; EMBER doesn't try to defeat that.
- **No LLM in the safety path.** See guardrail 5.

## 7. Hardware
- **Already owned:**
  - 4× Seeed XIAO ESP32-S3 Sense (3 sensor arms + 1 core; the camera and mic are unused in v2);
  - an M2 MacBook (dashboard + local SLM).
- **One-time buy:** 4× **Seeed Wio-SX1262 for XIAO** LoRa modules, about $5–10 each, or a XIAO ESP32-S3 + Wio-SX1262 kit.
  - Pick the band that's legal where you are: US 902–928 MHz, EU 863–870 MHz, India 865–867 MHz.
  - Confirm on the product page that the module covers it.
- **Before the modules arrive:**
  - development continues at $0 over USB and time-sliced ESP-NOW (see the architecture doc);
  - the LoRa driver drops in later behind the same transport interface.
