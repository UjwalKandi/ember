#!/usr/bin/env bash
# Export a fine-tuned model to GGUF for llama.cpp (Lab 05). Two routes:
#
#   merged:  training/export_gguf.sh merged  runs/qwen3-1.7b-lora/adapter  Q4_K_M
#            LoRA merged into the base -> F16 GGUF -> quantized GGUF (one self-contained file)
#   adapter: training/export_gguf.sh adapter runs/qwen3-1.7b-lora/adapter
#            only the LoRA as a small GGUF; load it on top of a base GGUF with --lora
#
# Needs: brew install llama.cpp (llama-quantize) and the llama.cpp repo for its Python
# converters (cloned into .llama.cpp on first run).
set -euo pipefail
MODE=${1:?usage: export_gguf.sh merged|adapter <adapter_dir> [QUANT]}
ADAPTER=${2:?adapter dir}
QUANT=${3:-Q4_K_M}
RUN=$(dirname "$ADAPTER")
[ -d .llama.cpp ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp .llama.cpp
python -m pip install -q -r .llama.cpp/requirements/requirements-convert_hf_to_gguf.txt

if [ "$MODE" = merged ]; then
  python training/merge_lora.py --adapter "$ADAPTER" --out "$RUN/merged"
  python .llama.cpp/convert_hf_to_gguf.py "$RUN/merged" --outtype f16 --outfile "$RUN/model-f16.gguf"
  llama-quantize "$RUN/model-f16.gguf" "$RUN/model-$QUANT.gguf" "$QUANT"
  ls -lh "$RUN"/*.gguf
  echo "serve: scripts/serve_gguf.sh $RUN/model-$QUANT.gguf"
else
  BASE=$(python -c "import json,sys;print(json.load(open('$ADAPTER/adapter_config.json'))['base_model_name_or_path'])")
  python .llama.cpp/convert_lora_to_gguf.py --base-model-id "$BASE" --outfile "$RUN/lora.gguf" "$ADAPTER"
  ls -lh "$RUN/lora.gguf"
  echo "serve: scripts/serve_gguf.sh <base .gguf or hf-repo:quant> --lora $RUN/lora.gguf"
fi
