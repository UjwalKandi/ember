#!/usr/bin/env bash
# Convert a trained EMBER LoRA adapter (from training/kaggle_qlora.py) into a llama.cpp
# GGUF adapter, so it runs on top of the free local base model:
#   scripts/lora_to_gguf.sh ./ember-lora ember-lora.gguf
set -euo pipefail
ADAPTER=${1:?usage: scripts/lora_to_gguf.sh <adapter_dir> [out.gguf]}
OUT=${2:-ember-lora.gguf}
[ -d .llama.cpp ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp .llama.cpp
python -m pip install -q -r .llama.cpp/requirements/requirements-convert_lora_to_gguf.txt
# --base-model-id fetches only the base config/tokenizer from Hugging Face, not the weights
python .llama.cpp/convert_lora_to_gguf.py --base-model-id meta-models/Muse-Glimmer-30B --outfile "$OUT" "$ADAPTER"
echo
echo "Done. Run the fine-tuned model locally with:"
echo "  scripts/run_local_model.sh --lora $OUT"
