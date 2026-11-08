# Lab 02: Tokenizers and chat templates

**Goal:** see exactly what text and tokens a model is trained on and prompted with, how different model families format tools and tool calls, and why SFT trains only on the assistant's tokens.

## Concepts (why)
- **Tokenizer:** turns text into integer IDs. Each family has its own vocabulary, so the same EMBER conversation costs a different number of tokens per model.
- **Special tokens:** markers like `<|im_start|>`, `<|eot_id|>` or `<start_of_turn>` that separate roles and end turns. A model only behaves well if these are exactly what it saw in training.
- **Chat template:** a small Jinja program, shipped with the tokenizer, that turns a list of messages (system / user / assistant / tool) plus tool schemas into one string. **The same conversation looks completely different per family.** Qwen wraps calls in `<tool_call>…</tool_call>`, Llama uses its own JSON style, and some templates (e.g. Gemma's) have no tool support at all.
- **EMBER's two modes** (`training/sft_data.py::prepare`):
  - `native`: the template renders the tool schemas and tool calls itself. EMBER prefers this.
  - `text`: the template can't. EMBER moves the tool list into the system prompt, writes calls as `<tool_call>{"name": ..., "arguments": {...}}</tool_call>` text, and turns tool results into user turns. At inference time, `gateway/llm.py::parse_text_calls` reads that format back.
- **Loss masking:** in SFT the loss is computed only on what the assistant writes (its tool calls and the final SITREP). Everything else (system prompt, swarm status, tool results) gets label `-100` and is ignored. Otherwise the model would spend capacity learning to reproduce its inputs instead of learning to *act*.
  `sft_data.build_example` finds each assistant message's character range by rendering the conversation up to and including that message, then maps those characters onto tokens. That way the mask always follows the real template.

## Steps

### 1. Log in for gated models (once)
Llama and Gemma require accepting their license on Hugging Face.
1. Open https://huggingface.co/google/gemma-3-4b-it and click to accept (same for Llama 3.2 if you want it).
2. Create a Read token (Settings → Access Tokens) and run:
   ```bash
   huggingface-cli login
   ```
Qwen and SmolLM are ungated; you can skip this if you only use those.

### 2. Compare three templates
```bash
cd ~/Documents/meta/ember && source .venv/bin/activate
python training/inspect_templates.py --models Qwen/Qwen3-0.6B,HuggingFaceTB/SmolLM3-3B,google/gemma-3-4b-it
```
This only downloads tokenizers (a few MB each). For each model it prints:
- `template_mode=native|text`
- vocabulary size, BOS/EOS tokens
- token counts for the system prompt, the swarm context, the tool schemas, the whole trajectory, and the trained part
- the rendered text around the first tool call

✅ The tool calls are formatted differently per model. At least one model is likely to show `template_mode=text`; read how EMBER rewrote that conversation.

Useful flags: `--row 5` picks another example, `--chars 4000` shows more text, `--data data/train.jsonl` uses the full set.

### 3. See the loss mask
```bash
python training/sft_lora.py --model Qwen/Qwen3-0.6B --data data-small/train.jsonl --dry-run
```
✅ You see:
- `template_mode=…`
- `examples=570 kept=… dropped=…`. Dropped examples were too long for `--max-len` (default 4096), or the template wasn't prefix-stable.
- token length p50/p95/max, and **trained tokens = N% of all tokens**, typically a small fraction
- the decoded **trained spans** of the first example: only tool calls and the SITREP, never the swarm context

Try a model with a different template:
```bash
python training/sft_lora.py --model HuggingFaceTB/SmolLM3-3B --data data-small/train.jsonl --dry-run
```

## Exercises
1. Which model needs the fewest tokens for the same trajectory? What does that mean for speed and context limits?
2. Find the end-of-turn token in each rendered template. What would happen at inference if training used a different one?
3. In `training/sft_data.py`, why does `build_example` **drop** examples longer than `max_len` instead of cutting them?
4. Run the dry-run with `--max-len 2048`. How many examples are dropped? How would you fix that without losing data? (Hint: `--max-events` in `training.gen_data`.)
5. Look at the "trained tokens %" figure. If you trained on *all* tokens instead, what would the model get better at, and what would it get worse at?

## Checkpoint
- [ ] You can explain what a chat template is and show two different tool-call formats
- [ ] You know what `native` vs `text` mode means in EMBER and when each happens
- [ ] You can explain the `-100` labels and point to the trained spans in the dry-run output

## Interview questions
- What breaks if you fine-tune with one chat template and serve with another?
- Why mask prompt tokens in SFT? Are there cases where you wouldn't?
- How do you add tool calling to a model whose template doesn't support it?
