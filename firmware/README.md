# EMBER firmware

One PlatformIO project for the XIAO ESP32-S3 Sense with two roles:

- **node**: camera + PDM microphone, on-device flame/smoke/crackle/motion detection, sends compact ELP packets (`protocol/SPEC.md`) and answers commands.
- **gateway**: plugged into the laptop over USB; bridges the mesh to serial (`RX|src|hops|rssi|body` / `TX|body`).

Transport is ESP-NOW broadcast on channel 1 with Meshtastic-style managed flooding: ≤200 B bodies, TTL 3, a dedupe ring keyed on `(src, pkt_id)`, and 5-40 ms random relay jitter. Every node relays, so a node out of gateway range still gets through via its neighbours.

## Layout

| file | purpose |
|---|---|
| `src/main.cpp` | picks the role at compile time |
| `src/mesh.{h,cpp}` | ESP-NOW flood mesh (frame header, TTL/hops, dedupe, jittered relays, RSSI) |
| `src/sense_cam.{h,cpp}` | QQVGA RGB565 frames → flame ratio, haze (contrast loss vs slow baseline), motion |
| `src/sense_mic.{h,cpp}` | 16 kHz PDM mic → sound level and crackle transient rate |
| `src/node.cpp` | 1 Hz detection state machine with hysteresis, heartbeats, PING/CONFIRM/SET/ALERT |
| `src/gateway.cpp` | mesh ↔ USB serial bridge |
| `src/config.h` | node id default, channel, TTL, LED pin, optional battery ADC |

## Flashing

Requires PlatformIO (`pipx install platformio` or the VS Code extension). The project uses the pioarduino platform (Arduino-ESP32 3.x); the first build downloads it.

```bash
cd firmware

# gateway (one board, stays plugged into the laptop)
pio run -e gateway -t upload

# nodes: give every board a unique id 1-254
PLATFORMIO_BUILD_FLAGS=-DNODE_ID=1 pio run -e node -t upload
PLATFORMIO_BUILD_FLAGS=-DNODE_ID=2 pio run -e node -t upload
PLATFORMIO_BUILD_FLAGS=-DNODE_ID=3 pio run -e node -t upload
```

If the upload port isn't found, hold **BOOT**, tap **RESET**, release BOOT, and retry. Add `--upload-port /dev/cu.usbmodemXXXX` when several boards are connected.

Node ids must match `gateway/nodes.json` so the command center knows where each node sits.

Optional battery reporting: wire a 2×100 kΩ divider from BAT+ to a free ADC pin and add `-DBATT_ADC_PIN=A0` to the build flags. Without it, nodes report `b0.0`.

## Test procedure

1. **Gateway boot.** `pio device monitor -e gateway` should print
   `LOG|EMBER gateway v1 mac=.. ch=1 mesh=ok`.
2. **Heartbeats.** Power a node (USB power bank is fine). Within a few seconds the gateway prints
   `RX|1|0|-48|H|n1|s1|b0.0|up2|hz0|snd12`. A node's own monitor shows `EMBER node n1 mesh=1 cam=1 mic=1`, followed by its `TX ...` lines.
3. **Commands.** Type into the gateway monitor:
   `TX|C|n1|q1|PING` → `RX|1|0|..|R|n1|q1|PONG|b0.0`
   `TX|C|n1|q2|CONFIRM` → about 3 s later `R|n1|q2|CONF|OK|c..|hz..|snd..`
   `TX|C|*|q3|ALERT|lvl2` → every node's orange LED double-blinks; `TX|C|*|q4|ALERT|lvl0` clears it.
4. **Relay test.** Place node A next to the gateway and node B far enough away (another room, or a metal box) that B's packets show up only with `hops` ≥ 1, as in `RX|2|1|..|H|n2|...`. Each B packet should appear exactly **once** at the gateway, which shows dedupe is working.
5. **Flame test.** Point a node's camera at a lighter or candle 20-50 cm away (or play a fire video full-screen on a phone). Within about 2 s you should see `E|n1|s..|FLM|c..|hz..|snd..`. Remove the flame and an `OK` event follows after about 3 s. Crinkling foil or snapping twigs near the mic should trigger `CRK`. Waving a hand or switching a lamp should trigger `MOT`.

Tuning constants sit at the top of `sense_cam.cpp` (`FLAME_GAIN`, `HAZE_GAIN`, `MOTION_GAIN`), `sense_mic.cpp` (`TRANSIENT_RATIO`, `TRANSIENT_FLOOR`), and `node.cpp` (`ENTER` thresholds, `HYSTERESIS`).

## Moving to real Meshtastic / LoRa

Only `mesh.{h,cpp}` is transport-specific. To switch, replace it with a Meshtastic serial/BLE client, or an SX1262 driver that keeps the same `begin/send/loop` interface. ELP bodies already fit Meshtastic's payload limit.
