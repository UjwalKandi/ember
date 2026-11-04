# EMBER setup & run guide

A step-by-step runbook for taking EMBER from a fresh clone to a live demo. Use it to rebuild the project on another machine, or as a template for a similar "LLM agent + sensor mesh" project (see [Reusing this blueprint](#reusing-this-blueprint-for-a-new-project)).

Every phase ends with a ✅ checklist. Don't move on until it passes.

| Phase | What | Time | Can run in parallel with |
|---|---|---|---|
| [A](#phase-a-laptop-setup) | Laptop setup | ~20 min | — |
| [B](#phase-b-accounts-secrets-model-download) | Accounts, secrets, 60 GB model download | ~15 min + download | C |
| [C](#phase-c-hardware) | Flash and calibrate the XIAO boards | ~1.5–2 h | B, D |
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

### A2. Install the tools
```bash
python3 -m pip install --user modal pyserial platformio
echo 'export PATH="$HOME/Library/Python/3.9/bin:$PATH"' >> ~/.zshrc   # macOS system Python; adjust 3.9 to your version
source ~/.zshrc
modal --version
pio --version
```
✅ Both print a version (verified with `modal 1.2.6`, `PlatformIO 6.2.0`).

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
2. If there's a license or access form, accept it and wait for approval (the page will say you have access).
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

**Needs:** ≥4 XIAO ESP32-S3 Sense boards (1 gateway + 3 nodes), USB-C data cables, a USB power bank per field node.

### C1. Assemble
For each board:
1. Clip on the Sense expansion board (camera + mic).
2. Attach the **U.FL antenna**. Without it the range is a few metres.
3. Put a sticky label on each board: `GW`, `1`, `2`, `3`.

### C2. Flash the gateway
Plug in only the `GW` board:
```bash
cd firmware
pio run -e gateway -t upload        # first build downloads the ESP32 toolchain (several minutes)
ls /dev/cu.usbmodem*                # note this port, e.g. /dev/cu.usbmodem1101
```
- Upload can't find the board: hold **BOOT**, tap **RESET**, release BOOT, and retry.
- Compile errors: this firmware was written without a compiler available. Copy the full error for a fix.

### C3. Flash the nodes (each with its own id)
Unplug GW, plug in board `1`:
```bash
PLATFORMIO_BUILD_FLAGS=-DNODE_ID=1 pio run -e node -t upload
```
Repeat with board `2` / `NODE_ID=2` and board `3` / `NODE_ID=3`. Ids must match the `"real": true` entries in `config/nodes.json`.

### C4. Bring-up
Plug GW back into the laptop and power nodes 1–3 from power banks.
```bash
pio device monitor -e gateway       # Ctrl+C to quit
```
✅ Within 30 s: `RX|1|0|-4x|H|n1|s..|up..|hz..|snd..` lines from every node. You can type commands, e.g. `TX|C|n1|q1|PING`, which should get `R|n1|q1|PONG` back.

### C5. Calibrate (details in `firmware/README.md` → "Calibration")
| Test | Do | Expect |
|---|---|---|
| Quiet baseline | Leave nodes still in a quiet room for 2 min | `hz` and `snd` in heartbeats around 3–10 (the simulator's range). If not, adjust `HAZE_GAIN` / the dBFS offset per the README and re-flash |
| Flame | Hold a lighter ~30 cm from node 2's camera | `E|n2|...|FLM|c40+` |
| Glare | Shine a phone flashlight steadily, then send `TX|C|n2|q2|CONFIRM` | The reply is **not** `CONF|FLM` |
| Fog / rebase | Breathe on or diffuse the lens until `SMK` appears, then send `TX|C|*|q3|SET|rebase1` | Node returns to `OK` |
| Relay | Carry node 3 out of the gateway's range, with node 1 or 2 between them | Node 3 packets arrive with hops `1` (`RX|3|1|...`) |

- Flame never registers: swap the RGB565 byte order in `sense_cam.cpp` (see README).

### C6. Layout
Place the three nodes where they'll be during the demo, measure or pace their positions in metres, and edit `x`/`y` for ids 1–3 in `config/nodes.json`.

### C7. Live test (real + simulated nodes together)
```bash
cd ..        # back to the repo root
python3 -m gateway.server --serial /dev/cu.usbmodem1101 --nodes config/nodes.json --sim quiet --auto
```
✅ Nodes 1–3 on the map are the real boards; 4–9 are simulated. A lighter in front of a real node triggers an assessment, and its CONFIRM goes over the radio to that board.

### ✅ Phase C checklist
- [ ] gateway + 3 nodes flashed, heartbeats visible
- [ ] calibration tests pass
- [ ] `config/nodes.json` positions updated
- [ ] live dashboard shows the real nodes

---

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

1. Create the public repo and push:
   ```bash
   gh auth login                 # once
   gh repo create ember --public --source . --push
   ```
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
