"""EMBER on Modal: weights download, QLoRA SFT (Axolotl), LoRA merge, and vLLM serving.

Setup (once):
    pip install modal && modal setup
    modal secret create huggingface HF_TOKEN=hf_...          # access to meta-models/Muse-Glimmer-30B
    modal secret create ember-llm EMBER_LLM_KEY=<any-string>  # bearer token for the vLLM endpoint

Pipeline:
    modal run training/modal_app.py::download              # ~60 GB into a Volume (do this first!)
    modal deploy training/modal_app.py                     # base + ember endpoints (ember after merge)
    python -m training.gen_data --out data                 # build SFT data locally
    modal run training/modal_app.py::train --smoke         # renders template, token counts, label mask, 2 steps
    modal run training/modal_app.py::train                 # full QLoRA run on 1x H100
    modal run training/modal_app.py::merge                 # adapter -> merged bf16 checkpoint

The training config is the Muse Glimmer cookbook's Axolotl QLoRA config
(meta-models/meta-oss-cookbook, Apache-2.0), loaded at a pinned commit, with only
the dataset, batch and output settings overridden for EMBER.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import modal

BASE_MODEL = "meta-models/Muse-Glimmer-30B"
BASE_REVISION = "a4e59da52a7bc87ae7251dd5545c0dd437c44b68"   # revision pinned by the cookbook configs
COOKBOOK_REF = os.environ.get("EMBER_COOKBOOK_REF", "main")
COOKBOOK_CFG = "muse-glimmer/post-training/sft/axolotl/configs/muse-glimmer-30b-qlora.yaml"
RUN_DIR = "/runs/ember-qlora"
MERGED_DIR = f"{RUN_DIR}/merged"

app = modal.App("ember")
hf_cache = modal.Volume.from_name("ember-hf-cache", create_if_missing=True)
runs = modal.Volume.from_name("ember-runs", create_if_missing=True)
HF = "/root/.cache/huggingface"
hf_secret = modal.Secret.from_name("huggingface", required_keys=["HF_TOKEN"])
llm_secret = modal.Secret.from_name("ember-llm", required_keys=["EMBER_LLM_KEY"])

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

dl_image = modal.Image.debian_slim(python_version="3.12").pip_install("huggingface_hub[hf_transfer]==0.36.0") \
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})

# Matches the cookbook's pinned stack (torch 2.12.1 / cu130 / axolotl 0.19.0). FlashAttention and
# Cut Cross Entropy are not installed (30+ min source builds): the config drops the CCE plugin
# and sets attn_implementation=sdpa.
train_image = (
    modal.Image.from_registry("nvidia/cuda:13.0.0-devel-ubuntu24.04", add_python="3.12")
    .apt_install("git")
    .pip_install("torch==2.12.1", "torchvision==0.27.1", extra_index_url="https://download.pytorch.org/whl/cu130")
    .pip_install("axolotl==0.19.0", "pyyaml")
    .run_commands(f"git clone https://github.com/meta-models/meta-oss-cookbook /cookbook && "
                  f"cd /cookbook && git checkout {COOKBOOK_REF}")
    .env({"HF_HOME": HF})
)
if DATA_DIR.exists():
    train_image = train_image.add_local_dir(DATA_DIR, "/data")

vllm_image = modal.Image.from_registry("vllm/vllm-openai:muse-glimmer").entrypoint([]).env({"HF_HOME": HF})


@app.function(image=dl_image, volumes={HF: hf_cache}, secrets=[hf_secret], timeout=3 * 3600)
def download():
    from huggingface_hub import snapshot_download
    path = snapshot_download(BASE_MODEL, revision=BASE_REVISION, cache_dir=f"{HF}/hub")
    hf_cache.commit()
    print("weights at", path)


SEQ_LEN = 8192
CFG_PATH = "/runs/ember-qlora.yaml"


def _ember_config(smoke: bool, epochs: int) -> str:
    """Cookbook QLoRA config switched to Axolotl's text-only chat_template path.

    axolotl examples/muse-glimmer/README.md: tool data must not use the multimodal
    path (processor_type + skip_prepare_dataset drop tools/tool_calls); the text path
    needs eot_tokens for <|eot|> and <|eom|>.
    """
    import yaml
    cfg = yaml.safe_load(open(f"/cookbook/{COOKBOOK_CFG}"))
    for k in ("processor_type", "skip_prepare_dataset", "remove_unused_columns",
              "plugins", "cut_cross_entropy", "flash_attention"):
        cfg.pop(k, None)
    ds_common = {
        "type": "chat_template",
        "field_messages": "messages", "field_tools": "tools",
        "roles_to_train": ["assistant"], "train_on_eos": "turn",
    }
    cfg["datasets"] = [{**ds_common, "path": "/data/train.jsonl"}]
    cfg["test_datasets"] = [{**ds_common, "path": "/data/val.jsonl", "split": "train"}]
    cfg.update({
        "output_dir": RUN_DIR,
        "micro_batch_size": 1,
        "gradient_accumulation_steps": 8,
        "num_epochs": epochs,
        "sequence_len": SEQ_LEN,
        "attn_implementation": "sdpa",
        "eot_tokens": ["<|eot|>", "<|eom|>"],
        "train_on_inputs": False,
        "dataset_prepared_path": "/runs/prepared",
        "wandb_mode": "online" if os.environ.get("WANDB_API_KEY") else "disabled",
        "wandb_project": "ember-muse-glimmer",
    })
    if smoke:
        cfg.update({"max_steps": 2, "eval_strategy": "no", "save_strategy": "steps", "save_steps": 2})
    yaml.safe_dump(cfg, open(CFG_PATH, "w"), sort_keys=False)
    print(open(CFG_PATH).read())
    return CFG_PATH


@app.function(image=train_image, gpu="H100", volumes={HF: hf_cache, "/runs": runs},
              secrets=[hf_secret], timeout=6 * 3600)
def train(smoke: bool = False, epochs: int = 1):
    _check_data()
    cfg = _ember_config(smoke, epochs)
    runs.commit()
    if smoke:
        subprocess.run(["axolotl", "preprocess", cfg], check=True)
        _show_label_mask()
    subprocess.run(["axolotl", "train", cfg], check=True)
    runs.commit()


def _check_data():
    """Render one example and check every row fits SEQ_LEN (the collator does not truncate)."""
    import json
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(BASE_MODEL, revision=BASE_REVISION)
    lens = []
    for i, line in enumerate(open("/data/train.jsonl")):
        row = json.loads(line)
        text = tok.apply_chat_template(row["messages"], tools=row["tools"], tokenize=False)
        if i == 0:
            print("=== rendered example (head) ===\n", text[:3000], "\n=== (tail) ===\n", text[-2500:])
        lens.append(len(tok(text, add_special_tokens=False)["input_ids"]))
    lens.sort()
    print(f"tokens/example p50={lens[len(lens) // 2]} p95={lens[int(len(lens) * .95)]} max={lens[-1]}")
    probe = Path("/data/token_probe.jsonl")
    if probe.exists():
        elp = js = 0
        for line in open(probe):
            r = json.loads(line)
            elp += len(tok(r["elp"], add_special_tokens=False)["input_ids"])
            js += len(tok(r["json"], add_special_tokens=False)["input_ids"])
        print(f"ELP vs JSON telemetry, Muse Glimmer tokens: {elp} vs {js} ({js / elp:.2f}x fewer with ELP)")
    if lens[-1] > SEQ_LEN:
        raise SystemExit(f"{sum(l > SEQ_LEN for l in lens)} examples exceed {SEQ_LEN} tokens")


def _show_label_mask():
    """Decode the trained (label != -100) spans of one preprocessed example."""
    from datasets import load_from_disk
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(BASE_MODEL, revision=BASE_REVISION)
    dirs = [p.parent for p in Path("/runs/prepared").rglob("dataset_info.json")]
    if not dirs:
        print("no prepared dataset found under /runs/prepared")
        return
    ex = load_from_disk(str(dirs[0]))[0]
    spans, cur = [], []
    for tid, lab in zip(ex["input_ids"], ex["labels"]):
        if lab != -100:
            cur.append(tid)
        elif cur:
            spans.append(tok.decode(cur))
            cur = []
    if cur:
        spans.append(tok.decode(cur))
    print(f"=== {len(spans)} trained spans (expect one per assistant message) ===")
    for sp in spans:
        print("---", sp[:400])


@app.function(image=train_image, gpu="H100", memory=200 * 1024, volumes={HF: hf_cache, "/runs": runs},
              secrets=[hf_secret], timeout=3 * 3600)
def merge():
    subprocess.run(["axolotl", "merge-lora", CFG_PATH, f"--lora-model-dir={RUN_DIR}"], check=True)
    # vLLM needs the checkpoint's chat template next to the merged weights
    from huggingface_hub import snapshot_download
    base = Path(snapshot_download(BASE_MODEL, revision=BASE_REVISION))
    for f in ("config.json", "chat_template.jinja", "generation_config.json", "tokenizer_config.json", "tokenizer.json",
              "special_tokens_map.json", "preprocessor_config.json"):
        if (base / f).exists() and not (Path(MERGED_DIR) / f).exists():
            subprocess.run(["cp", str(base / f), MERGED_DIR], check=True)
    runs.commit()
    print("merged checkpoint at", MERGED_DIR)


def _vllm(model: str, name: str, revision: str = ""):
    cmd = ["vllm", "serve", model, "--served-model-name", name,
           *(["--revision", revision] if revision else []),
           "--host", "0.0.0.0", "--port", "8000", "--tensor-parallel-size", "1",
           "--enable-auto-tool-choice", "--tool-call-parser", "muse_glimmer",
           "--reasoning-parser", "muse_glimmer", "--generation-config", "auto",
           "--max-model-len", "8192", "--gpu-memory-utilization", "0.92",
           "--api-key", os.environ["EMBER_LLM_KEY"]]
    subprocess.Popen(cmd)


_serve = dict(image=vllm_image, gpu="H100", volumes={HF: hf_cache, "/runs": runs},
              secrets=[hf_secret, llm_secret], timeout=24 * 3600, scaledown_window=20 * 60)


@app.function(**_serve)
@modal.concurrent(max_inputs=16)
@modal.web_server(port=8000, startup_timeout=30 * 60)
def serve_base():
    """Stock Muse Glimmer (served-model-name: muse-glimmer)."""
    _vllm(BASE_MODEL, "muse-glimmer", BASE_REVISION)


@app.function(**_serve)
@modal.concurrent(max_inputs=16)
@modal.web_server(port=8000, startup_timeout=30 * 60)
def serve_ember():
    """Fine-tuned EMBER checkpoint (served-model-name: ember)."""
    _vllm(MERGED_DIR, "ember")
