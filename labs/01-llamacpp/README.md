# Lab 01: llama.cpp internals (convert, quantize, benchmark, serve)

**Goal:** take a model from raw Hugging Face weights to a quantized GGUF file, measure what quantization costs in speed and quality, and serve it as an API that can make tool calls. You'll reuse all of this in Labs 03 and 05.

**Model:** `Qwen/Qwen3-0.6B`. It's tiny, so every step takes seconds to minutes.

## Concepts (why)
- **GGUF** is llama.cpp's single-file model format: the weights plus everything needed to run them (tokenizer, chat template, architecture settings).
- **Quantization** stores weights with fewer bits:

  | Type | Bits per weight (approx.) | Typical use |
  |---|---|---|
  | F16 | 16 | reference quality, big and slow |
  | Q8_0 | ~8.5 | almost lossless |
  | Q4_K_M | ~4.8 | the usual sweet spot for laptops |

  Fewer bits means less memory and faster generation, but some quality loss.
- **Two speeds:**
  - **Prompt processing** (`pp`) reads your input in parallel. It's fast and limited by compute.
  - **Token generation** (`tg`) writes one token at a time. It's slower and mostly limited by memory bandwidth, which is why smaller quants generate faster.
- **KV cache:** while generating, the model keeps attention keys/values for every token in context. Longer context (`-c`) means more memory, even with a tiny model.
- **Metal offload:** on Apple silicon, llama.cpp runs the layers on the GPU (`-ngl`, all layers by default in recent builds).
- **Perplexity:** how "surprised" the model is by real text; lower is better. It's a cheap way to see whether a quant damaged the model.

## Steps

### 1. Download the original weights
```bash
cd ~/Documents/meta/ember && source .venv/bin/activate
huggingface-cli download Qwen/Qwen3-0.6B --local-dir models/qwen3-0.6b
ls -lh models/qwen3-0.6b
```
✅ You see `config.json`, `tokenizer.json`, and `model.safetensors` (~1.2 GB in bf16).

### 2. Get llama.cpp's Python converters
```bash
git clone --depth 1 https://github.com/ggml-org/llama.cpp .llama.cpp
pip install -r .llama.cpp/requirements/requirements-convert_hf_to_gguf.txt
```
The repo is git-ignored. `training/export_gguf.sh` reuses the same clone in Lab 05.

### 3. Convert to GGUF (F16) and quantize
```bash
python .llama.cpp/convert_hf_to_gguf.py models/qwen3-0.6b --outtype f16 --outfile models/qwen3-0.6b-f16.gguf
llama-quantize models/qwen3-0.6b-f16.gguf models/qwen3-0.6b-Q8_0.gguf Q8_0
llama-quantize models/qwen3-0.6b-f16.gguf models/qwen3-0.6b-Q4_K_M.gguf Q4_K_M
ls -lh models/*.gguf
```
✅ Three files, roughly in the ratio 16 : 8.5 : 4.8. Q4_K_M should be under a third of F16.

### 4. Benchmark speed
```bash
for q in f16 Q8_0 Q4_K_M; do llama-bench -m models/qwen3-0.6b-$q.gguf; done
```
✅ A table per file with `pp512` (prompt tok/s) and `tg128` (generation tok/s). Expect `tg` to rise as the file gets smaller, and `pp` to change less.

### 5. Measure quality loss (perplexity)
Make a test text from the repo's own docs:
```bash
cat README.md docs/SETUP_GUIDE.md docs/HARDWARE_BRINGUP.md > models/ppl.txt
for q in f16 Q8_0 Q4_K_M; do echo "== $q"; llama-perplexity -m models/qwen3-0.6b-$q.gguf -f models/ppl.txt -c 512 2>&1 | grep -i "estimated PPL\|Final estimate"; done
```
✅ A final perplexity for each. Q8_0 should be almost identical to F16; Q4_K_M a bit higher. Tiny models lose more from quantization than big ones; note how much.

### 6. Chat with it
```bash
llama-cli -m models/qwen3-0.6b-Q4_K_M.gguf -cnv
```
Ask something, then type `/exit` or press Ctrl+C. Watch the speed printout at the end.

### 7. Serve it as an API with tool calling
```bash
scripts/serve_gguf.sh models/qwen3-0.6b-Q4_K_M.gguf
```
This runs `llama-server` on **port 8081** (the EMBER dashboard uses 8080) with `--jinja`, so the model's own chat template, including its tool-call format, is used. The model is registered under the alias `local`.

In a second terminal:
```bash
curl -s http://localhost:8081/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "local",
  "messages": [{"role": "user", "content": "Node 3 reports smoke. Re-check it."}],
  "tools": [{"type": "function", "function": {"name": "request_confirm",
     "description": "Ask a node to re-sample its sensors",
     "parameters": {"type": "object", "properties": {"node_id": {"type": "integer"}}, "required": ["node_id"]}}}],
  "chat_template_kwargs": {"enable_thinking": false}
}' | python -m json.tool
```
✅ The response has `"finish_reason": "tool_calls"` and a `tool_calls` entry calling `request_confirm` with `{"node_id": 3}`. If you get plain text instead, that's a tool-use failure. Try a couple of times; a 0.6B model isn't always reliable, and that's exactly what Lab 03 measures.

The response also contains `timings` (e.g. `predicted_per_second`). EMBER's client reads these to report tok/s in evaluations.

Stop the server with **Ctrl+C**.

## Exercises
1. **Fill this table** (and copy it into your learning log):

   | Quant | File size | pp512 tok/s | tg128 tok/s | Perplexity |
   |---|---|---|---|---|
   | F16 | | | | |
   | Q8_0 | | | | |
   | Q4_K_M | | | | |

2. Try `Q2_K` or `Q3_K_M` with `llama-quantize`. Where does quality fall off a cliff?
3. Run the server with `-c 4096` and then `-c 32768`, and watch memory in Activity Monitor. That's the KV cache.
4. Open `gateway/llm.py` and find where EMBER sends requests and reads `timings`. Which fields does it use?

## Checkpoint
- [ ] You produced F16, Q8_0 and Q4_K_M GGUFs yourself
- [ ] You can explain pp vs tg speed and why quantization helps tg more
- [ ] You saw a real tool call come back from `llama-server`

## Interview questions
- Why is token generation memory-bandwidth bound on most hardware?
- What does "K-quant" (Q4_K_M) mean compared with plain Q4_0?
- When would you choose Q8_0 over Q4_K_M in production?
- What does the KV cache store, and how does context length affect memory?
