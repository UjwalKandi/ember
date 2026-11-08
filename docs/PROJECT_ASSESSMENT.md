# EMBER project assessment (updated 2026-11-07)

> **Superseded on 2026-11-08** by the v2 pivot to a passive wireless-security mesh: [ASSESSMENT_V2.md](ASSESSMENT_V2.md), [OBJECTIVES.md](OBJECTIVES.md). The cost and free-tooling decisions below still apply.

Post-hackathon review of every phase and component, and the switch to **free / open-source resources only**.

## Phases
| Phase | Status | Notes |
|---|---|---|
| A Laptop setup | ✅ done | Python 3.12 `.venv` (system 3.9 is too old for current tooling), PlatformIO, git identity |
| B Accounts | ✅ simplified | only a free Hugging Face token; Modal removed (cleanup script provided) |
| C1 Single board | ✅ passed | [HARDWARE_BRINGUP.md](HARDWARE_BRINGUP.md): self-test, flicker-gated flame detection, dashboard over USB |
| C2 Replication + radio | ⏭ next | 4 boards: nodes 1–3 + gateway |
| D LLM labs | ⏭ in progress | Goal changed to *learning SFT/RL with small local models*: [labs/](../labs/README.md). LoRA SFT on the Mac (≤4B), free Kaggle QLoRA for 7–8B, llama.cpp for export/serving/eval |
| F/G Demo + submission | ✅ done | |

## Components and cost
| Component | License / cost | Verdict |
|---|---|---|
| ELP protocol, firmware, simulator, gateway, dashboard, tests | our code (MIT), stdlib Python | ✅ keep |
| PlatformIO + pioarduino, Arduino-ESP32, esp32-camera | open source | ✅ keep |
| GitHub repo + Actions + Pages (public repo) | free | ✅ keep (firmware CI + browser flasher) |
| ESP Web Tools, esptool, Web Serial console | open source | ✅ keep |
| Hugging Face Hub (model + GGUF downloads) | free | ✅ keep |
| **llama.cpp** (convert, quantize, bench, serve; Metal) | MIT, free | ✅ **new**: replaces vLLM |
| **PyTorch + transformers + PEFT + TRL** (Mac MPS) | open source | ✅ **new**: SFT/DPO/GRPO training stack |
| bitsandbytes QLoRA on **Kaggle** (7–8B only) | free GPU quota | ✅ kept for big models |
| XIAO ESP32-S3 Sense ×4, M2 laptop, custom domain | already owned | ✅ |
| Modal (H100 training, vLLM serving, volumes) | paid | ❌ removed from the repo; `scripts/cleanup_modal.sh` deletes volumes/secrets |
| vLLM `muse-glimmer` image | free software, needs a ≥72 GB GPU | ❌ dropped (no free GPU that large) |
| Together AI / hosted APIs | paid | ❌ not used |

**Housekeeping:** `scripts/cleanup_modal.sh` (Modal volumes, secrets, apps, client) and `scripts/cleanup_local.sh` (old Python 3.9 installs, caches, regenerable data).

## $0 architecture
```
XIAO nodes ⇄ ESP-NOW mesh ⇄ gateway ⇄ USB ⇄ dashboard (Python, stdlib)
                                                │ OpenAI-compatible HTTP (localhost:8081)
                                                ▼
                        llama.cpp llama-server on the M2 (Metal)
                        small model GGUF (0.6–4B student) [+ EMBER LoRA .gguf after SFT]

Training: PEFT LoRA on the Mac (MPS, ≤4B) or free Kaggle QLoRA (7–8B) → merge or LoRA GGUF → llama.cpp
```

## Risks to watch
- **Mac memory for training:** LoRA on a 4B model in bf16 needs ~10–14 GB; keep `--max-len` ≤4096 and close heavy apps. 7–8B training goes to Kaggle.
- **Local speed:** small models answer in seconds, but evals still run on reduced sets (`--limit 1–2` = 14–28 episodes). Reasoning models (TwIL, Qwen3 with thinking) are much slower unless thinking is capped.
- **Model claims:** benchmark claims (e.g. TwIL-LM3 vs gpt-oss-120B) don't transfer automatically to EMBER's task; trust the bake-off.
- **Kaggle (7–8B only):** fp16 stability on T4 and the 30 h/week quota. Mitigations are in [training/KAGGLE.md](../training/KAGGLE.md).

## Model candidates (bake-off, Lab 03)
| Model | Size | License | Why it's in the bake-off |
|---|---|---|---|
| Qwen3 0.6B / 1.7B / 4B | 0.6–4B | Apache-2.0 | native tool calling, thinking can be switched off, fast iteration and RL |
| Llama 3.2 1B / 3B Instruct | 1–3B | Llama 3.2 (gated) | Meta family, tool calling |
| Gemma 3 4B | 4B | Gemma (gated) | no native tool template, so it tests EMBER's text fallback |
| SmolLM3-3B | 3B | Apache-2.0 | fully open; base of TwIL-LM3 (clean A/B) |
| **webAI TwIL-LM3** | 3B | WebAI NonCommercial (per a third-party README; verify) | reasoning SLM for formal logic / tool-call verification; Q4 GGUF ~1.9 GB; runs on phones/RPi |
| **webAI TwIL-8B** | 8B | verify on model card | larger webAI model; serve on the Mac, fine-tune only via Kaggle QLoRA |
| Muse Glimmer 30B | 30B | Apache-2.0 | large reference (~17 GB GGUF), not a fine-tuning target here |

The webAI pages couldn't be opened from the assistant's sandbox, so confirm license and GGUF availability on the model cards before relying on them. The choice is made by measurement (`training/bakeoff.py`), not by benchmark claims.
