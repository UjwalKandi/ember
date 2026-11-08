"""See how different models turn the same EMBER conversation into tokens (Lab 02).

    python training/inspect_templates.py --models Qwen/Qwen3-0.6B,HuggingFaceTB/SmolLM3-3B
Prints, per model: whether its chat template renders tools natively (or needs EMBER's text
fallback), how tools / tool calls / tool results are formatted, special tokens, and how many
tokens the system prompt, swarm context and whole trajectory cost. Only downloads tokenizers.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.sft_data import build_example, load_rows, prepare, render  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="Qwen/Qwen3-0.6B")
    ap.add_argument("--data", default="data-small/train.jsonl")
    ap.add_argument("--row", type=int, default=0)
    ap.add_argument("--chars", type=int, default=1800, help="how much of the rendered text to show")
    args = ap.parse_args()
    from transformers import AutoTokenizer
    row = load_rows(args.data)[args.row]
    print(f"trajectory: scenario={row['meta']['scenario']} messages={len(row['messages'])} tools={len(row['tools'])}")
    for mid in args.models.split(","):
        tok = AutoTokenizer.from_pretrained(mid.strip())
        msgs, tools, mode = prepare(tok, row)
        text = render(tok, msgs, tools)
        n = lambda s: len(tok(s, add_special_tokens=False)["input_ids"])
        ex = build_example(tok, row, 100_000)
        trained = sum(l != -100 for l in ex["labels"]) if ex else 0
        print("\n" + "=" * 100)
        print(f"{mid}  template_mode={mode}  vocab={len(tok)}  bos={tok.bos_token!r} eos={tok.eos_token!r}")
        print(f"tokens: system prompt={n(row['messages'][0]['content'])}  swarm context={n(row['messages'][1]['content'])}"
              f"  tool schemas={n(json.dumps(row['tools']))}  whole trajectory={n(text)}  trained={trained}")
        first_call = text.find("request_confirm") if "request_confirm" in text else text.find("submit_assessment")
        start = max(0, first_call - args.chars // 3)
        print(f"--- rendered text around the first tool call (chars {start}..{start + args.chars}) ---")
        print(text[start:start + args.chars])


if __name__ == "__main__":
    main()
