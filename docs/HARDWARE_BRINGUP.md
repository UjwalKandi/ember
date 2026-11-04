# Hardware bring-up log: board 1 (XIAO ESP32-S3 Sense)

Record of the single-board bring-up (Phase C1 in [SETUP_GUIDE.md](SETUP_GUIDE.md)): what was tested, what we measured, what broke, and how it was fixed. Use it to calibrate new boards and to explain the detector design to judges.

- **Date:** 2026-11-04
- **Board:** XIAO ESP32-S3 Sense (ESP32-S3 rev2, 8 MB PSRAM, OV camera, PDM mic), node id 1
- **Firmware:** v2, built by GitHub Actions and flashed from the browser (https://ujwalkandi.in/ember/)
- **Result:** ✅ **C1 gate passed.** Flash, provision, self-test, heartbeats, flame detection with lamp/flashlight rejection, and dashboard assessments over USB all work.

---

## 1. Flash + provision + self-test ✅

| Step | Observed |
|---|---|
| Browser install (Chrome, ESP Web Tools) | Install complete; no BOOT/RESET needed |
| First boot | `LOG|cfg role=node id=0` + `LOG|unprovisioned: send CFG|id=<1-254>` every 5 s |
| `CFG|id=1` | Reboot → `LOG|EMBER v2-… role=node id=1 mac=F0:9E:9E:3B:DB:64 mesh=ok cam=ok mic=ok` |
| `TEST` | `heap=213884 psram=8388608 (ok)`, `mesh=ok`, `cam=ok` (samples flame=0 haze=5-6 motion=0), `mic=ok` (level=3 crackle=0), **`LOG|selftest PASS`** |
| Heartbeats | `RX|1|0|0|H|n1|s…|up…|hz…|snd…` every 30 s; `up` increments |

## 2. Dashboard with a single board ✅
`python -m gateway.server --serial /dev/cu.usbmodem101 --nodes config/nodes-single.json --sim quiet --auto`

- n1 (real) appears alongside simulated n2–n6. Its packets show `h0 0dBm` (USB, zero hops).
- Lighter in front of the board → auto-assessment → `request_confirm(n1)` executed **on the board** → `R|n1|q8|CONF|FLM|c60` → verdict **WATCH: campfire** (correct for a single steady flame with no spread).
- `tune_detectors` / `set_sample_rate` commands reach the board and are ACKed (`R|n1|q…|ACK`).

## 3. Camera calibration: issues found and fixed

All fixes are calibratable over USB with no reflash (`CAM?`, `CAM|swap=`, `CAM|gain=`, `CAM|flick=`).

### 3a. RGB565 byte order was wrong
`CAM?` (prints both byte orders) on a normal room:

| decode | mean_rgb | brightest_rgb | verdict |
|---|---|---|---|
| big-endian (`swap=0`) | 11,34,19 (dark green) | 64,88,64 | ❌ implausible |
| little-endian (`swap=1`) | 104,64,57 | 224,224,200 | ✅ normal room |

**Fix:** default `swap=1` (XIAO frames arrive little-endian).

### 3b. Colour-only flame test fired on warm rooms
With correct colours, an ordinary warm-lit room showed 979–2011 "warm" pixels, and the lighter's core clips to white (`248,252,248`), so the old "core" rule never matched.
**Fix:** a pixel only counts if it is ≥60 luma above the frame mean. There are two pixel classes, an orange halo (R≥200, R−G≥25, G−B≥15) and a clipped core (R≥240, G≥235). The core counts only alongside ≥3 halo pixels, up to 2× the halo count.

### 3c. Lamps and flashlights look exactly like flames in colour
Labeled test, colour rule only:

| Test | orange / core px | flame | stream |
|---|---|---|---|
| A nothing (plain wall) | 9 / 0 | 5 | heartbeats only ✅ |
| B lighter 10–15 cm | 21 / 275 | 39 | FLM c41–91 ✅ |
| C phone flashlight (steady) | 26 / 255 | 48 | FLM c58–100 ❌ |
| D room light | 64 / 182 | 100 | FLM c61–100 ❌ |

Colour can't separate them, but **temporal flicker** can.
**Fix:** each 1 s sample grabs 5 quick frames and measures flicker.

**First flicker metric, (max−min)/mean: failed.** A single auto-exposure frame or a slow drift passed lamps:

| Test | flame px per frame | (max−min)/mean |
|---|---|---|
| A nothing | 59, 58, 57, **45** | 22% |
| B lighter | 49, 40, 36, 21 | 51% |
| C flashlight | 126, 132, 135, **93** | 29% |
| D lamp | 69, 78, 81, 84 (drift) | 28% |

**Final metric: median of consecutive-frame changes / mean.** It is robust to one odd frame and to drift: lighter ≈ 25%, flashlight / lamp / empty ≤ 5%. Threshold `flick=15`. Steady lights print `(steady light: suppressed)` and score 0. **Verified on the board: only the lighter produces FLM.**

Also fixed: the diagnostic buffer overflow (`CAM?` grabbed 6 frames into a 4-slot array, so the printed tail values were garbage).

### 3d. Haze jumped 0↔100 indoors
Contrast-loss haze spiked with hand movement and exposure changes, which sent the heuristic agent into "sensor fault" and "haze muted".
**Fix:** haze is smoothed (EMA 0.35) and held while motion ≥ 40, `HAZE_GAIN` drops 2.0 → 1.2, and the stuck-baseline fast adaptation kicks in after 60 s.

## 4. Calibrated defaults (firmware v2, saved in NVS)

| setting | default | console | meaning |
|---|---|---|---|
| `cam_swap` | 1 | `CAM|swap=0/1` | RGB565 byte order |
| `flame_gain` | 30 | `CAM|gain=5-200` | flame area → score (≈3.3% of frame = 100) |
| `flick` | 15 | `CAM|flick=0-100` | minimum median frame-to-frame flicker % (0 = off) |
| SMK / FLM / CRK / MOT thresholds | 25 / 40 / 40 / 40 | `SET|smk…` over the mesh | same as the simulator |

Expected `CAM?` readings on a calibrated board:
- **nothing:** flame 0
- **lighter at 10–15 cm:** flicker ~20–40%, flame ≥ 40
- **lamp / flashlight:** flicker ≤ 5%, `(steady light: suppressed)`

## 5. Dashboard / tooling issues found and fixed
| Issue | Fix |
|---|---|
| Board kept agent tuning (`haze muted`, `flicker-check`) across dashboard runs | Dashboard sends `C|*|SET|smk25|flk0` to real boards at start and on every scenario load; console **Reset tuning** button |
| No way to tell a node was live | White ring + **LIVE · seen Ns** / red **OFFLINE** on real nodes; header pill `hardware: n1 3s` |
| Long tool-call text squeezed the mesh map | Fixed grid proportions (`minmax(0, …)`); long text wraps inside panels |
| Map drew a duplicate wind arrow after a resize | Canvas re-fits its backing store when the panel size changes |
| `Resource busy` traceback when the console tab held the port | Plain-English error; console **Disconnect** button |
| Clicking the map started simulated fires during hardware tests | Documented ("click map = ignite (sim)"); don't click during hardware tests |

## 6. Next: Phase C2 (replication)
Same image for every board: **Install** → console `CFG|id=2` (3, …) → **Self-test** PASS → **Camera check** (nothing / lighter / lamp). Gateway board: `CFG|role=gateway`. Then run the radio tests (heartbeats over the air with real RSSI, `TX|C|n2|q9|PING` → PONG) and the relay test (hops = 1). Steps are in [SETUP_GUIDE.md](SETUP_GUIDE.md) → C2.
