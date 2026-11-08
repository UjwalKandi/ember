"""Merge a LoRA adapter into its base model and save a normal Hugging Face checkpoint (Lab 05).

    python training/merge_lora.py --adapter runs/qwen3-1.7b-lora/adapter --out runs/qwen3-1.7b-merged
Merging bakes W' = W + (alpha/r)·B·A into the base weights, so inference needs no PEFT and the
result can be converted to GGUF like any other model. (Alternative: keep the adapter separate
and load it at runtime with llama-server --lora; see training/export_gguf.sh.)
"""
import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer
    base_id = json.load(open(Path(args.adapter) / "adapter_config.json"))["base_model_name_or_path"]
    print("base model:", base_id)
    base = AutoModelForCausalLM.from_pretrained(base_id, torch_dtype=torch.bfloat16, low_cpu_mem_usage=True)
    merged = PeftModel.from_pretrained(base, args.adapter).merge_and_unload()
    merged.save_pretrained(args.out, safe_serialization=True)
    AutoTokenizer.from_pretrained(args.adapter).save_pretrained(args.out)
    print("merged model saved to", args.out)


if __name__ == "__main__":
    main()
