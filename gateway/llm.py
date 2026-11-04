"""Minimal OpenAI-compatible chat client (stdlib only) + Muse Glimmer ATEM fallback parser.

vLLM serves Muse Glimmer with `--enable-auto-tool-choice --tool-call-parser muse_glimmer`,
which returns structured `tool_calls`. If a server returns raw ATEM blocks in the
content instead, `parse_atem` recovers the calls (same rules as the cookbook parser).
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
                 api_key: Optional[str] = None, timeout: float = 120.0):
        self.base_url = (base_url or os.environ.get("EMBER_LLM_URL", "http://localhost:8000/v1")).rstrip("/")
        self.model = model or os.environ.get("EMBER_LLM_MODEL", "ember")
        self.api_key = api_key or os.environ.get("EMBER_LLM_KEY", "EMPTY")
        self.timeout = timeout

    def chat(self, messages: List[dict], tools: Optional[List[dict]] = None,
             temperature: float = 0.6, max_tokens: int = 1500) -> Dict[str, Any]:
        """Returns {"content": str, "tool_calls": [...], "reasoning": str|None}.
        Muse Glimmer should not be run greedy; always send temperature, top_p and top_k together."""
        body: Dict[str, Any] = {"model": self.model, "messages": _wire(messages),
                                "temperature": temperature, "top_p": 0.95, "top_k": 64,
                                "max_tokens": max_tokens}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read())
        msg = data["choices"][0]["message"]
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
        if not calls:
            content, calls = parse_atem(content)
        return {"content": content, "tool_calls": calls,
                "reasoning": msg.get("reasoning_content") or msg.get("reasoning")}


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
