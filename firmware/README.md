# EMBER firmware

One firmware image for the XIAO ESP32-S3 Sense. Every board runs the same image; its **role** and **node id** are settings saved in flash and set over USB serial:

- **node** (`CFG|id=<1-254>`): camera + PDM microphone, on-device flame/smoke/crackle/motion detection, sends compact ELP packets (`protocol/SPEC.md`) and answers commands. When plugged into a laptop it also **bridges itself**: it prints its own packets and everything it hears on the mesh as `RX|...` lines and accepts `TX|...`, so **a single node works with the command center without a gateway**.
- **gateway** (`CFG|role=gateway`): no sensors; bridges the mesh to the laptop over USB (`RX|src|hops|rssi|body` / `TX|body`).

Transport is ESP-NOW broadcast on channel 1 with Meshtastic-style managed flooding: ≤200 B bodies, TTL 3, a dedupe ring keyed on `(src, pkt_id)`, and 5-40 ms random relay jitter. Every node relays, so a node out of gateway range still gets through via its neighbours.

## Layout

| file | purpose |
|---|---|
| `src/main.cpp` | picks the role at boot from the stored setting |
| `src/settings.{h,cpp}` | role + node id in flash (NVS); USB console commands `CFG?`, `CFG|id=N`, `CFG|role=…`, `TEST`, `REBOOT` |
| `src/selftest.{h,cpp}` | `TEST`: chip, PSRAM, mesh, camera and mic check, ends with `LOG|selftest PASS` |
| `src/mesh.{h,cpp}` | ESP-NOW flood mesh (frame header, TTL/hops, dedupe, jittered relays, RSSI) |
| `src/sense_cam.{h,cpp}` | QQVGA RGB565 frames → flame ratio, haze (contrast loss vs slow baseline), motion |
| `src/sense_mic.{h,cpp}` | 16 kHz PDM mic → sound level and crackle transient rate |
| `src/node.cpp` | 1 Hz detection state machine (simulator-matched thresholds, debounce), heartbeats, PING/CONFIRM/SET/ALERT |
| `src/gateway.cpp` | mesh ↔ USB serial bridge |
| `src/node.cpp` USB bridge | own packets → `RX|<id>|0|0|…`, heard frames → `RX|…`, `TX|…` handled locally + flooded |
| `src/config.h` | node id default, channel, TTL, LED pin, optional battery ADC |

## Flashing

### From the browser (recommended, nothing to install)
GitHub Actions builds the firmware on every push (`.github/workflows/firmware.yml`) and publishes a flasher page:

1. Open **https://ujwalkandi.in/ember/** in **Chrome or Edge** (Safari/Firefox can't access USB).
2. Plug in the board with a USB-C data cable → **Install EMBER firmware** → pick the port → **Install**.
3. Open the **serial console** page (linked from the flasher), **Connect**, then provision:
   - node: `CFG|id=1` (unique per board, matching `config/nodes.json`)
   - gateway: `CFG|role=gateway`
   The board reboots with its new identity. `CFG?` prints the current settings.
4. Click **Self-test** (`TEST`) → expect `LOG|selftest PASS`.

The firmware image is also attached to every Actions run as the `ember-firmware` artifact (`ember-factory.bin`, flash at offset `0x0`), usable with any esptool-based flasher.

### Locally with PlatformIO (alternative)
```bash
cd firmware
pio run -e ember -t upload         # same image for every board
pio device monitor -e ember        # then type CFG|id=1 or CFG|role=gateway
```

If the upload port isn't found, hold **BOOT**, tap **RESET**, release BOOT, and retry. Choosing "erase device" during a browser install wipes the saved id; provision again.

Optional battery reporting: wire a 2×100 kΩ divider from BAT+ to a free ADC pin and add `-DBATT_ADC_PIN=A0` to the build flags. Without it, nodes leave the `b` token out of heartbeats and PONGs.

## Test procedure

1. **Gateway boot.** In the console (or `pio device monitor -e ember`) the gateway prints
   `LOG|EMBER v2-xxxxxxx role=gateway mac=.. ch=1 mesh=ok`.
2. **Heartbeats.** Power a node (USB power bank is fine). Within a few seconds the gateway prints
   `RX|1|0|-48|H|n1|s1|up2|hz0|snd12`. A node's own console shows `LOG|EMBER v2-xxxxxxx role=node id=1 ... mesh=ok cam=ok mic=ok`, followed by its own packets as `RX|1|0|0|...` lines.
3. **Commands.** Type into the gateway monitor:
   `TX|C|n1|q1|PING` → `RX|1|0|..|R|n1|q1|PONG`
   `TX|C|n1|q2|CONFIRM` → about 3 s later `R|n1|q2|CONF|OK|c0|hz..|snd..`
   `TX|C|*|q3|ALERT|lvl2` → every node's orange LED double-blinks; `TX|C|*|q4|ALERT|lvl0` clears it.
   `TX|C|n1|q5|SET|rate10|smk40|flk1` → `R|n1|q5|ACK` (several SET tokens can go in one command).
4. **Relay test.** Place node A next to the gateway and node B far enough away (another room, or a metal box) that B's packets show up only with `hops` ≥ 1, as in `RX|2|1|..|H|n2|...`. Each B packet should appear exactly **once** at the gateway, which shows dedupe is working.
5. **Flame test.** Point a node's camera at a lighter or candle 20-50 cm away (or play a fire video full-screen on a phone). Within about 2 s you should see `E|n1|s..|FLM|c..|hz..|snd..`. Remove the flame and an `OK` event follows after about 3 s. Several quick snaps near the mic (crinkling foil, snapping twigs: at least 3 pops within the 200 ms listening window) should trigger `CRK`. A single click should not. Switching a lamp on and off should trigger `MOT`; a waving hand often won't, because MOT measures frame brightness change.

6. **Edge re-programming.** The command center can retune each node's detectors over the mesh with `SET`:

   | token | effect |
   |---|---|
   | `rate<N>` | heartbeat interval, 5-600 s |
   | `smk<N>` | SMK (haze) threshold, default 25; `smk101` mutes the haze channel (faulty sensor) |
   | `flk1` / `flk0` | FLM also requires flicker across the flame-hot samples in the last 5 (glare-prone node) |
   | `rebase1` | adopt the current scene as the haze baseline, so ambient fog/dust reads ~0 |

   - **Fog test:** breathe on the lens or hold a diffuser (tracing paper) in front of it until the node sends `SMK`. Keep the diffuser in place and send `TX|C|*|q1|SET|rebase1`. The node ACKs (after a short per-node delay) and returns to `OK` within ~3 s.
   - **Glare test:** hold a phone flashlight still until `FLM` fires, then send `TX|C|n1|q2|SET|flk1`. FLM should clear and not come back while the light stays still. A flickering lighter still triggers FLM.
   - **Mute test:** `TX|C|n1|q3|SET|smk101`. The node never sends `SMK` again; `smk25` restores the default.

   Settings live in RAM, so a reboot restores the defaults.

Tuning constants sit at the top of `sense_cam.cpp` (`FLAME_GAIN`, `HAZE_GAIN`, `MOTION_GAIN`, baseline alphas), `sense_mic.cpp` (`TRANSIENT_RATIO`, `TRANSIENT_FLOOR`, the dBFS mapping in `sample()`), and `node.cpp` (`THRESHOLD`, `GLARE_FLICKER`). The default thresholds (FLM 40, SMK 25, CRK 40, MOT 40) must stay equal to `SimNetwork.classify` in `gateway/sim.py`, since that is the distribution the model was trained on. Tune the gains, not the thresholds.

## Calibration (do before the demo)

The fine-tuned model learned what "normal" looks like from the simulator: quiet-scene heartbeats carry **hz ≈ 3-10** and **snd ≈ 3-10**. Real nodes need to report in the same range.

1. **Baseline.** Put the nodes where they'll sit for the demo, keep the room quiet and the lighting steady, and watch about 2 minutes of heartbeats in the serial console. Ignore the first ~10 s, while the camera warms up and its exposure settles.
2. **Haze off?** If `hz` stays above ~10 with nothing in view, lower `HAZE_GAIN` in `sense_cam.cpp`. If it never moves when you breathe on the lens or hold paper in front of it, raise it.
3. **Sound off?** `snd = (dBFS + 60) * 2`. If a quiet room reads above ~10, change the `+ 60.0f` offset in `sense_mic.cpp` to something smaller (e.g. `+ 50.0f`). If talking at 1 m doesn't reach ~30-50, make it larger.
4. **Flame.** Hold a lighter 20-50 cm from the camera: expect `E|n..|FLM|c40+`, and a `CONFIRM` sent from the gateway monitor should come back `CONF|FLM`. If `c` stays low, raise `FLAME_GAIN`. If it never fires at all, swap the RGB565 byte order in `cam::sample()`.
5. **Glare.** Hold a phone flashlight (or a red LED) perfectly still in front of the camera. It may raise `FLM`, but `CONFIRM` should answer `CONF|OK` (or another non-flame code), because the burst sees no flicker. If it still answers FLM, raise `GLARE_FLICKER` in `node.cpp`.
6. Re-flash and repeat until a quiet scene sits in the 3-10 band and both checks behave.

## Moving to real Meshtastic / LoRa

Only `mesh.{h,cpp}` is transport-specific. To switch, replace it with a Meshtastic serial/BLE client, or an SX1262 driver that keeps the same `begin/send/loop` interface. ELP bodies already fit Meshtastic's payload limit.
