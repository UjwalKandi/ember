#!/usr/bin/env bash
# Serve Muse Glimmer 30B locally with llama.cpp (free, Metal on Apple silicon) as an
# OpenAI-compatible API on http://localhost:8081/v1 (the dashboard uses 8080).
# Extra args are passed through, e.g.:  scripts/run_local_model.sh --lora ember-lora.gguf
#
# First run downloads the ~17 GB GGUF from Hugging Face. On a 24 GB Mac, let Metal use more
# memory first (resets on reboot):  sudo sysctl iogpu.wired_limit_mb=20480
set -euo pipefail
QUANT=${EMBER_QUANT:-kquant-17gb}      # smallest published quant; Q4_K_M / Q4_K_XL also exist
exec llama-server -hf "meta-models/Muse-Glimmer-30B-GGUF:${QUANT}" --alias muse-glimmer \
  --port 8081 -c 16384 -np 1 --jinja "$@"
