# EMBER LLM labs: from running a model to SFT and RL

> **v2 pivot (2026-11-08):** labs 00–02 apply unchanged. Labs 03–08 are being re-targeted from the wildfire decision agent to the v2 assistant roles: grounded incident Q&A (SFT → DPO), policy → config with a verifier reward (GRPO), and a novelty-triage agent. In v2 the LLM never makes the alert decision. See [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md#6-assistant-local-slm-never-in-the-decision-path).

A hands-on course built on this repo. You learn by doing real LLM engineering on a real system: a small language model (SLM) that runs on your Mac becomes the brain of the EMBER sensor swarm. You run it, measure it, fine-tune it, ship it back to llama.cpp, and later improve it with preference tuning and reinforcement learning.

**Budget: $0.** Everything runs on your M2 MacBook (24 GB) with free, open-source tools. A free Kaggle GPU is needed only for the optional 7–8B work.

## The two halves of the job

| Half | What it means | Tools you'll use |
|---|---|---|
| **Train** | change the model's weights: SFT, LoRA, DPO, GRPO | PyTorch, Hugging Face `transformers`, `peft`, `trl` (the industry-standard stack) |
| **Ship** | convert, quantize, serve, evaluate | **llama.cpp**: `convert_hf_to_gguf.py`, `llama-quantize`, `llama-bench`, `llama-server` |

llama.cpp is the best tool for running and shipping models on laptops and edge devices. Training happens in the PyTorch ecosystem, and that's what job descriptions for "SFT / RL engineer" ask for. In these labs you do both, in a loop: train → convert → serve → measure → train again.

## Curriculum map

| Lab | Topic | You build / run | Status |
|---|---|---|---|
| [00](00-setup/README.md) | Setup + cleanup | clean environment, llama.cpp, training deps, datasets | ready |
| [01](01-llamacpp/README.md) | llama.cpp internals | HF weights → GGUF → Q8_0 / Q4_K_M, benchmark speed and perplexity, serve with tool calling | ready |
| [02](02-templates/README.md) | Tokenizers + chat templates | see how each model family serializes tools; understand loss masking | ready |
| [03](03-agents-bakeoff/README.md) | Agents + model bake-off | run small models as the EMBER agent; measure them; **pick your student model** | ready |
| [04](04-sft-lora/README.md) | SFT with LoRA | fine-tune the student on EMBER trajectories on your Mac; read loss curves | ready |
| [05](05-export-eval/README.md) | Export + evaluate | merge/convert/quantize the fine-tune, serve it with llama.cpp, compare against the base | ready |
| [06](06-eval-science/README.md) | Evaluation science | seeds, confidence intervals, held-out compositions, error analysis | outline |
| [07](07-dpo/README.md) | Preference tuning (DPO) | learn from the model's own mistakes | outline |
| [08](08-grpo/README.md) | RL with verifiable rewards (GRPO) | the simulator scores answers; train with rewards | outline |
| [09](09-edge/README.md) | Edge deployment | quantized student on a Pi/phone; TinyML on the ESP32 | outline |

## How the labs build on each other
```
00 setup ─► 01 llama.cpp ─► 02 templates ─► 03 bake-off ──► 04 SFT ──► 05 export + eval ─┬─► 06 eval science
                 (ship half)    (data half)     (choose model)  (train half)  (close the loop) ├─► 07 DPO
                                                                                           ├─► 08 GRPO
                                                                                           └─► 09 edge
```
- Lab 01 teaches the shipping tools you reuse in 03 and 05.
- Lab 02 explains what the trainer in 04 actually feeds the model.
- Lab 03 gives you the **baseline numbers** that 05 must beat.
- Labs 07–08 start from your SFT model from 04.

## Skills checklist (what an SFT/RL engineer is expected to know)
Tick these off in [docs/LEARNING_LOG.md](../docs/LEARNING_LOG.md) as you go.

- [ ] Explain GGUF, quantization types and their speed/quality trade-off; measure tok/s and perplexity (01)
- [ ] Explain tokenizers, special tokens, chat templates and tool-call formats (02)
- [ ] Explain why SFT masks the prompt and trains only on assistant tokens (02, 04)
- [ ] Build and debug an agent loop with tool calling; categorize failures (03)
- [ ] Choose a model from measurements, not leaderboards (03)
- [ ] Run LoRA SFT, explain rank/alpha/lr/effective batch, spot overfitting from curves (04)
- [ ] Run a sanity "overfit one batch" check before a real run (04)
- [ ] Merge an adapter, convert to GGUF, quantize, and verify quality didn't regress (05)
- [ ] Design a fair eval: same episodes, held-out data, failure taxonomy, variance (05, 06)
- [ ] Build preference pairs and run DPO (07)
- [ ] Write a verifiable reward and run GRPO; recognize reward hacking (08)
- [ ] Deploy a quantized model on an edge device (09)

## How to work through a lab
1. Read **Concepts** first. Each lab explains *why* before *how*.
2. Run the **Steps** exactly. Each step says what you should see.
3. Do the **Exercises**. This is where most of the learning happens.
4. Write the numbers and surprises in [docs/LEARNING_LOG.md](../docs/LEARNING_LOG.md). It becomes your portfolio.
5. Try the **Interview questions** out loud.

All commands assume you're in the repo with the venv active:
```bash
cd ~/Documents/meta/ember && source .venv/bin/activate
```

Results depend on your machine, the exact model files, and randomness, so your numbers will differ from any examples here. Measuring them yourself is the point.
