"""Turn EMBER trajectories into supervised fine-tuning examples for any chat model.

The two ideas that matter (Lab 02 / Lab 04):
  1. Chat templates: every model family serializes messages, tool schemas and tool calls
     differently. We always let the model's own tokenizer template do it, so training text
     looks exactly like what llama.cpp / the server will send at inference time.
  2. Loss masking: we only train on what the *assistant* writes (tool calls + the SITREP).
     The system prompt, the swarm context and tool results are labelled -100, so the model
     learns to act, not to parrot its input.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

IGNORE = -100


def load_rows(path: str, limit: int = 0) -> List[dict]:
    rows = [json.loads(line) for line in open(path)]
    return rows[:limit] if limit else rows


def render(tok, messages: List[dict], tools: Optional[List[dict]]) -> str:
    return tok.apply_chat_template(messages, tools=tools, tokenize=False)


def _as_text_calls(messages: List[dict], tools: List[dict]) -> List[dict]:
    """Fallback for templates without native tool support: tools go into the system prompt,
    tool calls become <tool_call>{json}</tool_call> text, tool results become user turns."""
    out: List[dict] = []
    spec = json.dumps([t["function"] for t in tools], separators=(",", ":"))
    for m in messages:
        if m["role"] == "system":
            out.append({"role": "system", "content": m["content"] + "\n\nTools you can call. Reply with "
                        "<tool_call>{\"name\": ..., \"arguments\": {...}}</tool_call> to call one:\n" + spec})
        elif m.get("tool_calls"):
            calls = "".join("<tool_call>" + json.dumps({"name": c["function"]["name"],
                                                         "arguments": c["function"]["arguments"]}) + "</tool_call>"
                            for c in m["tool_calls"])
            out.append({"role": "assistant", "content": (m.get("content") or "") + calls})
        elif m["role"] == "tool":
            out.append({"role": "user", "content": f"[tool result: {m.get('name', 'tool')}] {m['content']}"})
        else:
            out.append({"role": m["role"], "content": m.get("content") or ""})
    return out


def prepare(tok, row: Dict[str, Any]) -> Tuple[List[dict], Optional[List[dict]], str]:
    """Pick native tool rendering when the template supports it, else the text fallback.
    Returns (messages, tools_or_None, mode) where mode is 'native' or 'text'."""
    msgs, tools = row["messages"], row.get("tools")
    try:
        text = render(tok, msgs, tools)
        if tools and "submit_assessment" in text and text.count("submit_assessment") >= 2:
            return msgs, tools, "native"   # schema and the call both made it into the text
    except Exception:   # template rejects tools / tool roles
        pass
    return _as_text_calls(msgs, tools or []), None, "text"


def build_example(tok, row: Dict[str, Any], max_len: int) -> Optional[Dict[str, List[int]]]:
    """Tokenize one trajectory; label only the assistant messages.

    Assistant spans are found by rendering the conversation up to (and including) each
    assistant message and mapping those character ranges onto token offsets, so the mask
    follows the real chat template. Examples longer than max_len are dropped, never cut
    (a cut example would teach the model to stop mid-sentence)."""
    msgs, tools, _ = prepare(tok, row)
    full = render(tok, msgs, tools)
    spans = []
    for i, m in enumerate(msgs):
        if m["role"] != "assistant":
            continue
        before, through = render(tok, msgs[:i], tools), render(tok, msgs[:i + 1], tools)
        if not (full.startswith(before) and full.startswith(through)):
            return None   # template isn't prefix-stable for this example; skip it
        spans.append((len(before), len(through)))
    enc = tok(full, add_special_tokens=False, return_offsets_mapping=True)
    ids = enc["input_ids"]
    if len(ids) > max_len or not spans:
        return None
    labels = [IGNORE] * len(ids)
    for k, (s, _) in enumerate(enc["offset_mapping"]):
        if any(a <= s < b for a, b in spans):
            labels[k] = ids[k]
    return {"input_ids": ids, "labels": labels}


def trained_spans(tok, ex: Dict[str, List[int]]) -> List[str]:
    """Decode the labelled (trained) parts of an example; handy for eyeballing the mask."""
    spans, cur = [], []
    for t, l in zip(ex["input_ids"], ex["labels"]):
        if l != IGNORE:
            cur.append(t)
        elif cur:
            spans.append(tok.decode(cur))
            cur = []
    if cur:
        spans.append(tok.decode(cur))
    return spans
