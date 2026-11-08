# Lab 00: Setup and cleanup

**Goal:** a clean, $0 environment with everything the labs need. That means the old paid Modal setup is removed, unused leftovers are deleted, the training libraries are installed in the project venv, llama.cpp is installed, and the datasets are generated.

**Time:** ~30 minutes, plus downloads.

## Concepts (why)
- **Nothing should be able to bill you.** Modal *volumes* (stored files) and running *apps* can cost money even when you're not using them. Modal *secrets* are free, but one of ours holds your Hugging Face token, which shouldn't sit in an account you no longer use.
- **One environment per project.** Everything goes into `~/Documents/meta/ember/.venv` (Python 3.12). The macOS system Python 3.9 is too old for current ML libraries.
- **Two kinds of tools.** `requirements-train.txt` is the *training* half (PyTorch, transformers, PEFT, TRL). Homebrew's `llama.cpp` is the *shipping* half (convert, quantize, serve).

## Steps

### 1. Remove everything on Modal
```bash
cd ~/Documents/meta/ember && source .venv/bin/activate
git pull
scripts/cleanup_modal.sh
```
This script:
1. lists your Modal apps and stops any that aren't stopped,
2. deletes the volumes `ember-hf-cache` and `ember-runs` (the model download, if it finished, lives there and could cost storage),
3. deletes the secrets `huggingface` and `ember-llm`,
4. prints the apps and volumes again (they should be empty),
5. uninstalls the `modal` package and removes your Modal login (`~/.modal.toml`).

✅ The "after cleanup" lists show no EMBER apps or volumes, and the script ends with `removed ~/.modal.toml`.

**Then revoke the old Hugging Face token** that was stored in Modal: huggingface.co → **Settings → Access Tokens** → delete it. If you need a token later (for gated models like Llama or Gemma), create a new **Read** token and log in with `huggingface-cli login`.

### 2. Remove local leftovers
```bash
scripts/cleanup_local.sh
```
This uninstalls the old Python 3.9 user packages (`modal`, `platformio`, `pyserial`, `grpclib`, `synchronicity`), purges the pip cache, and deletes `data/` and `firmware/.pio` (both regenerable).

✅ It ends with `done`, and `~/Library/Python/3.9` is much smaller than the original ~244 MB.

### 3. Install the training libraries
```bash
pip install -r requirements-train.txt
python -c "import torch; print(torch.__version__, 'MPS available:', torch.backends.mps.is_available())"
```
✅ You see a version number and `MPS available: True`. MPS is the Apple GPU backend PyTorch uses for training on your Mac.

`bitsandbytes` (4-bit QLoRA) is intentionally missing: it only works on NVIDIA GPUs, so it's installed only on Kaggle.

### 4. Install llama.cpp
```bash
brew install llama.cpp          # or: brew upgrade llama.cpp
llama-server --version
llama-quantize --help | head -5
```
✅ `llama-server` prints a version/build number, and `llama-quantize` prints its usage line.

**Optional (recommended for learning): build it from source.** Seeing it compile helps you understand what it is: a C/C++ library (ggml) plus tools.
```bash
git clone --depth 1 https://github.com/ggml-org/llama.cpp ~/src/llama.cpp
cd ~/src/llama.cpp
cmake -B build && cmake --build build -j
ls build/bin | head            # llama-server, llama-cli, llama-quantize, llama-bench, ...
cd ~/Documents/meta/ember
```
Metal (the Mac GPU) is enabled by default on Apple silicon. The labs use the Homebrew binaries, so the source build is only for exploring.

### 5. Generate the datasets
EMBER's training data is synthetic. `gateway/sim.py` simulates the wildfire swarm (fires, fog, glare, vehicles, dead nodes…). For each episode an observation-only policy picks the tool calls, and the simulator's ground truth supplies the right verdict. Each row is a full agent conversation: system prompt → swarm status → tool calls → tool results → `submit_assessment` → SITREP.

```bash
# small set for the labs (~600 rows; shorter contexts so it fits small models)
python -m training.gen_data --per-scenario 60 --max-examples 600 --max-events 25 --out data-small
# full set (~2,400 rows) for bigger runs later
python -m training.gen_data
```
✅ The small run prints `train=570 val=30 eval=420` and a breakdown by verdict and budget. `ls data-small` shows `train.jsonl val.jsonl eval_scenarios.jsonl manifest.json token_probe.jsonl`.

Have a look at one example:
```bash
head -1 data-small/train.jsonl | python -c "import json,sys; r=json.loads(sys.stdin.read()); [print(m['role'], '>>', (m.get('content') or json.dumps(m.get('tool_calls')))[:200]) for m in r['messages']]"
```

### 6. Sanity check
```bash
python -m unittest discover -s tests
```
✅ All tests `OK`.

## Checkpoint
- [ ] Modal: no apps, no volumes; `modal` uninstalled; old HF token revoked
- [ ] `torch` sees MPS
- [ ] `llama-server --version` works
- [ ] `data-small/` exists, and you've read one example conversation
- [ ] Tests pass

Log the date and anything surprising in [docs/LEARNING_LOG.md](../../docs/LEARNING_LOG.md).

## Interview questions
- Why keep training and inference tools separate in your head (and sometimes on separate machines)?
- What are the risks of training on synthetic data, and how does a held-out eval help?
