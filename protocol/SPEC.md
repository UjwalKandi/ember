# Ember Link Protocol (ELP) v1

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
type   := H | E | C | R
target := "n" <id> | "*"            (* = broadcast, only for C)
token  := key value                 (e.g. s42, c81, hz34, b3.9)
        | CODE                      (e.g. FLM, CONFIRM)
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
| `CFG|role=gateway` / `CFG|role=node` | saves the role and reboots |
| `TEST` | hardware self-test; ends with `LOG|selftest PASS` or `LOG|selftest FAIL: <parts>` |
| `REBOOT` | restarts the board |
