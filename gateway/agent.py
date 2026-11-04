"""Muse Glimmer agent loop: observe swarm -> tool calls over the mesh -> assessment + SITREP."""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

from .llm import ChatClient
from .prompts import SYSTEM_PROMPT, user_prompt
from .swarm import Swarm
from .tools import TOOL_SCHEMAS, Toolbox


class GlimmerAgent:
    # The muse_glimmer vLLM parser can return parallel calls one per response, so allow extra turns.
    def __init__(self, swarm: Swarm, toolbox: Toolbox, client: Optional[ChatClient] = None,
                 max_turns: int = 10):
        self.swarm, self.tools = swarm, toolbox
        self.client = client or ChatClient()
        self.max_turns = max_turns

    def run(self, on_step: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
        emit = on_step or (lambda _: None)
        messages: List[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt(self.swarm.context())},
        ]
        report, n_calls, t0 = "", 0, time.time()
        for turn in range(self.max_turns):
            out = self.client.chat(messages, tools=TOOL_SCHEMAS)
            if out.get("reasoning"):
                emit({"kind": "thought", "text": out["reasoning"]})
            calls = out["tool_calls"]
            if not calls:
                report = out["content"]
                break
            messages.append({"role": "assistant", "content": out["content"] or "", "tool_calls": calls})
            for c in calls:
                name, args = c["function"]["name"], c["function"]["arguments"]
                emit({"kind": "call", "name": name, "args": args})
                result = self.tools.call(name, args)
                n_calls += 1
                emit({"kind": "result", "name": name, "result": result})
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
        else:
            report = "(agent hit the turn limit before writing a report)"
        emit({"kind": "report", "text": report})
        return {"assessment": self.tools.assessment, "report": report, "steps": n_calls,
                "seconds": round(time.time() - t0, 1), "messages": messages}


def dump_trace(result: Dict[str, Any]) -> str:
    return json.dumps({k: v for k, v in result.items() if k != "messages"}, indent=2)
