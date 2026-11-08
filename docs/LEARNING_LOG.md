# Learning log

Your portfolio of the EMBER labs. Fill one section per lab: what you ran, the numbers, what surprised you, and what you'd try next. Concrete numbers and honest conclusions are what interviewers ask about.

Tip: paste tables straight from `results/bakeoff.md`, `python -m training.eval --report …` and your loss plots (`runs/*/metrics.png`).

## Skills checklist
See [labs/README.md](../labs/README.md#skills-checklist-what-an-sftrl-engineer-is-expected-to-know) and tick items here as you master them.

- [ ] GGUF + quantization trade-offs (01)
- [ ] Tokenizers, chat templates, tool-call formats (02)
- [ ] Loss masking (02, 04)
- [ ] Agent loop + failure taxonomy (03)
- [ ] Model selection from measurements (03)
- [ ] LoRA SFT + reading loss curves (04)
- [ ] Overfit-one-batch sanity check (04)
- [ ] Merge / convert / quantize / verify (05)
- [ ] Fair evaluation design (05, 06)
- [ ] DPO (07)
- [ ] GRPO + reward design (08)
- [ ] Edge deployment (09)

---

## Lab 00: Setup
| Date | What I did | Numbers | What surprised me | Next |
|---|---|---|---|---|
| | | | | |

## Lab 01: llama.cpp internals
| Quant | File size | pp512 tok/s | tg128 tok/s | Perplexity |
|---|---|---|---|---|
| F16 | | | | |
| Q8_0 | | | | |
| Q4_K_M | | | | |

| Date | What I did | What surprised me | Next |
|---|---|---|---|
| | | | |

## Lab 02: Tokenizers + templates
| Model | template_mode | trajectory tokens | trained tokens % | tool-call format |
|---|---|---|---|---|
| | | | | |

Notes:

## Lab 03: Agents + bake-off
Paste `results/bakeoff.md` here.

| Date | Chosen student model | Why (license, tool-use, size, speed) |
|---|---|---|
| | | |

Prompt-engineering experiment (what I changed → effect):

## Lab 04: SFT with LoRA
| Run | model | rank | alpha | lr | epochs | final train loss | final eval loss | time | notes |
|---|---|---|---|---|---|---|---|---|---|
| overfit check | | | | | | | — | | |
| baseline | | | | | | | | | |

Loss curve: `runs/<name>/metrics.png`

What surprised me / what I'd try next:

## Lab 05: Export + evaluate
Paste the `--report` table (heuristic vs base vs SFT vs oracle).

| Model | Quant | verdict acc | cause acc | false alarms | sec/episode |
|---|---|---|---|---|---|
| | | | | | |

Conclusion: did SFT help, where, and by how much?

## Lab 06: Evaluation science
## Lab 07: DPO
## Lab 08: GRPO
## Lab 09: Edge

---

## Portfolio summary
Write this last, in 5–8 bullet points, for your CV or LinkedIn:
- **Project:** EMBER, an agentic sensor mesh: a fine-tuned small language model runs a swarm of ESP32 camera nodes over a radio mesh.
- **What I built:**
- **Models and methods:** (e.g. "LoRA SFT of a 1.7B model on 570 simulator-generated agent trajectories")
- **Results:** (numbers from Lab 05: base → SFT, vs rules, vs oracle)
- **Deployment:** (GGUF quant, tok/s on an M2, edge device)
- **What I learned:**
- **Links:** https://github.com/UjwalKandi/ember
