# EMBER setup & run guide

A step-by-step runbook for taking EMBER from a fresh clone to a live demo. Use it to rebuild the project on another machine, or as a template for a similar "LLM agent + sensor mesh" project (see [Reusing this blueprint](#reusing-this-blueprint-for-a-new-project)).

Every phase ends with a ✅ checklist. Don't move on until it passes.

| Phase | What | Time | Can run in parallel with |
|---|---|---|---|
| [A](#phase-a-laptop-setup) | Laptop setup | ~20 min | — |
| [B](#phase-b-accounts-secrets-model-download) | Accounts, secrets, 60 GB model download | ~15 min + download | C |
| [C](#phase-c-hardware) | Browser-flash + bring up ONE board, then replicate | ~1.5–2 h | B, D |
| [D](#phase-d-training) | Fine-tune Muse Glimmer on Modal | ~2–3 h (mostly waiting) | C |
| [E](#phase-e-serve--evaluate) | Serve + evaluate base vs fine-tuned | ~1 h | — |
| [F](#phase-f-demo-prep) | Demo rehearsal | ~1–2 h | — |
| [G](#phase-g-submission) | Publish + submit | ~30 min | — |

**Run every command from the repo root** (`cd ~/Documents/meta/ember`, or wherever you cloned it) in your own Terminal window.

---

## Phase A: Laptop setup

**Needs:** macOS or Linux with `python3` ≥ 3.9 and `git`. The gateway, simulator and eval use only the standard library.

### A1. Open the project
```bash
cd ~/Documents/meta/ember        # new device: git clone https://github.com/<you>/ember && cd ember
ls
```
✅ You see `README.md config docs firmware gateway protocol results tests training`.

### A2. Install the tools (use Python 3.12 in a project venv)
The macOS system Python (3.9) is too old for current Modal releases: pip stops at modal 1.2.6, which fails with `supports the following image builder versions ... You are using '2026.10'`. Use Python 3.12 in a project venv:
```bash
brew install python@3.12            # or the macOS installer from python.org/downloads
python3.12 -m venv .venv
source .venv/bin/activate           # do this in every new Terminal window (prompt shows "(.venv)")
pip install --upgrade pip
pip install modal pyserial platformio
modal --version
pio --version
```
✅ Both print a version, and modal is newer than 1.2.6. `.venv/` is git-ignored. On macOS, `pip` alone may be "command not found" outside the venv; use `python3 -m pip`.
- Can't upgrade right now? Fallback: Modal dashboard → **Settings → Image Config** → image builder version `2025.06`.
- **Still the same error inside `(.venv)`?** Check the traceback paths: if they show `Library/Python/3.9`, an old global modal is shadowing the venv. Fix:
  ```bash
  deactivate; python3 -m pip uninstall -y modal
  sed -i '' '/Library\/Python\/3.9\/bin/d' ~/.zshrc     # drop an old PATH line, if you added one
  rm -rf .venv && python3.12 -m venv .venv && source .venv/bin/activate
  python --version                                     # must be 3.12.x
  python -m pip install modal pyserial platformio
  ```
  Then prefer `python -m modal ...` over a bare `modal ...`, so the venv's copy is always used.

- A `NotOpenSSLWarning ... LibreSSL` message is harmless (macOS system Python). To hide it:
  `echo 'export PYTHONWARNINGS="ignore::Warning:urllib3"' >> ~/.zshrc && source ~/.zshrc`
- `command not found`: run `python3 -m site --user-base` and add `<that path>/bin` to `PATH`.
- `No matching distribution found for modal`: install a newer Python (`brew install python@3.12`) and use `python3.12 -m pip ...`.

### A3. Git identity (so GitHub credits you)
```bash
git config user.name "Your Name"
git config user.email "your-github-email"     # or the 123+you@users.noreply.github.com from GitHub → Settings → Emails
git config user.email                         # must print the email; if empty, the author becomes you@<hostname>.local
```
If commits already exist and haven't been pushed, rewrite their author:
```bash
git rebase -r --root --exec "git commit --amend --no-edit --reset-author"
git log --format='%an <%ae>' | sort -u
```
✅ Exactly one line with your GitHub email.

### A4. Tests
```bash
python3 -m unittest discover -s tests
```
✅ `Ran 24 tests ... OK` (the count grows as tests are added).

### A5. Dashboard in simulation
```bash
python3 -m gateway.server --sim wildfire --auto --show-truth
```
Open **http://localhost:8080**.
- **Map (left):** coloured node dots and a wind arrow. Within ~30 s an orange glow (the simulated fire) appears and nearby nodes turn red or grey.
- **Packet feed (bottom right):** lines like `E|n3|s12|FLM|c81|...`.
- **Header:** an `airtime x / 36s per hour` pill.
- **Trace (top right):** ~40 s after ignition an auto-assessment runs (`→ request_confirm(...)`), followed by an **ALERT** report.
- Choose **fog** → **Load scenario** → wait 60 s → **Assess now**. The rule-based baseline wrongly says ALERT; that's the weakness the fine-tuned model fixes.
- Click the map to start a fire anywhere. Press **Ctrl+C** in Terminal to stop.

### ✅ Phase A checklist
- [ ] `modal` and `pio` print versions
- [ ] git author is your GitHub email
- [ ] tests OK
- [ ] dashboard loads and shows an ALERT for the wildfire scenario

---

## Phase B: Accounts, secrets, model download

### B1. Hugging Face access
1. Sign in at https://huggingface.co and open https://huggingface.co/meta-models/Muse-Glimmer-30B.
2. If there's a license or access form, accept it and wait for approval (the page will say you have access). As of 2026-11-03 the model is **Apache-2.0 and not gated**: there's no form, and you only need a token.
3. Create a token: profile picture → **Settings → Access Tokens → Create new token**, type **Read**, name `ember`. Copy it (starts with `hf_`).

### B2. Modal account
1. Sign up at https://modal.com and add a payment method in **Settings → Billing**. GPU time (H100) is billed per second; check current pricing and your credits.
2. Connect the CLI; this opens a browser tab to approve:
   ```bash
   modal setup
   ```
   ✅ Terminal says the token was verified and saved.

### B3. Create the two secrets
```bash
modal secret create huggingface HF_TOKEN=hf_paste_your_token_here
export EMBER_LLM_KEY=$(openssl rand -hex 16)
modal secret create ember-llm EMBER_LLM_KEY=$EMBER_LLM_KEY
echo $EMBER_LLM_KEY        # save this somewhere safe (password manager); needed in Phases E/F
```
✅ `modal secret list` shows `huggingface` and `ember-llm`.

### B4. Start the model download (~60 GB, the longest step; start it early)
```bash
modal run training/modal_app.py::download
```
- Runs on Modal's servers, not your laptop. Keep the Terminal open until it finishes (or add `--detach` after `run` to let it continue if you close the terminal).
- ✅ Ends with `weights at /root/.cache/huggingface/hub/models--meta-models--Muse-Glimmer-30B/snapshots/...`
- Check the volume: `modal volume ls ember-hf-cache hub`
- `401` / `403` / `GatedRepoError`: your HF access isn't approved yet, or the secret holds the wrong token (re-run `modal secret create huggingface ... --force`).
- `Revision not found`: the pinned revision in `training/modal_app.py` (`BASE_REVISION`) changed upstream. Update it to the one in the cookbook's Axolotl configs.

### ✅ Phase B checklist
- [ ] HF access approved, token created
- [ ] `modal setup` done, both secrets exist
- [ ] download finished (or running) without auth errors

---

## Phase C: Hardware

**Approach:** no Arduino IDE and nothing to install. GitHub Actions compiles the firmware (`.github/workflows/firmware.yml`), and you flash it from the browser. Every board runs the **same image**, and you give each one its identity over USB afterwards. **Bring up ONE board completely first (C1), and only then replicate (C2).**

**Needs:**
- XIAO ESP32-S3 Sense boards
- USB-C **data** cables (charge-only cables don't show up as a port)
- **Chrome or Edge** (Safari and Firefox can't access USB)
- later, a USB power bank per field node

**Links:**
- Flasher: **https://ujwalkandi.in/ember/** (always use `https://`)
- Serial console: **https://ujwalkandi.in/ember/console.html**
- Firmware build status: https://github.com/UjwalKandi/ember/actions (must be green ✅)

### C1. Single-board bring-up (board #1)

**Step 1: Assemble one board.** Clip the Sense board (camera + mic) onto the XIAO and attach the U.FL antenna (press straight down until it snaps). Label it `1`.

**Step 2: Flash it.**
1. Plug the board into the laptop.
2. Open the flasher page in Chrome/Edge → **Install EMBER firmware**.
3. Pick the port (`USB JTAG/serial debug unit` or `cu.usbmodem…`) → **Install**. Erasing is fine on the first install.
4. ✅ "Installation complete".

If the port is missing or it can't connect: try another cable or USB port. Or hold **B** (BOOT), tap **R** (RESET), release B, and click Install again.

**Step 3: Open the console.**
1. Open the console page → **Connect** → pick the same port.
2. ✅ You see `LOG|cfg role=node id=0 fw=v2-…` and, every 5 s, `LOG|unprovisioned: send CFG|id=<1-254>`.

**Step 4: Give it an id.** Type `1` in the id box → **Set node id** (sends `CFG|id=1`). The board reboots.
1. Click **Connect** again if the console disconnected.
2. ✅ `LOG|EMBER v2-… role=node id=1 mac=… mesh=ok cam=ok mic=ok`

**Step 5: Self-test.** Click **Self-test**.
- ✅ `psram=… (ok)`, `mesh=ok`, `cam=ok` with 4 sample lines, `mic=ok` with a level line, then a green `LOG|selftest PASS`.
- ❌ `cam=FAIL`: the Sense board isn't seated. Unplug, re-clip it firmly, and retry.
- ❌ `psram=0`: tell me. The PSRAM setting in `platformio.ini` would need changing.

**Step 6: Heartbeats.** Wait 30 s.
- ✅ Lines like `RX|1|0|0|H|n1|s2|up35|hz5|snd4`. Read it as: own packet (0 hops), haze 5, sound 4.

**Step 7: Detector checks** (watch the console). Click **Reset tuning** first. The dashboard's agent can leave a board with "haze muted" / "flicker-check" from earlier tests; the dashboard also resets this automatically whenever it starts or loads a scenario.
| # | Do | Expect |
|---|---|---|
| a | Leave it still in a quiet room for 2 min | `hz` and `snd` in the `H|` lines stay around **3–10**. Note the typical values |
| b | Hold a lighter flame **10–15 cm** from the camera so it fills a visible part of the image (a fire video full-screen on a phone also works) | `RX|1|0|0|E|n1|…|FLM|c40…` (yellow line). Still no FLM? See **Camera calibration** below |
| c | Hold a phone flashlight **steady** at the camera (and point it at a room lamp) | **No FLM.** `Camera check` shows `(steady light: suppressed)`, flicker ≤5% |
| d | Lighter again → **Re-sample (CONFIRM)** | `CONF|FLM|c40+` (real flames flicker, so they pass) |
| e | Breathe on the lens / cover it with tissue until `SMK` appears → **Rebase haze** | `R|n1|…|ACK`, then the node returns to `OK` |
| f | **Ping self** | `R|n1|q…|PONG` |

**Camera calibration (only if a flame never shows FLM):**
1. Point the camera at a plain scene and click **Camera check** (`CAM?`). Then hold the flame 10–15 cm away and click it again.
2. Each run prints two lines, `swap=0` and `swap=1`, with `mean_rgb`, `orange_px` / `core_px` (flame pixels that stand out from the scene) and the resulting `flame=` score. The one marked `<- in use` is active.
   - **Right byte order:** `mean_rgb` of a normal room looks plausible (e.g. `104,64,57`), not dark green (`11,34,19`). The XIAO default is `swap=1`. Change it with `CAM|swap=0/1` only if the in-use line looks wrong.
   - **Plain scene:** `flame` should be ~0. **Flame 10–15 cm away:** `orange_px` > 3 and `flame` ≥ 40. If orange pixels appear but `flame` stays under 40, raise sensitivity: `CAM|gain=60` (default 30; 5–200).
   - **Lamps and flashlights** have the same clipped-white core and warm fringe as a flame, so colour alone can't separate them. The board also measures **flicker**: the median frame-to-frame change of the flame area across quick frames (`flame_px/frame=…`, `flicker=…%`). Measured: lighter ~25%, flashlight/lamp/empty scene ≤5%. A steady light prints `(steady light: suppressed)` and scores 0; a real flame flickers above the `flick` threshold (default 15%).
   - Lamp still triggers? Raise the threshold: `CAM|flick=25`. Real flame suppressed? Lower it: `CAM|flick=10`. `CAM|flick=0` turns the test off.
   - Settings are saved on the board and survive reboots. `CFG?` shows `cam_swap` and `flame_gain`.
3. Repeat test 7b.

**How to tell a node is live:**
- Console: an `RX|1|0|0|H|n1|…` heartbeat every 30 s; `up` (uptime in seconds) keeps increasing.
- Dashboard: real boards have a white ring and a green **LIVE · seen Ns** tag (red **OFFLINE** after 90 s of silence), and the header shows `hardware: n1 3s`. In the traffic feed, a real board connected over USB shows `h0 0dBm`; over the radio it shows real RSSI such as `-55dBm`.

**Step 8: Command center with the single board.**
1. **Close the console tab** (only one program can hold the port).
2. Find the port and start the dashboard:
   ```bash
   ls /dev/cu.usbmodem*
   python -m gateway.server --serial /dev/cu.usbmodemXXXX --nodes config/nodes-single.json --sim quiet --auto
   ```
3. Open http://localhost:8080:
   - ✅ **n1** is your real board; n2–n6 are simulated.
   - ✅ Hold the lighter in front of the board. n1 turns red and an assessment starts by itself.
   - ✅ The trace shows `→ request_confirm({"node_id": 1})` and a reply `R|n1|…|CONF|FLM…`. That CONFIRM was executed by your board.
4. Stop with **Ctrl+C**.

**✅ C1 success gate:** all of steps 4–8 pass. Send the results (especially the 7a numbers). Fix anything that fails **before** flashing more boards.

> **Status 2026-11-04:** board 1 passed C1. Measurements, issues and fixes are in **[HARDWARE_BRINGUP.md](HARDWARE_BRINGUP.md)**. Reference `Camera check` readings for a good board: nothing → flame 0; lighter at 10–15 cm → flicker ~20–40%, flame ≥ 40; lamp or flashlight → flicker ≤ 5%, `(steady light: suppressed)`.

### C2. Replicate (only after C1 passes)

1. **More nodes:** for each new board, assemble → **Install** → console → `CFG|id=2`, then `3`, … → **Self-test** PASS. Label each board with its id.
2. **Gateway (optional with a single laptop-connected node):** flash one more board the same way and click **Make gateway** (`CFG|role=gateway`). ✅ `LOG|EMBER v2-… role=gateway … mesh=ok`. Its self-test only requires `mesh=ok`.
3. **Radio test:** keep the gateway (or node 1) on USB in the console, and power the other nodes from power banks.
   - ✅ `RX|2|0|-5x|H|n2|…` and `RX|3|0|…` lines arrive over the air, with real RSSI values.
   - Type `TX|C|n2|q9|PING` → ✅ `R|n2|q9|PONG` comes back over the radio.
4. **Relay test:** carry node 3 away until its lines stop, then place node 2 halfway. ✅ Node 3 comes back with hops 1: `RX|3|1|…`.
5. **Layout:** measure the demo positions in metres and edit `x`/`y` for the real ids in `config/nodes.json`.
6. **Live test:**
   ```bash
   python -m gateway.server --serial /dev/cu.usbmodemXXXX --nodes config/nodes.json --sim quiet --auto
   ```
   Use the port of whichever board is on USB, gateway or node.

### ✅ Phase C checklist
- [ ] C1 gate passed on board 1 (self-test PASS, detectors behave, dashboard runs an assessment with the real board)
- [ ] remaining boards flashed and provisioned with unique ids
- [ ] radio + relay tests pass
- [ ] `config/nodes.json` positions updated

## Phase D: Training

### D1. Build the training data (local; `data/` is not in git)
```bash
python3 -m training.gen_data --out data
```
✅ `train=2280 val=120 eval=420` plus a class/budget breakdown. Re-run this whenever `gateway/` logic changes; the Modal image packs whatever is in `data/`.

### D2. Smoke run (~15–30 min incl. first image build)
```bash
modal run training/modal_app.py::train --smoke
```
Read the output and check **all four**:
1. **Rendered example:** tool definitions appear near the top, and each tool call sits in its **own** assistant message.
2. `tokens/example p50=… p95=… max=…`: **max must be < 8192**.
3. `ELP vs JSON telemetry, Muse Glimmer tokens: … (N.NNx fewer with ELP)`: copy N.NN into the README "What's new" table.
4. `=== K trained spans ===`: roughly one span per assistant message, including the tool-call ones (not just the final report).

If any check fails or the run crashes, save the full log; the fix goes in `_ember_config` / `gen_data.py` before the full run.

### D3. Full run (~1–2 h on 1×H100)
```bash
modal run --detach training/modal_app.py::train
```
Watch progress at https://modal.com/apps (app `ember` → `train` → logs). Loss should fall steadily; eval loss runs at epoch end.

### D4. Merge the adapter into the base model
```bash
modal run training/modal_app.py::merge
```
✅ Ends with `merged checkpoint at /runs/ember-qlora/merged`.

### ✅ Phase D checklist
- [ ] smoke checks 1–4 pass
- [ ] full training finished
- [ ] merge finished

---

## Phase E: Serve & evaluate

### E1. Deploy the endpoints
```bash
modal deploy training/modal_app.py
```
Note the two URLs it prints and add `/v1`:
- `https://<workspace>--ember-serve-base.modal.run/v1` (stock Muse Glimmer, model name `muse-glimmer`)
- `https://<workspace>--ember-serve-ember.modal.run/v1` (fine-tuned, model name `ember`)

### E2. Warm them up (the first call loads ~60 GB; can take 5–15 min)
```bash
export EMBER_LLM_KEY=<the key from B3>
curl -s -H "Authorization: Bearer $EMBER_LLM_KEY" https://<base-url>/v1/models
curl -s -H "Authorization: Bearer $EMBER_LLM_KEY" https://<ember-url>/v1/models
```
✅ JSON listing `muse-glimmer` / `ember`. Containers shut down after 20 idle minutes.

### E3. Quick check (1 episode per scenario)
```bash
python3 -m training.eval --agent llm --url https://<ember-url>/v1 --model ember --key $EMBER_LLM_KEY --limit 1 --name ember-quick
```
✅ Prints metrics with `valid_assessment` near 1.0. If it's 0, check `results/ember-quick.json` → `rows[].error`.

### E4. Full evaluation
```bash
python3 -m training.eval --agent llm --url https://<base-url>/v1  --model muse-glimmer --key $EMBER_LLM_KEY --name base
python3 -m training.eval --agent llm --url https://<ember-url>/v1 --model ember        --key $EMBER_LLM_KEY --name ember
python3 -m training.eval --report results/heuristic.json results/base.json results/ember.json results/oracle.json
```
Paste the table into the README "Results" section (base + EMBER columns). Keep the numbers honest, even where EMBER loses.

### ✅ Phase E checklist
- [ ] both endpoints answer
- [ ] base + ember evals saved in `results/`
- [ ] README table filled in

---

## Phase F: Demo prep

### F1. Point the dashboard at the fine-tuned model
```bash
export EMBER_LLM_URL=https://<ember-url>/v1
export EMBER_LLM_MODEL=ember
export EMBER_LLM_KEY=<key>
python3 -m gateway.server --serial /dev/cu.usbmodem1101 --nodes config/nodes.json --sim quiet \
  --agent llm --auto --show-truth --airtime-budget 120
```
`--airtime-budget 120` keeps repeated rehearsal assessments from exhausting the hourly airtime.

### F2. Rehearse `docs/demo-script.md` twice
Key beats: real flame → CONFIRM over the radio → verdict; click-to-ignite → ALERT + LEDs blink; **fog → one `SET|rebase1` broadcast → event storm stops**; airtime gauge; results table.

### F3. Backups
- Record a full demo video (screen + phone video of the boards).
- Fallbacks: drop `--serial` (all nodes simulated), or use `--agent heuristic` if Modal is slow.

### F4. Five minutes before judging
Run the `curl .../v1/models` warm-up from E2 and keep the dashboard open.

---

## Phase G: Submission

1. The repo is already public at **https://github.com/UjwalKandi/ember** (created 2026-11-04). Push your latest work: `git push`. New device: `git clone https://github.com/UjwalKandi/ember`.
2. Check the README: results filled in, "Built during the hackathon" section, cookbook attribution, demo video link.
3. Submit on the hackathon page: repo link, video, and a one-paragraph pitch ("Existing wildfire networks detect. EMBER investigates, and re-programs its own sensors while doing it.").

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Downloads blocked when Claude Code runs a command | Apple Claude Code's sandbox; run the command yourself in Terminal, or allow the domain in the ACC Dashboard → Domains |
| Dashboard won't start inside Claude Code (`Operation not permitted`) | The sandbox blocks binding ports; run it in your own Terminal |
| git author shows `you@<hostname>.local` | `user.email` not set; see A3 |
| Modal `GatedRepoError` / 401 | HF access not approved or wrong token in the `huggingface` secret |
| Smoke run: tool calls missing from the rendered text | Axolotl is on the multimodal path; `_ember_config` must pop `processor_type` and `skip_prepare_dataset` |
| Smoke run: examples > 8192 tokens | Lower `max_events` in `Swarm.context()` or raise `SEQ_LEN` |
| vLLM: tool calls come back as plain text | The `muse_glimmer` tool parser is missing; check `_vllm()` flags. The agent also falls back to its own ATEM parser |
| Generation never stops | Never stop on `<|eom|>`; leave the checkpoint's generation config alone (`--generation-config auto`) |
| Every real node shows as unreliable | Battery token issue; nodes without a divider must omit `b` (fixed in firmware; re-flash) |
| Flasher: no port in the list | Charge-only cable, or the board isn't in download mode: hold B, tap R, release B. Use Chrome/Edge |
| Flasher page says the browser can't flash | You're in Safari/Firefox, or opened `http://` instead of `https://` |
| Console: "Failed to open serial port" | Another program holds the port (the dashboard, `pio device monitor`, another tab); close it |
| Board prints `unprovisioned` | Send `CFG|id=<n>` (an erase during install clears the id) |
| Dashboard: n1 never appears | Wrong `--serial` port, or the console tab is still open |
| Node stuck in SMK forever | Haze baseline latch; send `TX|C|nX|q1|SET|rebase1`, and re-flash if it recurs |

---

## Reusing this blueprint for a new project

EMBER is one instance of a general pattern: **an LLM agent that operates a low-bandwidth network of cheap edge devices**. Steps to adapt it to another domain (crop and livestock monitoring, structural health, flood gauges, warehouse sensing, …):

### The pattern (keep)
1. **Edge devices reduce the world to compact codes**: detect on-device; never stream raw data.
2. **A protocol co-designed for the model**: short ASCII packets the LLM reads natively (`protocol/SPEC.md`, `gateway/protocol.py`).
3. **Swarm state → compact text context**: a node table plus recent events (`gateway/swarm.py::context`).
4. **Tools that act over the network**: query, re-sample, configure, alert (`gateway/tools.py`).
5. **Airtime/bandwidth as a budget**: price every call so the agent learns value of information (`gateway/airtime.py`).
6. **Closed-loop edge re-programming**: the agent fixes false positives at the source (`tune_detectors`).
7. **Physics/digital-twin simulator with ground truth**: generates scenarios, look-alikes and failures (`gateway/sim.py`).
8. **Teacher trajectories**: an observation-only policy picks tools; ground truth gives the verdict (`gateway/policy.py`, `training/gen_data.py`).
9. **SFT on Modal with Axolotl QLoRA**, merge, serve on vLLM (`training/modal_app.py`).
10. **Honest eval**: rule baseline vs base model vs fine-tuned vs oracle, held-out compositions, tight budgets, after-action effect (`training/eval.py`).

### Files to change for a new domain
| Change | File |
|---|---|
| Event codes and fields | `protocol/SPEC.md`, `gateway/protocol.py` (`EVENT_CODES`) |
| On-device detectors | `firmware/src/sense_*.cpp`, `firmware/src/node.cpp` (thresholds must match the sim) |
| Scenarios, physics, look-alikes, ground truth | `gateway/sim.py` (`SCENARIOS`, `build`, `finalize_truth`) |
| Verdicts/causes and tool schemas | `gateway/tools.py` (`VERDICTS`, `CAUSES`, `TOOL_SCHEMAS`) |
| Domain knowledge in the system prompt | `gateway/prompts.py` |
| Investigation order, actions, report template | `gateway/policy.py` |
| Held-out compositions | `training/gen_data.py` (`HOLDOUT`) |

### Lessons learned (check these first next time)
- **Muse Glimmer makes one tool call per assistant message.** Training data must follow that format (`_step` in `gen_data.py`).
- **Tool data needs Axolotl's text-only `chat_template` path** (no `processor_type` / `skip_prepare_dataset`), plus `eot_tokens: ["<|eot|>", "<|eom|>"]`.
- **The Axolotl collator doesn't truncate.** Measure token lengths before training.
- **Muse Glimmer on vLLM needs the `vllm/vllm-openai:muse-glimmer` image** and the `muse_glimmer` tool and reasoning parsers. Serve a merged checkpoint, since LoRA serving isn't documented.
- **Simulator and firmware must agree** on thresholds, sample rate, debounce and replies, or the model trains on the wrong distribution.
- **Train/serve context parity:** the live context must look like the training context (EMBER drops command replies from it).
- **Labels must be observable.** If the evidence isn't in the context, the model learns to hallucinate. Check with a script.
- **Don't let the baseline saturate.** If rules score ~99%, fine-tuning has nothing to prove; add realistic look-alikes.
- **Watch flicker/debounce logic for toggling.** A suppression rule can create more events than it removes; measure after-action effects.
- **Calibrate cameras with labeled tests, not guesses.** Run `CAM?` for "nothing / flame / flashlight / lamp" and compare. That's how we found the RGB565 byte order was swapped and that colour alone can't tell lamps from flames.
- **Use robust temporal statistics.** The median consecutive-frame change beat (max−min)/mean, which a single auto-exposure frame or slow drift fooled.
- **Make detectors tunable at runtime** (NVS + console commands). Each calibration round took minutes instead of a rebuild and reflash.
- **One firmware image + runtime identity** (role/id in flash) plus a node that bridges itself over USB makes single-board bring-up and replication trivial.
