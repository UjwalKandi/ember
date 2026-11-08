# Lab 03: Agents and the model bake-off

**Goal:** run small models as the EMBER agent, measure them on the same evaluation, understand *why* they fail, and **choose the student model** you'll fine-tune in Lab 04.

## Concepts (why)
- **The agent loop** (`gateway/agent.py`):
  1. The model gets the system prompt plus the swarm status.
  2. It replies with tool calls (`request_confirm`, `ping_node`, `get_node_history`, `set_sample_rate`, `tune_detectors`, `broadcast_alert`, `submit_assessment`).
  3. EMBER executes them on the simulated or real swarm and appends the results.
  4. Repeat until the model writes its final SITREP.

  If it answers in prose without calling `submit_assessment`, the loop nudges it once. The loop stops after 16 turns.
- **Parsing:** servers usually return structured `tool_calls`. If they don't, `gateway/llm.py` tries two text formats itself (ATEM XML and `<tool_call>{json}</tool_call>`). The eval counts how often this fallback was needed (`fallback_parse_rate`), because a model that relies on it is fragile.
- **Evals over vibes:** a few nice-looking transcripts prove nothing. `training/eval.py` replays fixed, held-out simulated episodes and scores every run the same way.
- **Failure taxonomy:** each episode gets exactly one label (`failure_type` in `training/eval.py`):

  | Label | Meaning |
  |---|---|
  | `ok` | verdict and cause correct |
  | `wrong_decision` | submitted an assessment, but it was wrong |
  | `turn_limit` | ran out of turns |
  | `bad_tool_calls` | unknown tools or invalid arguments |
  | `no_assessment` | never called `submit_assessment` (answered in prose) |
  | `crash/timeout` | the request errored or timed out |

  These tell you what to fix: tool-use competence (fixable with prompts, templates or SFT) vs decision quality (mostly SFT and RL).

## Steps

### 1. Watch a local model run the swarm
Terminal 1, the model server (downloads ~2 GB the first time):
```bash
cd ~/Documents/meta/ember && source .venv/bin/activate
scripts/serve_gguf.sh Qwen/Qwen3-1.7B-GGUF:Q8_0
```
Terminal 2, the dashboard:
```bash
cd ~/Documents/meta/ember && source .venv/bin/activate
export EMBER_LLM_URL=http://localhost:8081/v1 EMBER_LLM_MODEL=local
export EMBER_LLM_EXTRA='{"chat_template_kwargs":{"enable_thinking":false}}'
python -m gateway.server --sim fog --agent llm
```
Open http://localhost:8080 and click **Assess now**.

✅ The header says the agent is the LLM, and the trace panel shows the model's own tool calls and the results coming back. Each turn takes a few seconds.

`EMBER_LLM_EXTRA` is added to every request. Here it turns off Qwen3's long "thinking" output, which is slow and eats the token budget.

Stop both with Ctrl+C when done.

### 2. List the candidates
```bash
python training/bakeoff.py --list
```
The candidates come from `config/models.json`:
- Qwen3 0.6B / 1.7B / 4B
- Llama 3.2 1B / 3B
- Gemma 3 4B
- SmolLM3 3B
- webAI's **TwIL-LM3** and **TwIL-8B**
- Muse Glimmer 30B, as a large reference

**Verify before you rely on them.** Entries whose notes say "verify" couldn't be checked from Hugging Face when the file was written. Open each repo, confirm the GGUF and quant exist, and **read the license**.
- **TwIL-LM3** is described in a third-party README as a 3B reasoning model derived from **SmolLM3-3B**, under a **"WebAI NonCommercial"** license. That's fine for learning, but a fine-tune of it can't be used commercially. Confirm on the official model card.
- **smollm3-3b vs twil-lm3** is a neat A/B test: same base model, with and without webAI's extra training.
- **TwIL-8B** and **Muse Glimmer 30B** are heavy. They're optional for the bake-off, and only trainable via Kaggle.

### 3. Run the bake-off
```bash
python training/bakeoff.py --models qwen3-0.6b,qwen3-1.7b,qwen3-4b,smollm3-3b,twil-lm3 --limit 1
```
For each model it:
1. starts `llama-server` (downloading the GGUF on first use),
2. waits for `/health`,
3. runs `python -m training.eval --agent llm … --limit 1 --workers 1` (1 episode × 14 scenarios),
4. stops the server.

Server logs go to `results/logs/llama-server-<name>.log`, and per-model results to `results/bakeoff-<name>.json`.

⏱ Expect roughly 10–60 minutes per model, depending on size and how much it "thinks". Downloads come on top. Starting with 2–3 models is fine. Useful flags: `--ctx` (context size, default 16384) and `--download-timeout` (seconds, default 1800).

✅ It ends with `report: …/results/bakeoff.md`.

### 4. Read the report
```bash
cat results/bakeoff.md
```
Columns: `valid` (did it call `submit_assessment`), `verdict`, `cause`, `false alarms`, `fallback parse`, `sec/episode`, `tok/s`, and `failures` (the taxonomy counts).

**Choose your student model:**
1. **License** you're happy with.
2. **Tool-use competence:** high `valid`, few `bad_tool_calls` / `no_assessment`, low `fallback parse`.
3. **Size ≤ 4B**, so LoRA training fits on your Mac (Lab 04). 7–8B is Kaggle-only.
4. **Speed** that keeps evals practical.

Don't worry if verdict/cause accuracy is low: that's what fine-tuning fixes. Poor tool-use competence is harder to fix.

Also run the two reference points once, since Labs 05–06 compare against them:
```bash
python -m training.eval --agent heuristic
python -m training.eval --agent oracle
```

## Exercises
1. **Prompt engineering vs fine-tuning:** change one thing in `gateway/prompts.py` (e.g. a clearer procedure or an example), re-run the bake-off for your chosen model, and compare. Keep a note of how much prompts alone can buy.
2. Re-run one model with `--limit 2`. How much do the numbers move? (This is variance; Lab 06 goes deeper.)
3. Pick one failed episode in `results/bakeoff-<name>.json` (`rows` → `failure`). Reproduce the scenario in the dashboard and watch where it goes wrong.
4. TwIL-LM3 reasons in `<think>` blocks. Does that help decisions? What does it cost in `sec/episode`?

## Checkpoint
- [ ] `results/bakeoff.md` exists for at least 3 models
- [ ] You can explain each failure label and which fix it calls for
- [ ] You've chosen a student model (≤ 4B) and written down why in your learning log

## Interview questions
- How do you evaluate an agent rather than a single model answer?
- What's the difference between tool-use competence and task competence, and which is easier to fix with SFT?
- Why is "it looked good in a demo" not evidence?
