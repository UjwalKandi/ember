# Fine-tuning 7–8B models for free on Kaggle

Models up to ~4B train on your Mac (see [labs/04-sft-lora](../labs/04-sft-lora/README.md)). Bigger ones, such as **webAI TwIL-8B** or Llama/Qwen 8B, need an NVIDIA GPU and 4-bit **QLoRA**. Kaggle gives about 30 free GPU hours per week; no credit card is needed, just phone verification. An 8B model in 4-bit (~5–6 GB) plus LoRA fits on a single T4 (16 GB).

The training script is the same one you use on the Mac, `training/sft_lora.py`, with `--qlora`.

## 1. Prepare and check on your Mac
```bash
cd ~/Documents/meta/ember && source .venv/bin/activate
python -m training.gen_data --per-scenario 60 --max-examples 600 --max-events 25 --out data-small
python training/sft_lora.py --model <hf-model-id> --data data-small/train.jsonl --dry-run
```
✅ Few examples dropped, token p95 under `--max-len` (4096), and one trained span per assistant message.

## 2. Upload to Kaggle
1. kaggle.com → **Datasets → New Dataset** named `ember-sft`.
2. Upload `data-small/train.jsonl`, `data-small/val.jsonl`, `training/sft_lora.py` and `training/sft_data.py`.

## 3. Notebook
1. **Code → New Notebook**, then **Add Input** → `ember-sft`.
2. Settings: **Accelerator: GPU T4 x2** (or P100), **Internet: On**, **Persistence: Files only**.
3. Cells:
   ```python
   !pip install -q -U transformers peft accelerate bitsandbytes
   !mkdir -p training && cp /kaggle/input/ember-sft/sft_*.py training/ && touch training/__init__.py
   ```
   ```python
   # smoke test: memory + speed
   !python training/sft_lora.py --model <hf-model-id> --data /kaggle/input/ember-sft/train.jsonl \
       --qlora --max-steps 4 --out /kaggle/working/run
   ```
   ```python
   # full run (resumes from checkpoints if the 12 h session ends: just rerun the cell)
   !python training/sft_lora.py --model <hf-model-id> --data /kaggle/input/ember-sft/train.jsonl \
       --val /kaggle/input/ember-sft/val.jsonl --qlora --out /kaggle/working/run
   ```
4. Download `/kaggle/working/run/adapter/` and `metrics.csv` from the Output tab.

## 4. Back on the Mac: export and run with llama.cpp
```bash
training/export_gguf.sh adapter runs/<name>/adapter      # small LoRA GGUF on top of the base GGUF
scripts/serve_gguf.sh <base-gguf-repo:quant> --lora runs/<name>/lora.gguf
```

## Known risks
| Risk | Sign | Fix |
|---|---|---|
| GPU memory | CUDA OOM | `--max-len 3072`, or regenerate data with `--max-events 15` |
| fp16 overflow (T4 has no bf16) | loss `nan` | `--lr 1e-4` |
| Weekly quota | step time × steps > ~25 h | `--limit 300` or `--epochs 0.5` |
| Gated model (Llama/Gemma) | 401 when downloading | accept the license on Hugging Face and add an HF token as a Kaggle secret |
| Non-commercial license (e.g. TwIL per its card) | — | fine for learning; don't ship the fine-tune commercially |
