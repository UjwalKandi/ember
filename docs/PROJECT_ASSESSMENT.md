# EMBER project assessment (2026-11-05)

Post-hackathon review of every phase and component, and the switch to **free / open-source resources only**.

## Phases
| Phase | Status | Notes |
|---|---|---|
| A Laptop setup | ✅ done | Python 3.12 `.venv` (system 3.9 is too old for current tooling), PlatformIO, git identity |
| B Accounts | ⚠️ partial | HF token ✅ (Muse Glimmer is Apache-2.0, not gated). Modal: no credits, so it's no longer on the critical path |
| C1 Single board | ✅ passed | [HARDWARE_BRINGUP.md](HARDWARE_BRINGUP.md): self-test, flicker-gated flame detection, dashboard over USB |
| C2 Replication + radio | ⏭ next | 4 boards: nodes 1–3 + gateway |
| D Fine-tune | 🔁 reworked | Was Axolotl on a paid H100 (Modal). Now QLoRA on **Kaggle's free 2×T4**: [training/KAGGLE.md](../training/KAGGLE.md) |
| E Serve + eval | 🔁 reworked | Was vLLM on a 72 GB GPU. Now **llama.cpp on the M2** (`scripts/run_local_model.sh`), evaluated on a reduced set |
| F/G Demo + submission | ✅ done | |

## Components and cost
| Component | License / cost | Verdict |
|---|---|---|
| ELP protocol, firmware, simulator, gateway, dashboard, tests | our code (MIT), stdlib Python | ✅ keep |
| PlatformIO + pioarduino, Arduino-ESP32, esp32-camera | open source | ✅ keep |
| GitHub repo + Actions + Pages (public repo) | free | ✅ keep (firmware CI + browser flasher) |
| ESP Web Tools, esptool, Web Serial console | open source | ✅ keep |
| Hugging Face Hub (model + GGUF downloads) | free | ✅ keep |
| **llama.cpp** (local inference, Metal, ATEM tool-call parser upstream) | MIT, free | ✅ **new**: replaces vLLM |
| **transformers + PEFT + bitsandbytes on Kaggle 2×T4** | open source + free GPU quota | ✅ **new**: replaces Axolotl on Modal |
| llama.cpp `convert_lora_to_gguf.py` | MIT | ✅ **new**: adapter → local model |
| XIAO ESP32-S3 Sense ×4, M2 laptop, custom domain | already owned | ✅ |
| Modal (H100 training, vLLM serving, volumes) | paid | ❌ dropped; `training/modal_app.py` kept as an optional, paid path |
| vLLM `muse-glimmer` image | free software, needs a ≥72 GB GPU | ❌ dropped (no free GPU that large) |
| Together AI / hosted APIs | paid | ❌ not used |

**Housekeeping:** if the Modal weights download finished, delete the volume to avoid storage charges: `python -m modal volume delete ember-hf-cache`.

## $0 architecture
```
XIAO nodes ⇄ ESP-NOW mesh ⇄ gateway ⇄ USB ⇄ dashboard (Python, stdlib)
                                                │ OpenAI-compatible HTTP (localhost:8081)
                                                ▼
                        llama.cpp llama-server on the M2 (Metal)
                        Muse Glimmer 30B GGUF (kquant-17gb) [+ EMBER LoRA .gguf after training]

Training: Kaggle notebook (2×T4, 4-bit QLoRA, assistant-only loss) → adapter → convert_lora_to_gguf → --lora
```

## Risks to watch
- **24 GB Mac memory:** the ~17 GB model plus KV cache is tight. Raise the Metal limit (`sudo sysctl iogpu.wired_limit_mb=20480`), close heavy apps, keep context at 16k.
- **Local speed:** a 30B model on an M2 takes tens of seconds per agent turn. The evaluation runs on a reduced set (`--limit 2` = 28 episodes) overnight, not all 420.
- **Kaggle:** disk for the 60 GB checkpoint, fp16 stability on T4, and the 30 h/week quota. Mitigations are in [training/KAGGLE.md](../training/KAGGLE.md).
