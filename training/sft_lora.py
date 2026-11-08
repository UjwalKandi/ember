"""LoRA / QLoRA supervised fine-tuning of a small chat model on EMBER trajectories (Lab 04).

Runs on your Mac (Apple GPU via PyTorch MPS) for models up to ~4B, or on a free CUDA GPU
(Kaggle T4) with --qlora for 7-8B models. Deliberately written with plain transformers.Trainer
+ PEFT so every moving part is visible:

  data      training/sft_data.py renders each trajectory with the model's own chat template
            and masks everything except the assistant turns (labels = -100)
  model     base weights frozen; small low-rank adapters (LoRA) trained on attention + MLP
  QLoRA     same, but the frozen base is stored in 4-bit (bitsandbytes, CUDA only)
  logs      metrics.csv (train/eval loss, learning rate) -> training/plot_metrics.py

Examples:
  # 1. check data + masking (tokenizer only, seconds)
  python training/sft_lora.py --model Qwen/Qwen3-0.6B --data data-small/train.jsonl --dry-run
  # 2. sanity check: a model must be able to memorize 8 examples (loss -> ~0)
  python training/sft_lora.py --model Qwen/Qwen3-0.6B --data data-small/train.jsonl --overfit-batch
  # 3. real run on the Mac
  python training/sft_lora.py --model Qwen/Qwen3-1.7B --data data-small/train.jsonl \
      --val data-small/val.jsonl --out runs/qwen3-1.7b-lora
  # 4. 8B on Kaggle (2xT4 or 1xT4), see training/KAGGLE.md
  python sft_lora.py --model <8B model> --data train.jsonl --qlora --out /kaggle/working/lora
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.sft_data import build_example, load_rows, prepare, trained_spans  # noqa: E402

PROJ = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")


def pick_device(force: str = "auto") -> str:
    import torch
    if force != "auto":
        return force
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def pick_dtype(device: str, choice: str):
    import torch
    if choice != "auto":
        return {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": torch.float32}[choice]
    if device == "cuda":
        return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16   # T4 -> fp16
    if device == "mps":
        return torch.bfloat16
    return torch.float32


def build_dataset(tok, path: str, max_len: int, limit: int = 0):
    rows = load_rows(path, limit)
    data = [b for b in (build_example(tok, r, max_len) for r in rows) if b]
    return rows, data


def dry_run(args) -> None:
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(args.model, revision=args.revision)
    rows, data = build_dataset(tok, args.data, args.max_len, args.limit)
    mode = prepare(tok, rows[0])[2]
    lens = sorted(len(b["input_ids"]) for b in data)
    print(f"model={args.model} template_mode={mode} (native = the template renders tools itself)")
    print(f"examples={len(rows)} kept={len(data)} dropped={len(rows) - len(data)} (too long or template mismatch)")
    if not data:
        return
    trained = sum(sum(l != -100 for l in b["labels"]) for b in data)
    total = sum(len(b["input_ids"]) for b in data)
    print(f"tokens/example p50={lens[len(lens) // 2]} p95={lens[int(len(lens) * .95)]} max={lens[-1]} "
          f"(limit {args.max_len}); trained tokens = {100 * trained / total:.0f}% of all tokens")
    spans = trained_spans(tok, data[0])
    print(f"first example: {len(spans)} trained spans (one per assistant message):")
    for sp in spans:
        print("  ---", sp[:240].replace("\n", " "))


def train(args) -> None:
    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (AutoModelForCausalLM, AutoTokenizer, Trainer, TrainerCallback,
                              TrainingArguments)

    device = pick_device(args.device)
    dtype = pick_dtype(device, args.dtype)
    print(f"device={device} dtype={dtype} qlora={args.qlora}")
    if args.qlora and device != "cuda":
        raise SystemExit("--qlora needs an NVIDIA GPU (bitsandbytes). On a Mac, train a <=4B model without it.")

    tok = AutoTokenizer.from_pretrained(args.model, revision=args.revision)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    _, train_data = build_dataset(tok, args.data, args.max_len, args.limit)
    val_data = build_dataset(tok, args.val, args.max_len)[1] if args.val else None
    if args.overfit_batch:
        # Sanity check: with 8 examples and many steps the loss must fall towards 0.
        # If it doesn't, something is broken (masking, lr, template) before any real run.
        train_data, val_data = train_data[:8], None
        args.epochs, args.max_steps, args.grad_accum, args.save_steps = 1, 60, 1, 10_000
    print(f"train examples={len(train_data)} val examples={len(val_data) if val_data else 0}")

    load_kw = dict(revision=args.revision, torch_dtype=dtype, low_cpu_mem_usage=True)
    if args.qlora:
        from transformers import BitsAndBytesConfig
        n_gpu = torch.cuda.device_count()
        load_kw.update(device_map="auto",
                       max_memory={**{i: args.gpu_mem for i in range(n_gpu)}, "cpu": args.cpu_mem},
                       quantization_config=BitsAndBytesConfig(
                           load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                           bnb_4bit_compute_dtype=dtype))
    model = AutoModelForCausalLM.from_pretrained(args.model, **load_kw)
    if args.qlora:
        model.is_parallelizable = model.model_parallel = True   # layers may span both T4s
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    else:
        model.to(device)
        model.gradient_checkpointing_enable()
        model.enable_input_require_grads()   # lets gradients reach the adapters through frozen layers
    model.config.use_cache = False

    # LoRA on the language model's attention + MLP projections (vision towers stay frozen)
    targets = sorted({n for n, _ in model.named_modules()
                      if n.split(".")[-1] in PROJ and "vision" not in n and "visual" not in n})
    model = get_peft_model(model, LoraConfig(r=args.rank, lora_alpha=args.alpha, lora_dropout=args.dropout,
                                             target_modules=targets, task_type="CAUSAL_LM"))
    model.print_trainable_parameters()

    def collate(batch):
        width = max(len(b["input_ids"]) for b in batch)
        pad = lambda seq, v: seq + [v] * (width - len(seq))
        return {"input_ids": torch.tensor([pad(b["input_ids"], tok.pad_token_id) for b in batch]),
                "labels": torch.tensor([pad(b["labels"], -100) for b in batch]),
                "attention_mask": torch.tensor([pad([1] * len(b["input_ids"]), 0) for b in batch])}

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    metrics_path = out / "metrics.csv"

    class CsvLogger(TrainerCallback):
        def on_log(self, _args, state, control, logs=None, **kw):
            logs = logs or {}
            new = not metrics_path.exists()
            with open(metrics_path, "a", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["step", "epoch", "loss", "eval_loss", "learning_rate", "grad_norm"])
                w.writerow([state.global_step, round(state.epoch or 0, 3), logs.get("loss", ""),
                            logs.get("eval_loss", ""), logs.get("learning_rate", ""), logs.get("grad_norm", "")])

    targs = TrainingArguments(
        output_dir=str(out / "ckpt"), per_device_train_batch_size=1, per_device_eval_batch_size=1,
        gradient_accumulation_steps=args.grad_accum, num_train_epochs=args.epochs, max_steps=args.max_steps,
        learning_rate=args.lr, lr_scheduler_type="cosine", warmup_ratio=0.05,
        fp16=args.qlora and dtype == torch.float16, bf16=args.qlora and dtype == torch.bfloat16,
        optim="paged_adamw_8bit" if args.qlora else "adamw_torch",
        logging_steps=args.log_steps, eval_strategy="steps" if val_data else "no", eval_steps=args.eval_steps,
        save_steps=args.save_steps, save_total_limit=2, report_to="none", remove_unused_columns=False,
        dataloader_pin_memory=device == "cuda")
    trainer = Trainer(model=model, args=targs, train_dataset=train_data, eval_dataset=val_data,
                      data_collator=collate, callbacks=[CsvLogger()])
    ckpt = Path(targs.output_dir)
    resume = ckpt.is_dir() and any(p.name.startswith("checkpoint-") for p in ckpt.iterdir())
    trainer.train(resume_from_checkpoint=resume or None)   # resumes after an interrupted session
    model.save_pretrained(out / "adapter")
    tok.save_pretrained(out / "adapter")
    print(f"adapter saved to {out / 'adapter'}; metrics in {metrics_path}")
    print(f"next: python training/plot_metrics.py {metrics_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, help="Hugging Face model id, e.g. Qwen/Qwen3-1.7B")
    ap.add_argument("--revision", default=None)
    ap.add_argument("--data", required=True)
    ap.add_argument("--val")
    ap.add_argument("--out", default="runs/lora")
    ap.add_argument("--max-len", type=int, default=4096)
    ap.add_argument("--limit", type=int, default=0, help="use only the first N training rows")
    ap.add_argument("--epochs", type=float, default=1)
    ap.add_argument("--max-steps", type=int, default=-1)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--alpha", type=int, default=32)
    ap.add_argument("--dropout", type=float, default=0.05)
    ap.add_argument("--grad-accum", type=int, default=8, help="effective batch size (batch is 1)")
    ap.add_argument("--log-steps", type=int, default=5)
    ap.add_argument("--eval-steps", type=int, default=25)
    ap.add_argument("--save-steps", type=int, default=50)
    ap.add_argument("--device", default="auto", choices=["auto", "mps", "cuda", "cpu"])
    ap.add_argument("--dtype", default="auto", choices=["auto", "bf16", "fp16", "fp32"])
    ap.add_argument("--qlora", action="store_true", help="4-bit base (CUDA only; for 7-8B on Kaggle)")
    ap.add_argument("--gpu-mem", default="14GiB", help="QLoRA per-GPU cap (T4 = 15 GiB usable)")
    ap.add_argument("--cpu-mem", default="26GiB")
    ap.add_argument("--overfit-batch", action="store_true", help="sanity check: memorize 8 examples")
    ap.add_argument("--dry-run", action="store_true", help="tokenizer-only check of lengths and label masks")
    args = ap.parse_args()
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")   # ops MPS lacks fall back to CPU
    dry_run(args) if args.dry_run else train(args)


if __name__ == "__main__":
    main()
