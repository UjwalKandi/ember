# Lab 05: Export, deploy and evaluate the fine-tuned model

**Goal:** close the loop. Turn your LoRA adapter into GGUF, serve it with llama.cpp, and prove with the same evaluation as Lab 03 whether fine-tuning helped, and whether quantization keeps the gain.

## Concepts (why)
- **Two ways to ship an adapter:**

  | Route | What you get | Good for |
  |---|---|---|
  | **merged** | the update baked into the weights (`W' = W + (alpha/r)·B·A`), one self-contained GGUF | deployment: one file, no extra runtime cost |
  | **adapter** | a small LoRA GGUF loaded on top of a base GGUF with `--lora` | swapping or comparing several fine-tunes on the same base |

- **Quantize after fine-tuning.** You trained in bf16. Quantizing to Q4_K_M can erase small gains, so you measure it rather than assume.
- **Fair evaluation:** same episodes (`data-small`/`data` `eval_scenarios.jsonl`), same `--limit`, same request settings (`EMBER_LLM_EXTRA`), same server flags. Only the model changes.

## Steps

### 1. Export (merged route)
```bash
cd ~/Documents/meta/ember && source .venv/bin/activate
training/export_gguf.sh merged runs/qwen3-1.7b-lora/adapter Q4_K_M
```
What it does:
1. clones llama.cpp into `.llama.cpp` if needed and installs its converter requirements
2. `training/merge_lora.py`: loads the base model named in `adapter_config.json`, merges the LoRA, and saves `runs/qwen3-1.7b-lora/merged/`
3. `convert_hf_to_gguf.py` → `runs/qwen3-1.7b-lora/model-f16.gguf`
4. `llama-quantize` → `runs/qwen3-1.7b-lora/model-Q4_K_M.gguf`

✅ It lists the `.gguf` files and prints the serve command.

### 2. Export (adapter route)
```bash
training/export_gguf.sh adapter runs/qwen3-1.7b-lora/adapter
```
✅ `runs/qwen3-1.7b-lora/lora.gguf`, a small file, plus the serve command. This uses `convert_lora_to_gguf.py` with the base model id from the adapter config.

### 3. Serve it
Either the merged file:
```bash
scripts/serve_gguf.sh runs/qwen3-1.7b-lora/model-Q4_K_M.gguf
```
or the base model plus the adapter:
```bash
scripts/serve_gguf.sh Qwen/Qwen3-1.7B-GGUF:Q8_0 --lora runs/qwen3-1.7b-lora/lora.gguf
```
✅ `llama-server` listening on http://127.0.0.1:8081. The model alias is `local`.

### 4. Evaluate: same protocol as the bake-off
In a second terminal, with the **same** request settings the bake-off used for this model (see `request_extra` in `config/models.json`):
```bash
cd ~/Documents/meta/ember && source .venv/bin/activate
export EMBER_LLM_EXTRA='{"chat_template_kwargs":{"enable_thinking":false}}'
python -m training.eval --agent llm --url http://localhost:8081/v1 --model local --limit 2 --workers 1 --name sft-qwen3-1.7b
```
✅ The script prints a summary and saves `results/sft-qwen3-1.7b.json`.

The bake-off used `--limit 1`. For a cleaner comparison, also re-run the base model with `--limit 2`, saving it as `--name base-qwen3-1.7b`.

### 5. Compare
You need the reference runs once (Lab 03):
```bash
python -m training.eval --agent heuristic
python -m training.eval --agent oracle
```
Then:
```bash
python -m training.eval --report results/heuristic.json results/bakeoff-qwen3-1.7b.json results/sft-qwen3-1.7b.json results/oracle.json
```
✅ A markdown table with one column per run: accuracy, false alarms, accuracy under tight airtime, failures, fallback rate, sec/episode, tok/s, and per-scenario scores. Paste it into your learning log.

How to read it:
- **SFT vs base:** did `valid`, `bad_tool_calls` and `no_assessment` improve (tool-use competence)? Did verdict/cause accuracy improve (decision quality)?
- **SFT vs heuristic:** does the model beat hand-written rules on the hard cases (fog, dusty roads, vehicles)?
- **SFT vs oracle:** the gap is what's left for Labs 07–08.
- **Unseen compositions** (`wildfire_plus_stuck`, `wildfire_in_fog` were never in training): did it generalize or just memorize?

### 6. Watch it in the dashboard
```bash
export EMBER_LLM_URL=http://localhost:8081/v1 EMBER_LLM_MODEL=local
python -m gateway.server --sim fog --agent llm --show-truth
```
Add `--serial /dev/cu.usbmodemXXXX --nodes config/nodes.json` to put your fine-tuned model in charge of the real boards.

## Exercises
1. **Does quantization erase the gain?** Export the same adapter at `Q8_0` (`training/export_gguf.sh merged runs/qwen3-1.7b-lora/adapter Q8_0`), evaluate both, and compare:

   | Model | Quant | verdict acc | cause acc | false alarms | sec/episode |
   |---|---|---|---|---|---|
   | base | Q8_0 | | | | |
   | SFT | Q8_0 | | | | |
   | SFT | Q4_K_M | | | | |

2. Merged vs adapter route: same accuracy? Same speed?
3. Find one episode the base failed and the SFT model got right, and one the SFT model still fails. What's different?
4. Which Lab 04 experiment (rank, lr, epochs) gave the best *agent* score, as opposed to the best eval loss?

## Checkpoint
- [ ] A fine-tuned GGUF served by llama.cpp
- [ ] A comparison table: heuristic vs base vs SFT vs oracle on the same episodes
- [ ] A written conclusion in your learning log: did SFT help, where, and by how much?

## Interview questions
- Merge an adapter or load it at runtime? Trade-offs?
- How do you make sure an eval comparison between two models is fair?
- Your fine-tune has lower eval loss but a worse agent score. What could explain it?
- How do you check whether quantization hurt a fine-tuned model?
