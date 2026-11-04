"""Rule-based command-center policy.

Two uses:
  * `HeuristicAgent`: an observation-only baseline / offline fallback agent.
  * the same investigation + reporting logic is reused by training/gen_data.py
    to build SFT traces, where the verdict is replaced by simulator ground truth.
"""
from __future__ import annotations

import json
import math
import random
from typing import Any, Dict, List, Optional, Tuple

from .sim import compass
from .swarm import Swarm

Call = Tuple[str, Dict[str, Any]]
HOT = ("FLM", "SMK", "CRK")


def silent_nodes(swarm: Swarm) -> List[int]:
    now = swarm.clock()
    return [n.id for n in swarm.nodes.values()
            if n.last_seen is None or now - n.last_seen > 2.5 * max(n.rate, 30)]


def stuck_nodes(swarm: Swarm) -> List[int]:
    out = []
    for n in swarm.nodes.values():
        h, snd = list(n.hz_hist), list(n.snd_hist)
        # pinned haze with no flame and a quiet mic: a fogged lens or failed sensor, not smoke
        if len(h) >= 3 and min(h) >= 80 and max(h) - min(h) <= 3 and max(snd) < 15 and n.code != "FLM":
            out.append(n.id)
    return out


def hot_nodes(swarm: Swarm) -> List[int]:
    return sorted((n.id for n in swarm.nodes.values() if n.code in HOT),
                  key=lambda i: -swarm.nodes[i].conf)


def recent_flame_nodes(swarm: Swarm, window: float = 600) -> List[int]:
    """Nodes that reported FLM/SMK in the window but are back to OK now."""
    now = swarm.clock()
    seen = {e.src for e in swarm.log if now - e.t <= window and e.pkt.type == "E" and e.pkt.code in ("FLM", "SMK")}
    return sorted(n for n in seen if swarm.nodes[n].code == "OK")


def motion_nodes(swarm: Swarm, window: float = 600) -> List[int]:
    now = swarm.clock()
    return sorted({e.src for e in swarm.log if now - e.t <= window and e.pkt.type == "E" and e.pkt.code == "MOT"})


def downwind_neighbor(swarm: Swarm, nid: int, exclude: List[int]) -> Optional[int]:
    src = swarm.nodes[nid]
    wx, wy = swarm.wind.vec
    best, best_score = None, 0.0
    for n in swarm.nodes.values():
        if n.id == nid or n.id in exclude:
            continue
        dx, dy = n.x - src.x, n.y - src.y
        d = math.hypot(dx, dy)
        if d == 0 or d > 450:
            continue
        along = (dx * wx + dy * wy) / d
        score = along / (1 + d / 300)
        if along > 0.3 and score > best_score:
            best, best_score = n.id, score
    return best


MESH_TOOLS = {"request_confirm": "CONFIRM", "ping_node": "PING"}


def investigate_plan(swarm: Swarm) -> Tuple[List[Call], int]:
    """First round of tool calls, in value-of-information order, priced in LoRa airtime.

    Radio queries are kept only while the duty-cycle budget can still pay for them plus an
    alert broadcast; local history lookups are free. Returns (calls, skipped radio queries)."""
    hot = hot_nodes(swarm)
    stuck = stuck_nodes(swarm)
    live_hot = [h for h in hot if h not in stuck]
    wanted: List[Call] = []
    if live_hot:
        wanted.append(("request_confirm", {"node_id": live_hot[0]}))
        dw = downwind_neighbor(swarm, live_hot[0], exclude=live_hot[:2] + stuck)
        if dw is not None:
            wanted.append(("request_confirm", {"node_id": dw}))
        if len(live_hot) > 1:
            wanted.append(("request_confirm", {"node_id": live_hot[1]}))
    elif not hot:
        for nid in recent_flame_nodes(swarm)[:1]:
            wanted.append(("request_confirm", {"node_id": nid}))
    for nid in stuck[:1]:
        wanted.append(("get_node_history", {"node_id": nid}))
    for nid in silent_nodes(swarm)[:2]:
        wanted.append(("ping_node", {"node_id": nid}))
    mot = motion_nodes(swarm)
    if mot and not hot:
        wanted.append(("get_node_history", {"node_id": mot[-1]}))

    left = swarm.airtime_left_ms() - swarm.broadcast_cost_ms("ALERT", lvl=2)
    calls: List[Call] = []
    skipped = 0
    for name, args in wanted:
        if name in MESH_TOOLS:
            cost = swarm.request_cost_ms(args["node_id"], MESH_TOOLS[name])
            if cost > left:
                skipped += 1
                continue
            left -= cost
        calls.append((name, args))
    if skipped and live_hot and not any(n == "get_node_history" and a["node_id"] == live_hot[0] for n, a in calls):
        calls.append(("get_node_history", {"node_id": live_hot[0]}))   # free fallback evidence
    return calls, skipped


def investigate(swarm: Swarm) -> List[Call]:
    return investigate_plan(swarm)[0]


def parse_results(calls: List[Call], results: List[str]) -> Dict[str, Dict[int, str]]:
    """Index tool results by tool name and node id -> reply packet / text."""
    out: Dict[str, Dict[int, str]] = {}
    for (name, args), res in zip(calls, results):
        try:
            r = json.loads(res)
        except json.JSONDecodeError:
            continue
        if "node_id" in args:
            out.setdefault(name, {})[int(args["node_id"])] = r.get("reply") or json.dumps(r.get("packets", r))
    return out


def _conf_reading(reply: Optional[str]) -> Tuple[str, int, int]:
    """'R|n3|q7|CONF|FLM|c88|hz40|snd20' -> ('FLM', 88, 40)."""
    if not reply or not reply.startswith("R|"):
        return "NONE", 0, 0
    parts = reply.split("|")
    code = parts[4] if len(parts) > 4 else "OK"
    c = next((int(p[1:]) for p in parts if p.startswith("c") and p[1:].isdigit()), 0)
    hz = next((int(p[2:]) for p in parts if p.startswith("hz") and p[2:].isdigit()), 0)
    return code, c, hz


def evidence(swarm: Swarm, res: Dict[str, Dict[int, str]]) -> Dict[int, Tuple[str, int, int]]:
    """Best current classification per node: confirm result if we have one, else last event."""
    ev = {}
    for n in swarm.nodes.values():
        hz = n.hz_hist[-1] if n.hz_hist else 0
        ev[n.id] = (n.code, n.conf, hz)
    for nid, reply in res.get("request_confirm", {}).items():
        code, c, hz = _conf_reading(reply)
        if code != "NONE":
            ev[nid] = (code, c, hz)
    return ev


def estimate_location(swarm: Swarm, ev: Dict[int, Tuple[str, int, int]],
                      exclude: List[int]) -> Optional[Dict[str, int]]:
    flm = [(nid, c) for nid, (code, c, _) in ev.items() if code == "FLM" and nid not in exclude]
    if flm:
        w = sum(c for _, c in flm)
        x = sum(swarm.nodes[n].x * c for n, c in flm) / w
        y = sum(swarm.nodes[n].y * c for n, c in flm) / w
        return {"x": int(round(x, -1)), "y": int(round(y, -1))}
    smk = [(nid, max(c, hz)) for nid, (code, c, hz) in ev.items() if code in ("SMK", "CRK") and nid not in exclude]
    if smk:
        w = sum(c for _, c in smk) or 1
        x = sum(swarm.nodes[n].x * c for n, c in smk) / w
        y = sum(swarm.nodes[n].y * c for n, c in smk) / w
        wx, wy = swarm.wind.vec
        return {"x": int(round(x - 80 * wx, -1)), "y": int(round(y - 80 * wy, -1))}
    return None


def confirmed_glare(swarm: Swarm, res: Dict[str, Dict[int, str]], ev=None) -> List[int]:
    """Nodes whose flame detection failed a re-sample and showed no haze."""
    ev = ev or evidence(swarm, res)
    confirms = res.get("request_confirm", {})
    return sorted(nid for nid in set(hot_nodes(swarm) + recent_flame_nodes(swarm))
                  if nid in confirms and _conf_reading(confirms[nid])[0] == "OK" and ev[nid][2] < 10)


def glare_targets(swarm: Swarm, res: Dict[str, Dict[int, str]]) -> List[int]:
    """Glare-prone nodes: confirmed ones, else flame reports with no haze and no sound."""
    conf = confirmed_glare(swarm, res)
    if conf:
        return conf
    now = swarm.clock()
    return sorted({e.src for e in swarm.log if now - e.t <= 600 and e.pkt.type == "E" and e.pkt.code == "FLM"
                   and e.pkt.get("hz", 0) < 12 and e.pkt.get("snd", 0) < 15})


def heuristic_assessment(swarm: Swarm, res: Dict[str, Dict[int, str]]) -> Dict[str, Any]:
    ev = evidence(swarm, res)
    stuck = stuck_nodes(swarm)
    timeouts = [nid for nid, r in res.get("ping_node", {}).items() if "TIMEOUT" in r]
    low_batt = [n.id for n in swarm.nodes.values() if n.batt is not None and n.batt < 3.5]
    unreliable = sorted(set(stuck + timeouts + low_batt))
    glare = confirmed_glare(swarm, res, ev)
    hot = [nid for nid, (code, c, hz) in ev.items()
           if code in HOT and nid not in stuck and nid not in glare]
    mot = motion_nodes(swarm)
    loc = estimate_location(swarm, ev, stuck + glare)
    max_hz = max((hz for nid, (_, _, hz) in ev.items() if nid not in stuck), default=0)

    if len(hot) >= 2 or any(ev[n][0] == "CRK" for n in hot) or (hot and max_hz >= 25 and len(mot) < 2):
        sev = 1 if len(hot) <= 1 else 2 if len(hot) <= 3 else 3
        a = dict(verdict="ALERT", cause="wildfire", confidence=0.8, location=loc, severity=sev,
                 affected_nodes=sorted(hot), unreliable_nodes=unreliable)
    elif len(mot) >= 2:
        a = dict(verdict="DISMISS", cause="vehicle", confidence=0.7, location=None, severity=0,
                 affected_nodes=[], unreliable_nodes=unreliable)
    elif hot:
        a = dict(verdict="WATCH", cause="campfire", confidence=0.6, location=loc, severity=0,
                 affected_nodes=sorted(hot), unreliable_nodes=unreliable)
    elif glare:
        a = dict(verdict="DISMISS", cause="sun_glare", confidence=0.7, location=None, severity=0,
                 affected_nodes=[], unreliable_nodes=unreliable)
    elif stuck:
        a = dict(verdict="DISMISS", cause="sensor_fault", confidence=0.75, location=None, severity=0,
                 affected_nodes=[], unreliable_nodes=unreliable)
    else:
        a = dict(verdict="DISMISS", cause="none", confidence=0.85, location=None, severity=0,
                 affected_nodes=[], unreliable_nodes=unreliable)
    a["summary"] = summary_line(swarm, a)
    return a


# --- actions + reporting -------------------------------------------------------

def actions_for(a: Dict[str, Any], swarm: Swarm, res: Dict[str, Dict[int, str]]) -> List[Call]:
    """Act on the decision: alert, tighten sampling, and re-program node detectors so the
    same false alarm doesn't recur. Only actions the airtime budget can pay for are kept."""
    wanted: List[Tuple[Call, float]] = []
    alive = [n for n in a["affected_nodes"] if n not in a["unreliable_nodes"] and n in swarm.nodes]
    if a["verdict"] == "ALERT":
        lvl = 3 if a["severity"] >= 3 else 2
        where = f" near ({a['location']['x']}, {a['location']['y']})" if a.get("location") else ""
        wanted.append((("broadcast_alert", {"level": lvl, "message": f"Wildfire detected{where}. Severity {a['severity']}."}),
                       swarm.broadcast_cost_ms("ALERT", lvl=lvl)))
        for nid in alive[:3]:
            wanted.append((("set_sample_rate", {"node_id": nid, "seconds": 5}),
                           swarm.request_cost_ms(nid, "SET", rate=5)))
    elif a["verdict"] == "WATCH":
        for nid in alive[:2]:
            wanted.append((("set_sample_rate", {"node_id": nid, "seconds": 10}),
                           swarm.request_cost_ms(nid, "SET", rate=10)))
    elif a["cause"] == "weather":
        wanted.append((("tune_detectors", {"node_id": "all", "rebase": True}),
                       swarm.broadcast_cost_ms("SET", expect_replies=True, rebase=1)))
    elif a["cause"] == "sun_glare":
        for nid in glare_targets(swarm, res)[:2]:
            wanted.append((("tune_detectors", {"node_id": nid, "flicker_check": True}),
                           swarm.request_cost_ms(nid, "SET", flk=1)))
    elif a["cause"] == "sensor_fault":
        for nid in stuck_nodes(swarm)[:2]:
            wanted.append((("tune_detectors", {"node_id": nid, "smoke_threshold": 101}),
                           swarm.request_cost_ms(nid, "SET", smk=101)))
    left, calls = swarm.airtime_left_ms(), []
    for call, cost in wanted:
        if cost <= left:
            calls.append(call)
            left -= cost
    return calls


def _names(ids: List[int]) -> str:
    return ", ".join(f"n{i}" for i in ids) or "one node"


def summary_line(swarm: Swarm, a: Dict[str, Any]) -> str:
    nodes = ", ".join(f"n{i}" for i in a["affected_nodes"]) or "no nodes"
    bad = ", ".join(f"n{i}" for i in a["unreliable_nodes"])
    loc = a.get("location")
    where = f" centered near ({loc['x']}, {loc['y']}) m" if loc else ""
    s = {
        "wildfire": f"Active wildfire{where}, detected by {nodes}; spreading {compass(swarm.wind.toward_deg)} with the wind.",
        "campfire": f"Small stable flame at {nodes}{where} with no spread to neighbors; likely a campfire.",
        "sun_glare": "Flame detection did not survive re-sampling and had no haze or sound; sun glare.",
        "vehicle": f"Motion at {_names(motion_nodes(swarm))} with engine-level sound and no haze; a passing vehicle.",
        "sensor_fault": "Haze reading is pinned high with no supporting signals from neighbors; sensor fault.",
        "weather": "Haze rose evenly across the swarm with no flame or crackle; regional fog, not smoke.",
        "none": "No fire indicators across the swarm.",
    }[a["cause"]]
    if bad:
        s += f" Unreliable: {bad}."
    return s


TUNE_TEXT = {
    "rebase": "re-baselined haze (ambient fog/dust now reads as zero)",
    "flk": "flame now requires flicker (glare suppression)",
    "smk": "haze channel muted",
}


def sitrep(swarm: Swarm, a: Dict[str, Any], res: Dict[str, Dict[int, str]],
           rng: Optional[random.Random] = None, actions: Optional[List[Call]] = None,
           spent_ms: float = 0.0, skipped: int = 0) -> str:
    rng = rng or random.Random(0)
    now = swarm.clock()
    lines = [f"## EMBER SITREP: {swarm.area}",
             f"**Verdict:** {a['verdict']} ({a['cause'].replace('_', ' ')}, severity {a['severity']}, "
             f"confidence {a['confidence']:.2f})"]
    if a.get("location"):
        loc = a["location"]
        near = min(swarm.nodes.values(), key=lambda n: math.hypot(n.x - loc["x"], n.y - loc["y"]))
        lines.append(f"**Source estimate:** ({loc['x']}, {loc['y']}) m, closest node n{near.id}")
    lines.append(f"**Assessment:** {a['summary']}")
    ev_lines = []
    for nid, reply in res.get("request_confirm", {}).items():
        ev_lines.append(f"- n{nid} re-sample: `{reply}`")
    for nid, reply in res.get("ping_node", {}).items():
        ev_lines.append(f"- n{nid} ping: `{reply}`")
    for nid in res.get("get_node_history", {}):
        ev_lines.append(f"- n{nid} history reviewed")
    if ev_lines:
        lines.append("**Evidence gathered:**")
        lines.extend(ev_lines)
    tunes = [args for name, args in (actions or []) if name == "tune_detectors"]
    if tunes:
        lines.append("**Edge re-programming:**")
        for t in tunes:
            what = "; ".join(TUNE_TEXT[k] for k, key in (("rebase", "rebase"), ("flk", "flicker_check"),
                                                          ("smk", "smoke_threshold")) if t.get(key))
            target = "all nodes" if str(t["node_id"]) == "all" else f"n{t['node_id']}"
            lines.append(f"- {target}: {what}")
    air = f"**Airtime:** {spent_ms / 1000:.1f}s spent, {swarm.airtime_left_ms() / 1000:.1f}s left this hour"
    if skipped:
        air += f" ({skipped} radio quer{'y' if skipped == 1 else 'ies'} skipped to stay within the duty cycle)"
    lines.append(air)
    if a["unreliable_nodes"]:
        bad = []
        for nid in a["unreliable_nodes"]:
            n = swarm.nodes[nid]
            seen = "never" if n.last_seen is None else f"{int(now - n.last_seen)}s ago"
            bad.append(f"n{nid} (last seen {seen}, batt {n.batt if n.batt is not None else '?'}V)")
        lines.append("**Network health:** " + "; ".join(bad))
    else:
        lines.append(f"**Network health:** all {len(swarm.nodes)} nodes reporting.")
    rec = {
        "wildfire": rng.choice([
            "Dispatch a ground crew to the source estimate and keep downwind nodes at 5 s sampling.",
            "Notify the fire response team; prioritize the downwind sector and re-assess every 2 minutes."]),
        "campfire": rng.choice([
            "Send a ranger to verify the fire is attended; keep the node on 10 s sampling.",
            "Low risk for now. Verify on the next patrol and escalate if neighbors pick up haze."]),
        "sun_glare": "No action. Consider re-aiming the camera or adding a hood to reduce glare.",
        "vehicle": "No action. Motion pattern matches a vehicle on the trail.",
        "sensor_fault": "Schedule maintenance for the faulty node and discount its haze readings.",
        "weather": "No action. Keep normal sampling; re-assess when the fog lifts.",
        "none": "No action required.",
    }[a["cause"]]
    if a["cause"] == "none" and a["unreliable_nodes"]:
        rec = "Replace or recharge the failing node to restore coverage."
    lines.append(f"**Recommendation:** {rec}")
    return "\n".join(lines)


class HeuristicAgent:
    """Observation-only agent with the same interface as the LLM agent."""

    def __init__(self, swarm: Swarm, toolbox):
        self.swarm, self.tools = swarm, toolbox

    def run(self, on_step=None) -> Dict[str, Any]:
        before = self.swarm.airtime_used_ms()
        calls, skipped = investigate_plan(self.swarm)
        results = [self.tools.call(n, a) for n, a in calls]
        res = parse_results(calls, results)
        a = heuristic_assessment(self.swarm, res)
        acts = actions_for(a, self.swarm, res)
        for n, args in acts:
            self.tools.call(n, args)
        self.tools.call("submit_assessment", a)
        report = sitrep(self.swarm, a, res, actions=acts,
                        spent_ms=self.swarm.airtime_used_ms() - before, skipped=skipped)
        return {"assessment": self.tools.assessment, "report": report, "steps": len(calls)}
