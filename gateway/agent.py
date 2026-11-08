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
    # Muse Glimmer emits one tool call per message, so a full assessment takes ~5-12 turns.
    def __init__(self, swarm: Swarm, toolbox: Toolbox, client: Optional[ChatClient] = None,
                 max_turns: int = 16):
        self.swarm, self.tools = swarm, toolbox
        self.client = client or ChatClient()
        self.max_turns = max_turns

    def run(self, on_step: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
        emit = on_step or (lambda _: None)
        messages: List[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt(self.swarm.context())},
        ]
        report, n_calls, t0, nudged = "", 0, time.time(), False
        # what went wrong / how fast: used by the eval's failure taxonomy (Lab 03/06)
        stats = {"turns": 0, "nudged": False, "fallback_parses": 0, "tool_errors": 0, "unknown_tools": 0,
                 "turn_limit": False, "gen_tokens": 0, "tok_per_s": []}
        for turn in range(self.max_turns):
            out = self.client.chat(messages, tools=TOOL_SCHEMAS)
            stats["turns"] += 1
            stats["fallback_parses"] += int(bool(out.get("fallback")))
            stats["gen_tokens"] += out.get("gen_tokens") or 0
            if out.get("tok_per_s"):
                stats["tok_per_s"].append(out["tok_per_s"])
            if out.get("reasoning"):
                emit({"kind": "thought", "text": out["reasoning"]})
            calls = out["tool_calls"]
            if not calls:
                if self.tools.assessment is None and not nudged:
                    nudged = stats["nudged"] = True   # answered without submitting: ask once for the structured call
                    messages.append({"role": "assistant", "content": out["content"] or ""})
                    messages.append({"role": "user", "content": "Call submit_assessment now, then write the SITREP."})
                    continue
                report = out["content"]
                break
            messages.append({"role": "assistant", "content": out["content"] or "", "tool_calls": calls})
            for c in calls:
                name, args = c["function"]["name"], c["function"]["arguments"]
                emit({"kind": "call", "name": name, "args": args})
                result = self.tools.call(name, args)
                n_calls += 1
                if result.startswith('{"error"'):
                    stats["unknown_tools" if "unknown tool" in result else "tool_errors"] += 1
                emit({"kind": "result", "name": name, "result": result})
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
        else:
            report = "(agent hit the turn limit before writing a report)"
            stats["turn_limit"] = True
        emit({"kind": "report", "text": report})
        tps = stats.pop("tok_per_s")
        stats["tok_per_s"] = round(sum(tps) / len(tps), 1) if tps else None
        return {"assessment": self.tools.assessment, "report": report, "steps": n_calls,
                "seconds": round(time.time() - t0, 1), "messages": messages, "stats": stats}


def dump_trace(result: Dict[str, Any]) -> str:
    return json.dumps({k: v for k, v in result.items() if k != "messages"}, indent=2)
