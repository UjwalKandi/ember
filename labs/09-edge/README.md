# Lab 09: Edge deployment (coming next)

**Status:** outline.

**Goal:** run your student model and EMBER's detectors on small devices, and understand the edge constraints: memory, speed, power, offline operation.

## Concepts
- **Quantized SLM on edge hardware:** a Q4_K_M 0.6–1.7B model runs with llama.cpp on a Raspberry Pi 4/5 (4 GB+) or a phone (e.g. PocketPal / llama.cpp-based apps). Trade-offs: tok/s, context size, quant level.
- **What an ESP32 can and can't do:** a XIAO ESP32-S3 has ~8 MB PSRAM, which is far too little for any language model. On the arms, edge AI means **TinyML**. That has its own lab: [Lab 10](../10-tinyml/README.md) trains EMBER's on-device attack classifier.

## Planned steps
1. Run your Lab 05 GGUF on a Raspberry Pi (if available) with `llama-server` and point the dashboard at it.
2. Measure tok/s and sec/episode on the device vs the Mac.
3. Point the dashboard's "Write report" (L3) at the device-hosted model and compare answer faithfulness with the Mac-hosted one.

## Interview questions
- How do you choose a quantization level for a memory-limited device?
- What changes when a model must run offline on battery power?
