"""Tools Muse Glimmer can call to interrogate and act on the swarm."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from .swarm import AirtimeExhausted, Swarm

VERDICTS = ("ALERT", "WATCH", "DISMISS")
CAUSES = ("wildfire", "campfire", "sun_glare", "vehicle", "sensor_fault", "weather", "none")

_node = {"type": "integer", "description": "Node id, e.g. 3 for n3"}

TOOL_SCHEMAS: List[dict] = [
    {"type": "function", "function": {
        "name": "get_node_history",
        "description": "Return the last packets (heartbeats, events, responses) received from one node.",
        "parameters": {"type": "object", "properties": {"node_id": _node}, "required": ["node_id"]}}},
    {"type": "function", "function": {
        "name": "ping_node",
        "description": "Check whether a node is reachable over the mesh. Returns PONG with battery or a timeout.",
        "parameters": {"type": "object", "properties": {"node_id": _node}, "required": ["node_id"]}}},
    {"type": "function", "function": {
        "name": "request_confirm",
        "description": "Ask a node to re-sample camera and microphone for 3 seconds and report its classification "
                       "(FLM/SMK/CRK/MOT/OK with confidence, haze and sound). Use to verify detections or "
                       "to check a neighbor (e.g. downwind) for spread.",
        "parameters": {"type": "object", "properties": {"node_id": _node}, "required": ["node_id"]}}},
    {"type": "function", "function": {
        "name": "set_sample_rate",
        "description": "Change how often a node reports (seconds between heartbeats). Lower = closer watch.",
        "parameters": {"type": "object", "properties": {
            "node_id": _node,
            "seconds": {"type": "integer", "description": "5-120"}}, "required": ["node_id", "seconds"]}}},
    {"type": "function", "function": {
        "name": "tune_detectors",
        "description": "Re-program the on-device detectors of one node or all nodes over the mesh. "
                       "rebase=true adopts the current scene as the haze baseline (removes ambient fog/dust); "
                       "smoke_threshold sets the haze level that triggers SMK (default 25, 101 mutes a faulty "
                       "haze sensor); flicker_check=true makes FLM require flame flicker (suppresses glare).",
        "parameters": {"type": "object", "properties": {
            "node_id": {"type": ["integer", "string"], "description": "Node id, or \"all\" to broadcast"},
            "rebase": {"type": "boolean"},
            "smoke_threshold": {"type": "integer", "description": "25-101"},
            "flicker_check": {"type": "boolean"}}, "required": ["node_id"]}}},
    {"type": "function", "function": {
        "name": "broadcast_alert",
        "description": "Flood an alert to every node in the mesh (nodes flash their LEDs; field crews see it).",
        "parameters": {"type": "object", "properties": {
            "level": {"type": "integer", "description": "1 = watch, 2 = warning, 3 = evacuate/respond"},
            "message": {"type": "string"}}, "required": ["level", "message"]}}},
    {"type": "function", "function": {
        "name": "submit_assessment",
        "description": "Submit the final structured assessment of the swarm situation. Call exactly once, "
                       "after investigating.",
        "parameters": {"type": "object", "properties": {
            "verdict": {"type": "string", "enum": list(VERDICTS)},
            "cause": {"type": "string", "enum": list(CAUSES)},
            "confidence": {"type": "number", "description": "0-1"},
            "location": {"type": ["object", "null"], "description": "Estimated source {x, y} in meters, or null",
                         "properties": {"x": {"type": "number"}, "y": {"type": "number"}}},
            "severity": {"type": "integer", "description": "0 none, 1 small, 2 spreading, 3 large"},
            "affected_nodes": {"type": "array", "items": {"type": "integer"}},
            "unreliable_nodes": {"type": "array", "items": {"type": "integer"},
                                 "description": "Nodes that are offline, failing or giving bad readings"},
            "summary": {"type": "string", "description": "One or two sentences."}},
            "required": ["verdict", "cause", "confidence", "location", "severity",
                         "affected_nodes", "unreliable_nodes", "summary"]}}},
]


class Toolbox:
    def __init__(self, swarm: Swarm, timeout: float = 8.0,
                 on_call: Optional[Callable[[str, dict, str], None]] = None):
        self.swarm = swarm
        self.timeout = timeout
        self.on_call = on_call
        self.assessment: Optional[Dict[str, Any]] = None
        self.alerts: List[dict] = []

    def call(self, name: str, args: Dict[str, Any]) -> str:
        fn = getattr(self, f"_t_{name}", None)
        if fn is None:
            out = {"error": f"unknown tool {name!r}"}
        else:
            try:
                out = fn(**args)
            except TypeError as e:
                out = {"error": f"bad arguments for {name}: {e}"}
            except AirtimeExhausted as e:
                out = {"error": f"airtime budget exhausted: {e}. Decide from passive data and free tools."}
            except (KeyError, ValueError) as e:
                out = {"error": str(e)}
        result = json.dumps(out, separators=(",", ":"))
        if self.on_call:
            self.on_call(name, args, result)
        return result

    def _check(self, node_id: Any) -> int:
        nid = int(node_id)
        if nid not in self.swarm.nodes:
            raise KeyError(f"no node n{nid}; known: {sorted(self.swarm.nodes)}")
        return nid

    def _t_get_node_history(self, node_id: int) -> dict:
        nid = self._check(node_id)
        now = self.swarm.clock()
        hist = self.swarm.history(nid)
        return {"node": nid, "packets": [f"T-{int(now - e.t):04d} {e.pkt.raw or e.pkt.encode()}" for e in hist]
                or ["(no packets received)"]}

    def _mesh(self, nid: int, cmd: str, **args) -> dict:
        """One request/response over the mesh, reported with the airtime it cost."""
        before = self.swarm.airtime_used_ms()
        r = self.swarm.request(nid, cmd, self.timeout, **args)
        return {"node": nid, "reply": r.encode() if r else f"TIMEOUT after {self.timeout:.0f}s",
                "airtime_ms": round(self.swarm.airtime_used_ms() - before)}

    def _t_ping_node(self, node_id: int) -> dict:
        return self._mesh(self._check(node_id), "PING")

    def _t_request_confirm(self, node_id: int) -> dict:
        return self._mesh(self._check(node_id), "CONFIRM")

    def _t_set_sample_rate(self, node_id: int, seconds: int) -> dict:
        nid = self._check(node_id)
        seconds = max(5, min(120, int(seconds)))
        out = self._mesh(nid, "SET", rate=seconds)
        if not out["reply"].startswith("TIMEOUT"):
            self.swarm.nodes[nid].rate = seconds
        return out

    def _t_tune_detectors(self, node_id: Any, rebase: bool = False, smoke_threshold: Optional[int] = None,
                          flicker_check: Optional[bool] = None) -> dict:
        args: Dict[str, int] = {}
        if rebase:
            args["rebase"] = 1
        if smoke_threshold is not None:
            args["smk"] = max(0, min(101, int(smoke_threshold)))
        if flicker_check is not None:
            args["flk"] = int(bool(flicker_check))
        if not args:
            raise ValueError("nothing to tune: pass rebase, smoke_threshold or flicker_check")
        before = self.swarm.airtime_used_ms()
        if str(node_id).lower() in ("all", "*"):
            qid, replies = self.swarm.broadcast("SET", collect_s=3.0, **args)
            acked = sorted({r.node for r in replies})
            for nid in acked:
                self.swarm.nodes[nid].tuning.update(args)
            return {"sent": f"C|*|q{qid}|SET|" + "|".join(f"{k}{v}" for k, v in args.items()),
                    "acked": acked, "airtime_ms": round(self.swarm.airtime_used_ms() - before)}
        nid = self._check(node_id)
        out = self._mesh(nid, "SET", **args)
        if not out["reply"].startswith("TIMEOUT"):
            self.swarm.nodes[nid].tuning.update(args)
        return out

    def _t_broadcast_alert(self, level: int, message: str) -> dict:
        level = max(1, min(3, int(level)))
        before = self.swarm.airtime_used_ms()
        qid, _ = self.swarm.broadcast("ALERT", lvl=level)
        self.alerts.append({"level": level, "message": message})
        return {"sent": f"C|*|q{qid}|ALERT|lvl{level}", "nodes": len(self.swarm.nodes),
                "airtime_ms": round(self.swarm.airtime_used_ms() - before)}

    def _t_submit_assessment(self, **a: Any) -> dict:
        if a.get("verdict") not in VERDICTS:
            raise ValueError(f"verdict must be one of {VERDICTS}")
        if a.get("cause") not in CAUSES:
            raise ValueError(f"cause must be one of {CAUSES}")
        self.assessment = a
        return {"ok": True}
