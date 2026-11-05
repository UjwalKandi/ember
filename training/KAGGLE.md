# Fine-tuning Muse Glimmer for free on Kaggle (2×T4)

No credit card is needed. Kaggle gives about 30 GPU hours per week with "GPU T4 x2" (2 × 16 GB), and the script fits a 4-bit 30B model with LoRA across both cards.

## 0. Build and check the dataset (on your Mac)
```bash
cd ~/Documents/meta/ember && source .venv/bin/activate
python -m training.gen_data --per-scenario 60 --max-examples 600 --max-events 25 --out data-small
pip install -U "transformers>=5.15"                      # tokenizer only, for the check below
python training/kaggle_qlora.py --dry-run --data data-small/train.jsonl
```
✅ You should see `kept=…` with almost nothing dropped, token p95 well under 4096, and one trained span per assistant message (tool calls and the final SITREP). If many examples are dropped as too long, lower `--max-events`.

## 1. Upload the data to Kaggle
1. Go to kaggle.com → **Datasets → New Dataset**, name it `ember-sft`.
2. Upload `data-small/train.jsonl` and `data-small/val.jsonl`.
3. Upload `training/kaggle_qlora.py` too (or paste it into a notebook cell later).

## 2. Create the notebook
1. **Code → New Notebook**, then **Add Input** → your `ember-sft` dataset.
2. In the right panel, set **Accelerator: GPU T4 x2**, **Internet: On**, **Persistence: Files only**.
   Phone verification is required once to enable GPU and Internet.
3. First cell:
   ```python
   !pip install -q -U "transformers>=5.15" peft bitsandbytes accelerate
   import os; os.environ["HF_HOME"] = "/tmp/hf"     # big scratch disk for the model download
   ```
4. Smoke test (a few steps, to check memory and speed):
   ```python
   !python /kaggle/input/ember-sft/kaggle_qlora.py --data /kaggle/input/ember-sft/train.jsonl \
       --out /kaggle/working/ember-lora --max-steps 4 --save-steps 2
   ```
   ✅ The log shows `trainable params …`, the model split across `cuda:0` and `cuda:1`, and loss values.
5. Full run (≈600 examples, 1 epoch):
   ```python
   !python /kaggle/input/ember-sft/kaggle_qlora.py --data /kaggle/input/ember-sft/train.jsonl \
       --out /kaggle/working/ember-lora
   ```
   Checkpoints are saved every 50 steps. If the 12 h session ends, run the same cell again and it resumes.
6. Download `/kaggle/working/ember-lora/` (Output tab → download). It's the small LoRA adapter, not the 30B model.

## 3. Run the fine-tuned model locally (free)
```bash
scripts/lora_to_gguf.sh ./ember-lora ember-lora.gguf
scripts/run_local_model.sh --lora ember-lora.gguf
python -m training.eval --agent llm --url http://localhost:8081/v1 --model muse-glimmer \
    --limit 2 --workers 1 --name ember-local
```

## Known risks (check on the first run)
| Risk | Sign | What to do |
|---|---|---|
| Disk: the bf16 checkpoint is ~60 GB | "No space left on device" while downloading | Keep `HF_HOME=/tmp/hf`. Also search Kaggle **Models** for Muse Glimmer and add it as an input instead of downloading |
| GPU memory | CUDA OOM | `--max-len 3072`, regenerate data with `--max-events 15`, or `--gpu-mem 15GiB` |
| fp16 overflow (T4 has no bf16) | loss becomes `nan` | lower `--lr 1e-4`; keep LoRA r=32 |
| Too slow for the weekly quota | step time × steps > ~25 h | `--limit 300` or `--epochs 0.5` |
| `convert_lora_to_gguf.py` rejects the architecture | error naming muse-glimmer | update the llama.cpp clone (`rm -rf .llama.cpp`); otherwise merge on Kaggle CPU and convert the full model |
