"""Minimal OpenAI-compatible chat client (stdlib only) + Muse Glimmer ATEM fallback parser.

Works with llama.cpp `llama-server` (free, local; the default: http://localhost:8081/v1) and
vLLM (`--tool-call-parser muse_glimmer`). Both return structured `tool_calls`; if a server
returns raw ATEM blocks in the content instead, `parse_atem` recovers the calls (same rules as
the cookbook parser).
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Tuple

_SPAN = re.compile(r"to=(self|user)\b.*?(?=to=|\Z)", re.S)
_INVOKE = re.compile(r'<atem:invoke name="([^"]+)">(.*?)</atem:invoke>', re.S)
_PARAM = re.compile(r'<atem:parameter name="([^"]+)">(.*?)</atem:parameter>', re.S)


_TOOL_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def parse_text_calls(text: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Recover <tool_call>{"name":..,"arguments":{..}}</tool_call> blocks written as plain text
    (Hermes/Qwen style, and EMBER's fallback format for templates without tool support)."""
    calls = []
    for blob in _TOOL_CALL.findall(text):
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "name" in obj:
            calls.append({"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                          "function": {"name": obj["name"], "arguments": obj.get("arguments") or {}}})
    return (_TOOL_CALL.sub("", text).strip() if calls else text), calls


def parse_atem(text: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Return (content_without_calls, tool_calls) from raw Muse Glimmer output."""
    if "<atem:invoke" not in text:
        return text, []
    scan = _SPAN.sub("", text) if "to=" in text else text   # echoed calls inside reasoning/answer don't count
    calls = []
    for name, body in _INVOKE.findall(scan):
        args = {}
        for k, v in _PARAM.findall(body):
            v = v.strip()
            try:
                args[k] = json.loads(v)
            except json.JSONDecodeError:
                args[k] = v
        calls.append({"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                      "function": {"name": name, "arguments": args}})
    content = re.sub(r"<atem:function_calls>.*?</atem:function_calls>", "", text, flags=re.S).strip()
    return content, calls


class ChatClient:
    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None,
                 api_key: Optional[str] = None, timeout: float = 600.0, extra: Optional[dict] = None):
        self.base_url = (base_url or os.environ.get("EMBER_LLM_URL", "http://localhost:8081/v1")).rstrip("/")
        self.model = model or os.environ.get("EMBER_LLM_MODEL", "muse-glimmer")
        self.api_key = api_key or os.environ.get("EMBER_LLM_KEY", "EMPTY")
        self.timeout = timeout   # a 30B model on a laptop can take a while per turn
        # Extra request fields, e.g. EMBER_LLM_EXTRA='{"chat_template_kwargs":{"reasoning_strength":"low"}}'
        # for llama.cpp, so Muse Glimmer's reasoning doesn't use up the token budget.
        self.extra = extra if extra is not None else json.loads(os.environ.get("EMBER_LLM_EXTRA") or "{}")

    def chat(self, messages: List[dict], tools: Optional[List[dict]] = None,
             temperature: float = 0.6, max_tokens: int = 3000) -> Dict[str, Any]:
        """Returns {"content": str, "tool_calls": [...], "reasoning": str|None}.
        Muse Glimmer should not be run greedy; always send temperature, top_p and top_k together.
        Reasoning tokens count toward max_tokens, hence the generous default."""
        body: Dict[str, Any] = {"model": self.model, "messages": _wire(messages),
                                "temperature": temperature, "top_p": 0.95, "top_k": 64,
                                "max_tokens": max_tokens, **self.extra}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        data = self._post(body)
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length" and not (choice["message"].get("content")
                                                           or choice["message"].get("tool_calls")):
            # all tokens went to reasoning; retry once with double the budget
            data = self._post({**body, "max_tokens": max_tokens * 2})
            choice = data["choices"][0]
        msg = choice["message"]
        content = msg.get("content") or ""
        calls = []
        for c in msg.get("tool_calls") or []:
            args = c["function"].get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError:
                    args = {"_raw": args}
            calls.append({"id": c.get("id") or f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                          "function": {"name": c["function"]["name"], "arguments": args}})
        fallback = False
        if not calls:   # server didn't parse tool calls: try the text formats ourselves
            for parser in (parse_atem, parse_text_calls):
                content, calls = parser(content)
                if calls:
                    fallback = True
                    break
        t = data.get("timings") or {}   # llama.cpp reports generation speed per request
        return {"content": content, "tool_calls": calls, "fallback": fallback,
                "reasoning": msg.get("reasoning_content") or msg.get("reasoning"),
                "gen_tokens": t.get("predicted_n") or (data.get("usage") or {}).get("completion_tokens"),
                "tok_per_s": t.get("predicted_per_second")}

    def _post(self, body: Dict[str, Any]) -> Dict[str, Any]:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())


def _wire(messages: List[dict]) -> List[dict]:
    """OpenAI wire format wants tool-call arguments as JSON strings."""
    out = []
    for m in messages:
        if m.get("tool_calls"):
            m = dict(m)
            m["tool_calls"] = [{**c, "function": {**c["function"],
                                "arguments": json.dumps(c["function"]["arguments"])}} for c in m["tool_calls"]]
        out.append(m)
    return out
