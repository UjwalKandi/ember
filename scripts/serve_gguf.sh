#!/usr/bin/env bash
# Serve any GGUF model with llama.cpp as an OpenAI-compatible API on http://localhost:8081/v1
# (the EMBER dashboard uses 8080). Free, runs on the Mac's GPU via Metal.
#
#   scripts/serve_gguf.sh runs/qwen3-1.7b-lora/model-Q4_K_M.gguf          # a local file
#   scripts/serve_gguf.sh Qwen/Qwen3-1.7B-GGUF:Q4_K_M                      # straight from Hugging Face
#   scripts/serve_gguf.sh Qwen/Qwen3-1.7B-GGUF:Q4_K_M --lora runs/x/lora.gguf
# Extra flags are passed to llama-server (e.g. -c 8192, --reasoning-budget 512).
set -euo pipefail
MODEL=${1:?usage: serve_gguf.sh <file.gguf | hf-repo:quant> [llama-server flags]}
shift
if [ -f "$MODEL" ]; then SRC=(-m "$MODEL"); else SRC=(-hf "$MODEL"); fi
exec llama-server "${SRC[@]}" --alias local --port "${EMBER_LLM_PORT:-8081}" -c 16384 -np 1 --jinja "$@"
