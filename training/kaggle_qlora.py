"""EMBER QLoRA fine-tune of Muse Glimmer 30B on Kaggle's free 2×T4 GPUs (no paid compute).

The model is loaded in 4-bit and sharded across both T4s (device_map="auto"); only LoRA
adapters on the language model's attention + MLP projections are trained (cookbook values:
r=32, alpha=16). Loss is computed on assistant turns only (tool calls and the SITREP).
T4s have no bf16, so compute is fp16.

Kaggle notebook (Settings: Accelerator "GPU T4 x2", Internet on), see training/KAGGLE.md:
    !pip install -q -U "transformers>=5.15" peft bitsandbytes accelerate
    !python kaggle_qlora.py --data /kaggle/input/ember-sft/train.jsonl --out /kaggle/working/ember-lora

Check the data + label masking on any machine (tokenizer only, no GPU):
    python training/kaggle_qlora.py --dry-run --data data-small/train.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional

MODEL = "meta-models/Muse-Glimmer-30B"
REVISION = "a4e59da52a7bc87ae7251dd5545c0dd437c44b68"   # same pin as the cookbook configs
TARGETS = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")


def render(tok, messages: List[dict], tools: Optional[List[dict]]) -> str:
    return tok.apply_chat_template(messages, tools=tools, tokenize=False)


def build_example(tok, row: Dict[str, Any], max_len: int) -> Optional[Dict[str, List[int]]]:
    """Tokenize one trajectory and label only the assistant messages.

    Assistant spans are found by rendering the conversation up to (and including) each
    assistant message and mapping those character ranges onto token offsets, so the mask
    follows the real chat template. Examples longer than max_len are dropped, not cut."""
    msgs, tools = row["messages"], row.get("tools")
    full = render(tok, msgs, tools)
    spans = []
    for i, m in enumerate(msgs):
        if m["role"] != "assistant":
            continue
        before, through = render(tok, msgs[:i], tools), render(tok, msgs[:i + 1], tools)
        if not (full.startswith(before) and full.startswith(through)):
            return None   # template isn't prefix-stable for this example; skip it
        spans.append((len(before), len(through)))
    enc = tok(full, add_special_tokens=False, return_offsets_mapping=True)
    ids = enc["input_ids"]
    if len(ids) > max_len or not spans:
        return None
    labels = [-100] * len(ids)
    for k, (s, _) in enumerate(enc["offset_mapping"]):
        if any(a <= s < b for a, b in spans):
            labels[k] = ids[k]
    return {"input_ids": ids, "labels": labels}


def load_rows(path: str, limit: int) -> List[dict]:
    rows = [json.loads(line) for line in open(path)]
    return rows[:limit] if limit else rows


def dry_run(args) -> None:
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(args.model, revision=args.revision)
    rows = load_rows(args.data, args.limit)
    built = [build_example(tok, r, args.max_len) for r in rows]
    kept = [b for b in built if b]
    lens = sorted(len(b["input_ids"]) for b in kept)
    print(f"examples={len(rows)} kept={len(kept)} dropped={len(rows) - len(kept)} (too long or template mismatch)")
    if lens:
        print(f"tokens p50={lens[len(lens) // 2]} p95={lens[int(len(lens) * .95)]} max={lens[-1]} (limit {args.max_len})")
        ex = kept[0]
        trained, cur = [], []
        for t, l in zip(ex["input_ids"], ex["labels"]):
            if l != -100:
                cur.append(t)
            elif cur:
                trained.append(tok.decode(cur))
                cur = []
        if cur:
            trained.append(tok.decode(cur))
        print(f"first example: {len(trained)} trained spans (expect one per assistant message)")
        for sp in trained:
            print("  ---", sp[:300].replace("\n", " "))


def train(args) -> None:
    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, Trainer,
                              TrainingArguments)

    tok = AutoTokenizer.from_pretrained(args.model, revision=args.revision)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    data = [b for b in (build_example(tok, r, args.max_len) for r in load_rows(args.data, args.limit)) if b]
    print(f"training examples: {len(data)}")

    n_gpu = torch.cuda.device_count()
    model = AutoModelForCausalLM.from_pretrained(
        args.model, revision=args.revision, device_map="auto", torch_dtype=torch.float16,
        low_cpu_mem_usage=True,
        max_memory={**{i: args.gpu_mem for i in range(n_gpu)}, "cpu": args.cpu_mem},
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16))
    model.is_parallelizable = model.model_parallel = True   # layers are split across both T4s
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    # language-model projections only; the vision tower stays frozen
    targets = sorted({n for n, _ in model.named_modules()
                      if n.split(".")[-1] in TARGETS and "vision" not in n and "visual" not in n})
    model = get_peft_model(model, LoraConfig(r=args.rank, lora_alpha=args.alpha, lora_dropout=0.05,
                                             target_modules=targets, task_type="CAUSAL_LM"))
    model.print_trainable_parameters()

    def collate(batch):
        width = max(len(b["input_ids"]) for b in batch)
        pad = lambda seq, v: seq + [v] * (width - len(seq))
        return {
            "input_ids": torch.tensor([pad(b["input_ids"], tok.pad_token_id) for b in batch]),
            "labels": torch.tensor([pad(b["labels"], -100) for b in batch]),
            "attention_mask": torch.tensor([pad([1] * len(b["input_ids"]), 0) for b in batch]),
        }

    targs = TrainingArguments(
        output_dir=args.out + "-ckpt", per_device_train_batch_size=1,
        gradient_accumulation_steps=args.grad_accum, num_train_epochs=args.epochs, max_steps=args.max_steps,
        learning_rate=args.lr, lr_scheduler_type="cosine", warmup_ratio=0.1, fp16=True,
        optim="paged_adamw_8bit", gradient_checkpointing=True, logging_steps=5,
        save_steps=args.save_steps, save_total_limit=2, report_to="none", remove_unused_columns=False)
    trainer = Trainer(model=model, args=targs, train_dataset=data, data_collator=collate)
    resume = os.path.isdir(targs.output_dir) and any(d.startswith("checkpoint-") for d in os.listdir(targs.output_dir))
    trainer.train(resume_from_checkpoint=resume or None)   # Kaggle sessions end after 12 h
    model.save_pretrained(args.out)
    tok.save_pretrained(args.out)
    print("adapter saved to", args.out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="ember-lora")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--revision", default=REVISION)
    ap.add_argument("--max-len", type=int, default=4096)
    ap.add_argument("--limit", type=int, default=0, help="use only the first N rows")
    ap.add_argument("--epochs", type=float, default=1)
    ap.add_argument("--max-steps", type=int, default=-1)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--rank", type=int, default=32)
    ap.add_argument("--alpha", type=int, default=16)
    ap.add_argument("--grad-accum", type=int, default=8)
    ap.add_argument("--save-steps", type=int, default=50)
    ap.add_argument("--gpu-mem", default="14GiB", help="per-GPU cap (T4 = 15 GiB usable)")
    ap.add_argument("--cpu-mem", default="26GiB")
    ap.add_argument("--dry-run", action="store_true", help="tokenizer-only check of lengths and label masks")
    args = ap.parse_args()
    dry_run(args) if args.dry_run else train(args)


if __name__ == "__main__":
    main()
