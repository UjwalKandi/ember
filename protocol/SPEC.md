# Ember Link Protocol (ELP)

v1 (§1–3) is the wildfire node protocol. v2 (§4) adds the passive RF **arm** packets; both share the body grammar, the 200 B limit and the USB serial bridge, so one parser and one bridge read both.

ELP is the language spoken by every part of EMBER: the ESP32 firmware, the gateway, the simulator, and the fine-tuned Muse Glimmer model. It is designed under **Meshtastic-class constraints** so the transport can be swapped from ESP-NOW to LoRa without touching anything above it:

- payload ≤ **200 bytes** of printable ASCII
- managed flooding, hop limit **3**, duplicate suppression by `(src, pkt_id)`
- low duty cycle: nodes send heartbeats every 30 s and events only on change

Packets are pipe-delimited so the model can read raw packets directly.

## 1. Over-the-air frame (ESP-NOW, broadcast)

| byte | field    | notes                                      |
|------|----------|--------------------------------------------|
| 0    | magic    | `0xE7`                                     |
| 1    | version  | `1`                                        |
| 2-3  | pkt_id   | little-endian u16, per-source counter      |
| 4    | src      | originating node id (1-254, 0 = gateway)   |
| 5    | ttl      | starts at 3, decremented on each relay     |
| 6    | hops     | starts at 0, incremented on each relay     |
| 7    | len      | body length (≤ 200)                        |
| 8..  | body     | ASCII ELP body (below)                     |

A node relays a frame if `ttl > 0` and `(src, pkt_id)` is not in its 32-entry dedupe ring. Relays are delayed by a random 5-40 ms to reduce collisions (Meshtastic-style SNR-less jitter).

## 2. Body grammar

```
body   := type "|" target "|" tokens
type   := H | E | C | R            (v2 arms add A | B | W, §4)
target := "n" <id> | "*"            (* = broadcast, only for C)
token  := key value                 (e.g. s42, c81, hz34, b3.9)
        | CODE                      (e.g. FLM, CONFIRM)
        | key ":" text              (v2 only, e.g. b:a1b2c3d4e5f6, ss:Home%20Net; §4)
```

### H: heartbeat (node → gateway, every 30 s)
`H|n3|s41|b3.9|up812|hz2|snd3`

| key | meaning |
|---|---|
| s   | sequence number |
| b   | battery volts |
| up  | uptime seconds |
| hz  | haze index 0-100 (max over the last interval) |
| snd | sound level 0-100 (max over the last interval) |

### E: event (node → gateway, on detection or change)
`E|n3|s42|FLM|c81|hz34|snd12`

| code | meaning |
|---|---|
| FLM | flame-colored region detected by camera |
| SMK | smoke / haze increase detected by camera |
| CRK | crackling transients detected by microphone |
| MOT | large motion / light change (vehicles, animals, headlights) |
| OK  | node returned to baseline |

`c` = detector confidence 0-100, `hz` = haze 0-100, `snd` = sound 0-100.

### C: command (gateway → node)
`C|n3|q7|PING`, `C|n3|q7|CONFIRM`, `C|n3|q7|SET|rate5`, `C|*|q9|SET|rebase1`, `C|n3|q7|SET|smk40|flk1`, `C|*|q8|ALERT|lvl2`

| command | effect |
|---|---|
| PING    | node replies `PONG` with battery |
| CONFIRM | node re-samples camera + mic for ~3 s and reports the result |
| SET     | edge re-programming, any combination: `rate<N>` heartbeat interval s; `smk<N>` SMK haze threshold (default 25, `101` = haze muted); `flk<0/1>` FLM requires flicker across the flame-hot samples in the last 5 (glare suppression); `rebase1` adopt current scene as haze baseline (ambient fog/dust → ~0). RAM only |
| ALERT   | `lvl0-3`: node shows alert (LED pattern); broadcast to all |

`q` = query id chosen by the gateway; responses echo it.

### R: response (node → gateway)
`R|n3|q7|PONG|b3.9`, `R|n3|q7|CONF|FLM|c88|hz40|snd20`, `R|n3|q7|ACK`

## 3. Board ↔ laptop (USB serial, 115200, newline-terminated)

Any board plugged into the laptop speaks this, whether it's a gateway or a node.

- board → laptop: `RX|<src>|<hops>|<rssi>|<body>` for every frame received from the mesh. A **node** also prints its own packets as `RX|<own id>|0|0|<body>`, so a single node works as its own gateway.
- laptop → board: `TX|<body>`. A gateway floods it as src 0. A node handles it locally if it is addressed to itself or `*`, and floods it otherwise.
- board → laptop: `LOG|<text>` for diagnostics (ignored by the parser)

Console commands (every role; one image for every board, settings stored in flash):

| line | effect |
|---|---|
| `CFG?` | prints `LOG|cfg role=<node/gateway> id=<n> fw=<version>` |
| `CFG|id=<1-254>` | saves the node id and reboots (a node with id 0 stays idle until provisioned) |
| `CFG|role=arm` / `CFG|role=gateway` / `CFG|role=node` | saves the role and reboots (fresh boards start as `arm`) |
| `TEST` | hardware self-test; ends with `LOG|selftest PASS` or `LOG|selftest FAIL: <parts>` |
| `REBOOT` | restarts the board |
| `CAM?` | camera diagnostics: colour stats and flame pixels for both RGB565 byte orders |
| `CAM|swap=<0/1>` / `CAM|gain=<5-200>` / `CAM|flick=<0-100>` | camera byte order / flame sensitivity / minimum flicker % (steady lights like lamps are not flame), saved in flash |

## 4. ELP v2: RF sensor arm packets

An **arm** (`CFG|role=arm`) listens only: Wi-Fi management frames in promiscuous mode (data frames are filtered out by the radio and never reach the firmware) and passive BLE advertisement scanning (no scan requests). It hops channels on a dwell plan (default `1..13 @ 300 ms`, a 3.9 s cycle). In R1 the transport is USB: packets are printed as `RX|<id>|0|0|<body>`, exactly like a v1 node bridging itself.

**String tokens.** v2 adds `key:text`. In the text, space, `|`, `%` and every byte outside printable ASCII are written as `%XX` (uppercase hex), so any SSID (raw bytes, up to 32) fits in one token and survives the round trip. The worst case is 96 characters.

### W: feature window (one per channel dwell; USB only)
`W|n1|s46|ch6|dt300|bcn12|prq3|prs4|ath0|asc0|dea0|dis0|act0|bc0|spf0|nb5|nn0|nss4|ds0|pd2|sqa0|tsj0|iec0|rx-41|ra-67`

The field order is fixed. It is the input vector of the L2 on-device classifier and `gateway/airspace.py: W_FIELDS`, and a test checks that the firmware's format string matches it.

| key | meaning |
|---|---|
| ch, dt | channel, actual dwell length in ms |
| bcn prq prs ath asc dea dis act | frames by kind: beacon, probe request, probe response, auth, (re)assoc request, deauth, disassoc, action. Counted in the capture callback, so these stay exact under a flood |
| bc | deauth/disassoc addressed to broadcast |
| spf | unprotected deauth/disassoc claiming to come from an AP that **requires** PMF (a genuine one would be protected): spoof-suspect |
| nb, nss | distinct BSSIDs beaconing, distinct SSIDs |
| nn | BSSIDs never seen before (AP-table misses) |
| ds, pd | distinct deauth senders, distinct probing devices |
| sqa | sequence-number anomalies on a known AP's frames within a dwell (repeat without retry, or a jump > 512): a second transmitter using its address |
| tsj | a known AP's beacon timestamp (TSF) went backwards within a dwell: two radios, one BSSID |
| iec | a known AP's beacon IE fingerprint changed |
| rx, ra | strongest and mean RSSI (dBm), `-127` if the window was empty |

### A: access point digest (on first sight, on any change, and every 60 s while seen)
`A|n1|s41|b:a1b2c3d4e5f6|ss:Home%20Net|ch6|sec:W2|pmf1|r-61|bi100|ie:9f3a` (+ `|hid1` for a hidden SSID, sent with an empty `ss:`)

| key | meaning |
|---|---|
| b | BSSID, 12 lowercase hex |
| ss | SSID (escaped) |
| ch | channel from the DS Parameter Set |
| sec | `OPN` open, `WEP`, `W1` WPA1, `W2` WPA2-PSK, `W3` WPA3-SAE, `W23` WPA2/3 transition, `ENT` 802.1X, `OWE` Enhanced Open |
| pmf | protected management frames: 0 none, 1 capable, 2 required |
| r, bi | RSSI dBm, beacon interval (TU) |
| ie | 16-bit fingerprint of the ordered IE ids (+ vendor OUI/type), TIM excluded |

### E: arm event
`E|n1|s43|DEA|ch6|b:a1b2c3d4e5f6|cnt20|rate66|bc20|spf20|r-40` and `E|n1|s44|BSP|ch1|nn12|nb14`

| code | when (R1 placeholder thresholds; replaced by the engine's CUSUM in L1 and the classifier in L2) |
|---|---|
| DEA | ≥ 10 deauth+disassoc frames in one dwell. `b` = BSSID of the last one, `cnt` frames, `rate` per second |
| BSP | ≥ 8 never-seen BSSIDs in one dwell, after the first two hop cycles |

Each code is held off for 5 s after it fires.

### B: BLE tracker (first sight, then every 30 s while seen)
`B|n1|s45|h:7c19aa02|trk1|mf:004C|rnd1|r-70`

`h` = trunc(SHA-256(salt ‖ address), 4 B), so raw addresses never leave the arm. The salt is random per boot (a shared daily salt belongs to the deferred multi-arm design). `trk`: 1 Apple Find My (offline-finding payload), 2 Tile, 3 Samsung SmartTag, 4 Google Find My Device network. `mf` = company id when manufacturer data is present; `rnd1` = random address.

### H: arm heartbeat (every 30 s)
`H|n1|s40|up3600|ap23|ble14|trk1|drp0|occ:1=34,6=71`

`ap` AP-table size, `ble`/`trk` distinct advertisers / trackers since the last heartbeat, `drp` frames dropped from the feature queue since boot (counts stay exact), `occ` management frames/s per channel on its last dwell (zeros omitted). An `H` with an `ap` key comes from an arm; one without comes from a v1 node.

### Arm console lines
| line | effect |
|---|---|
| `PLAN?` | prints `LOG|plan 1,2,...,13@300 (cycle 3900 ms)` |
| `PLAN|1,6,11@500` | channels 1–13 in order, dwell 100–5000 ms (RAM only) |
| `TEST` | `LOG|selftest wifi=ok frames=… ble=ok …` then `PASS` / `FAIL` |
