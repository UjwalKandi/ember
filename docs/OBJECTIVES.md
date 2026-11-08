# EMBER v2 objectives

EMBER v2 **watches the wireless airspace of a space you own** (a home or small office) with a $10 microcontroller, and is built to teach its builder the full stack: ESP32 systems, training models from scratch, fine-tuning language models, and deploying both locally.
- **Sensor arm:** an ESP32 listens to Wi-Fi management traffic and Bluetooth LE advertisements and runs an **on-device TinyML attack classifier** over feature windows: the project's single novelty claim (§3).
- **Engine and response playbook:** on the laptop, deterministic and auditable math decides ALERT / WATCH / OK, and a fixed playbook chooses the response: alert, evidence record, recommendations. EMBER never transmits on Wi-Fi/BLE.
- **Incident-report model:** a small language model, fine-tuned and served locally, writes the incident report: what was detected, the evidence, and the response EMBER chose. It documents decisions; it never makes them. An automatic checker scores every report for faithfulness.

**Two goals, honestly ranked:**
1. **Learning and portfolio:** every model in EMBER is trained and deployed locally by the builder, with measured results ([labs/README.md](../labs/README.md)).
2. **Visibility for your own space:** impostor networks, deauth floods, beacon spam, unknown trackers, and networks missing PMF.

EMBER is not a commercial security product, and the docs don't claim more protection than it gives.

**Scope (fixed 2026-11-08, narrowed to "lean v2" the same day): a space you own, one arm over USB.** On networks you don't own EMBER could only advise, and a VPN already does more there, so they're out of scope. Multiple arms, LoRa, localization and router actions are designed but deferred ([ARCHITECTURE §9](ARCHITECTURE.md#9-deferred-designed-not-built)).

> **Set these up first: EMBER complements baseline hardening, it does not replace it.**
> 1. **WPA3 (or WPA2/3) with PMF** on your router: protected management frames make spoofed deauths ineffective.
> 2. A **separate guest network / VLAN** for visitors and IoT devices.
> 3. **Firmware updates** for the router and devices.
>
> EMBER sits on top as a visibility and monitoring layer. The dashboard flags networks without PMF and open/WEP/WPA1 networks so you can check your own.

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

**Prior art (honest):**
- **Enterprise WIPS** (Cisco, Aruba, Juniper Mist) costs thousands of dollars and needs their managed access points.
- **Kismet / nzyme** are excellent, but each sensor needs a Linux box and a monitor-mode USB adapter.
- **ESP32 tools** (e.g. Marauder's sniff/detect modes) show deauths and beacons with fixed thresholds and no learned model.
- **Machine-learning detection of 802.11 attacks** is an established research topic with public datasets (e.g. the AWID family), mostly on PC-class features.
- **Phones** (iOS and Android) already warn about unknown trackers travelling with you, and **WPA3/PMF** already blunts deauth attacks.

EMBER's contribution is therefore narrow and measurable: a learned detector **on a $10 microcontroller**, trained on its own real captures plus simulation, benchmarked honestly against thresholds, with a locally fine-tuned model that writes checked incident reports.

**Where EMBER is deployed:** your home or small office (see Scope above). The table above shows the attacks that reach those spaces too: an impostor of your SSID near your building, deauths against your clients, a tracker brought inside.

## 2. Users
- **Primary:** builders: security students, researchers, makers and small-site IT people who want an affordable sensor network they understand and can extend.
- **Secondary:** owners of a home or small office who want an alert ("someone is impersonating HomeNet") and a plain-language report of what happened and what to do.
- **Not a user:** anyone monitoring spaces or networks they don't own or aren't authorized to monitor (see the guardrails).

## 3. What EMBER v2 offers
- **The novelty: an on-device TinyML attack classifier on each $10 arm.**
  - It scores sliding windows of 802.11/BLE behavioural features (frame rates by kind, sequence-number and beacon-clock anomalies, IE-fingerprint changes; the `W` packet) as benign / deauth-flood / beacon-spam / evil-twin-suspect / tracker.
  - It generalizes beyond the hand-tuned thresholds every existing ESP32 WIDS (Marauder etc.) relies on, and it reacts where the attack is, with no backhaul.
  - Its score feeds the deterministic engine (§4 guardrail 5); it never triggers an alert on its own.
  - Everything else is solid engineering around that core, not claimed as research.
- **Detection with evidence:** deauth floods (with a spoof-suspect signal when the AP requires PMF), beacon spam, evil twins (cloned BSSID or same-SSID impostor), unknown trackers; every alert comes with a probability, the evidence and a reason trace.
- **A response you can audit:** a deterministic playbook records what EMBER did (alert, evidence record) and what you should do (e.g. enable PMF, forget the impostor network).
- **A local incident report:** a fine-tuned small model on llama.cpp turns the incident record into a readable report, scored automatically for faithfulness, coverage and invented actions.
- **Offline:** no cloud, no internet; the arm talks USB, the models run on the laptop and the chip.

## 4. Guardrails (non-negotiable)
1. **Passive receive only.** Sensor arms never transmit on Wi-Fi or BLE. No deauth "countermeasures", no jamming and no attacks of any kind, against anything, ever; RF countermeasures are illegal in most jurisdictions.
   - **The response is advisory plus record-keeping:** dashboard alert, evidence record, recommendations for you. Acting on your own router through its API is deferred; if it's ever built it touches only gear you registered, is dry-run by default, logged, reversible, and refuses unregistered targets (unit-tested).
2. **Metadata only.** Only 802.11 management/control frames and BLE advertisements are parsed. Data frames and payloads are dropped on the node and never stored or forwarded.
3. **Privacy:**
   - Client device addresses never leave the arm: BLE addresses are hashed with a salt, and Wi-Fi clients only appear as counts.
   - Access point BSSIDs and SSIDs are kept, because APs broadcast them publicly in beacons.
   - Recorded captures stay local and are git-ignored (they contain neighbours' network names).
4. **Authorized use only.**
   - Deploy only on premises and networks you own or have written permission to monitor.
   - Live attack tests use **your own** access points and devices only, and the test procedure is documented.
5. **The LLM never decides.**
   - Alerts and responses come only from the deterministic engine and playbook.
   - The report model documents what was decided; the checker rejects any report that claims an action that wasn't taken.

## 5. Success metrics (portfolio-ready)
| Area | Metric | Target |
|---|---|---|
| Detection (simulation) | recall / false-alarm rate per threat type on held-out scenarios | recall ≥ 0.95, false alarms ≤ 0.05 per hour of normal traffic |
| Detection (live, authorized) | deauths during your own router reboot, an evil twin of your own hotspot, beacon spam from your own ESP32, a tracker you own | each detected; arm report → alert ≤ 10 s |
| On-device TinyML vs thresholds | recall / false alarms / latency on held-out scenarios, look-alikes (roaming mesh APs, a neighbour's stationary tag) and real captures | a measurable gain over pure thresholds, or an honest null result; int8 C/Python parity; µs, flash and RAM reported |
| Report model: faithfulness | share of reports whose every BSSID, SSID, channel, number and time exists in the incident record | fine-tuned > base; DPO ≥ SFT |
| Report model: invented actions | share of reports claiming an action that wasn't taken | 0 on held-out incidents |
| Report model: coverage + format | required facts and sections present | fine-tuned > base |
| Report model: deployment | tok/s and score on llama.cpp, Q8_0 vs Q4_K_M | measured; quantization regression reported |
| Portfolio | public repo, architecture write-up, 2–3 min demo video, learning log with real numbers | all published |

## 6. Non-goals
- **Not offensive.** No deauth, jamming, packet injection or credential capture. EMBER is the defensive counterpart to tools like ESP32 Marauder.
- **No 5 GHz / 6 GHz.** The ESP32-S3 radio is 2.4 GHz only, and the docs say so. Many evil-twin and deauth tools target 2.4 GHz because clients fall back to it.
- **No traffic inspection.** EMBER doesn't decrypt, look inside packets or track people's browsing.
- **No identifying people.** Phones randomize their MACs; EMBER doesn't try to defeat that.
- **No LLM in the safety path.** See guardrail 5.
- **No enforcement claims.** EMBER warns, records and recommends; it doesn't block attackers.
- **No protection on networks you don't own.** There EMBER can only advise; use a VPN.
- **Deferred future work (named, not built now):** a learned (RL) active-sensing scheduler (a deterministic value-of-information scheduler is used first), an RTL-SDR wideband survey, sub-GHz receive, and RF physical-layer fingerprinting.

## 7. Hardware
- **Needed for lean v2 (already owned, $0):** one Seeed XIAO ESP32-S3 (Sense) with its antenna as the arm; the M2 MacBook for the dashboard, engine, training and llama.cpp. The other XIAOs are spares, or a second arm for comparisons.
- **Optional, free:** a Kaggle T4 for the GRPO stretch in L3 or a 7–8B teacher.
- **Deferred purchase:** Wio-SX1262 LoRa modules, only if the multi-arm / LoRa phase is ever built.
