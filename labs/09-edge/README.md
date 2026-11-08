# Lab 09: Edge deployment (coming next)

**Status:** outline.

**Goal:** run your student model and EMBER's detectors on small devices, and understand the edge constraints: memory, speed, power, offline operation.

## Concepts
- **Quantized SLM on edge hardware:** a Q4_K_M 0.6–1.7B model runs with llama.cpp on a Raspberry Pi 4/5 (4 GB+) or a phone (e.g. PocketPal / llama.cpp-based apps). Trade-offs: tok/s, context size, quant level.
- **What an ESP32 can and can't do:** a XIAO ESP32-S3 has ~8 MB PSRAM, which is far too little for any language model. On the nodes, edge AI means **TinyML**: tiny vision/audio models (TensorFlow Lite Micro, tens of KB) replacing hand-written rules.
- **EMBER's current node detectors** are hand-tuned rules (scene-relative flame pixels + a flicker test; see [docs/HARDWARE_BRINGUP.md](../../docs/HARDWARE_BRINGUP.md)). A trained classifier is the natural upgrade.

## Planned steps
1. Run your Lab 05 GGUF on a Raspberry Pi (if available) with `llama-server` and point the dashboard at it.
2. Measure tok/s and sec/episode on the device vs the Mac.
3. Collect labelled camera frames from the XIAO (flame / lamp / glare / nothing) and train a tiny CNN; convert to TFLite Micro.
4. Replace or augment the flame rule in `firmware/src/sense_cam.cpp` and compare false alarms with the current flicker test.

## Interview questions
- How do you choose a quantization level for a memory-limited device?
- What changes when a model must run offline on battery power?
