# Lab 04: Supervised fine-tuning with LoRA

**Goal:** fine-tune your student model (chosen in Lab 03) on EMBER's agent trajectories, on your Mac. Learn to sanity-check a training setup, read loss curves, and run small, controlled experiments.

The examples use `Qwen/Qwen3-1.7B`; swap in your own choice. ≤ 4B models are practical on a 24 GB Mac.

## Concepts (why)
- **SFT (supervised fine-tuning):** show the model correct conversations and train it to predict the assistant's tokens (next-token cross-entropy, assistant tokens only; see Lab 02).
- **LoRA:** freeze the original weights `W` and learn a small low-rank update instead, `W' = W + (alpha / r) · B·A`, where `A` and `B` are thin matrices of rank `r`. Only `A` and `B` are trained (often <1% of parameters), so it fits in memory, trains fast, and the adapter is a small file.
  `training/sft_lora.py` puts LoRA on the attention and MLP projections (`q/k/v/o_proj`, `gate/up/down_proj`).
- **Hyperparameters you'll touch:**

  | Flag | Default | What it does |
  |---|---|---|
  | `--rank` | 16 | adapter capacity |
  | `--alpha` | 32 | scales the update (`alpha/r`) |
  | `--lr` | 2e-4 | learning rate (cosine schedule, 5% warmup) |
  | `--epochs` | 1 | passes over the data |
  | `--grad-accum` | 8 | the batch is 1 example, so the **effective batch = grad-accum** |
  | `--max-len` | 4096 | examples longer than this are dropped |
  | `--dropout` | 0.05 | LoRA dropout |

- **Overfitting:** train loss keeps falling while eval loss flattens or rises. The model is memorizing rather than generalizing.
- **Mac vs NVIDIA:** on the Mac, training runs on the Apple GPU via PyTorch **MPS** in bf16. **QLoRA** (`--qlora`, a 4-bit frozen base) needs bitsandbytes, which is NVIDIA-only. Use it on Kaggle for 7–8B models.

## Steps

### 1. Dry run: data and masking (seconds)
```bash
cd ~/Documents/meta/ember && source .venv/bin/activate
python training/sft_lora.py --model Qwen/Qwen3-1.7B --data data-small/train.jsonl --dry-run
```
✅ Few or no examples dropped, p95 tokens under 4096, and trained spans that are only tool calls and the SITREP (see Lab 02).

### 2. Sanity check: overfit one batch (a few minutes)
```bash
python training/sft_lora.py --model Qwen/Qwen3-1.7B --data data-small/train.jsonl --overfit-batch --out runs/overfit-check
```
`--overfit-batch` trains on just **8 examples** for **60 steps** with no eval. A working setup must memorize them, so loss should fall towards ~0.

✅ `metrics.csv` shows loss dropping steeply.
❌ If it doesn't, something is broken (masking, learning rate, template). Fix it **before** spending an hour on a real run. Professionals do this check every time.

```bash
python training/plot_metrics.py runs/overfit-check/metrics.csv
```

### 3. The real run
```bash
python training/sft_lora.py --model Qwen/Qwen3-1.7B \
    --data data-small/train.jsonl --val data-small/val.jsonl --out runs/qwen3-1.7b-lora
```
What it prints and writes:
- `device=mps dtype=torch.bfloat16 qlora=False`
- `trainable params: … || all params: … || trainable%: …`
- `runs/qwen3-1.7b-lora/metrics.csv`: step, epoch, loss, eval_loss (every `--eval-steps`, default 25), learning_rate, grad_norm
- checkpoints in `runs/qwen3-1.7b-lora/ckpt/` (every `--save-steps`, default 50). Re-running the same command **resumes** after an interruption.
- the final adapter in `runs/qwen3-1.7b-lora/adapter/`

⏱ Time depends heavily on model size and your Mac's load. A 0.6B model may take minutes; a 1.7B model around an hour or more for 570 examples. Start with `--limit 200` to get a feel for it.

### 4. Read the curves
```bash
python training/plot_metrics.py runs/qwen3-1.7b-lora/metrics.csv
open runs/qwen3-1.7b-lora/metrics.png
```
✅ Train loss falls quickly, then flattens. Eval loss falls and settles near train loss. The script also prints first → last values.
⚠️ Eval loss rising while train loss falls is overfitting: fewer epochs, a lower `--lr`, or a smaller `--rank`.

### 5. If you run out of memory
- `--max-len 3072`, or regenerate data with a smaller `--max-events` (Lab 00)
- `--limit 300` to train on fewer examples
- a smaller model (`Qwen/Qwen3-0.6B`)
- quit other apps. Training memory is model weights + activations + optimizer state for the LoRA parameters.

### 6. 7–8B models (e.g. TwIL-8B) → Kaggle
These don't fit for training on a 24 GB Mac. Use Kaggle's free GPU with QLoRA:
```bash
python sft_lora.py --model <8B model> --data train.jsonl --qlora --out /kaggle/working/lora
```
See [training/KAGGLE.md](../../training/KAGGLE.md) for the notebook steps. `--qlora` refuses to run without an NVIDIA GPU.

## Exercises
Change one thing at a time, keep everything else fixed, and record the final eval loss (plus the Lab 05 eval score if you have time):

| Run | rank | alpha | lr | epochs | final train loss | final eval loss | notes |
|---|---|---|---|---|---|---|---|
| baseline | 16 | 32 | 2e-4 | 1 | | | |
| low rank | 8 | 16 | 2e-4 | 1 | | | |
| high rank | 32 | 64 | 2e-4 | 1 | | | |
| low lr | 16 | 32 | 1e-4 | 1 | | | |
| 2 epochs | 16 | 32 | 2e-4 | 2 | | | |

Questions to answer:
1. Does a higher rank help, or just overfit faster on 570 examples?
2. What does `grad_norm` look like when the learning rate is too high?
3. Does lower eval loss always mean a better agent? Check with Lab 05; often it doesn't fully match.

## Checkpoint
- [ ] The overfit check passed (loss → ~0)
- [ ] You have a trained adapter in `runs/<name>/adapter/` and a loss plot
- [ ] You ran at least two experiments from the table and logged them

## Interview questions
- Explain LoRA. Why does a low-rank update work, and what do `r` and `alpha` control?
- What's the effective batch size here, and why use gradient accumulation?
- How do you detect overfitting? What do you change first?
- When would you choose full fine-tuning over LoRA? When QLoRA?
- Why run an "overfit one batch" test before a long run?
