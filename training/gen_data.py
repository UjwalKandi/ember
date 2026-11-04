"""Build the EMBER SFT dataset from simulated episodes.

Each example is a full agent trajectory in OpenAI `messages` format:
  system -> user(swarm context) -> assistant tool calls (investigate) -> tool results
  -> assistant tool calls (actions + submit_assessment) -> tool results -> assistant SITREP

Tool *selection* comes from the observation-only policy; the *verdict, cause,
severity and unreliable nodes* come from simulator ground truth, so the model
learns to make calls the rule-based baseline cannot.

    python -m training.gen_data --per-scenario 250 --out data
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gateway import policy, sim  # noqa: E402
from gateway.episode import Episode, make_episode  # noqa: E402
from gateway.prompts import SYSTEM_PROMPT, user_prompt  # noqa: E402
from gateway.tools import TOOL_SCHEMAS  # noqa: E402

EVAL_SEED_OFFSET = 100_000
# Compositions never shown in training; eval reports them separately to measure generalization.
HOLDOUT = ("wildfire_plus_stuck", "wildfire_in_fog")


def teacher_assessment(ep: Episode, res: Dict[str, Dict[int, str]], rng: random.Random) -> Dict[str, Any]:
    sw, tr = ep.swarm, ep.truth
    ev = policy.evidence(sw, res)
    observed_hot = {nid for nid, (code, _, _) in ev.items() if code in policy.HOT}
    observed_hot |= {e.src for e in sw.log if e.pkt.type == "E" and e.pkt.code in policy.HOT}
    silent = set(policy.silent_nodes(sw))
    # keep labels learnable: only claim what the command center could have observed
    affected = sorted(n for n in tr.affected if n in observed_hot or n in silent)
    unreliable = sorted(set(tr.unreliable) & (silent | set(policy.stuck_nodes(sw)) |
                        {n.id for n in sw.nodes.values() if n.batt is not None and n.batt < 3.5}))
    loc = None
    if tr.cause in ("wildfire", "campfire"):
        excl = list(set(policy.stuck_nodes(sw)) | {n.id for n in ep.net.nodes.values() if n.glare})
        loc = policy.estimate_location(sw, ev, excl)
        if loc is None and tr.fire_xy:
            loc = {"x": int(round(tr.fire_xy[0], -1)), "y": int(round(tr.fire_xy[1], -1))}
    conf = {"wildfire": 0.9, "campfire": 0.7, "sun_glare": 0.8, "vehicle": 0.75,
            "sensor_fault": 0.8, "weather": 0.75, "none": 0.9}[tr.cause]
    if ep.scenario == "wildfire_early":
        conf = 0.7
    a = dict(verdict=tr.verdict, cause=tr.cause, confidence=round(conf + rng.uniform(-0.05, 0.05), 2),
             location=loc, severity=tr.severity if tr.cause == "wildfire" else 0,
             affected_nodes=affected, unreliable_nodes=unreliable)
    a["summary"] = policy.summary_line(sw, a)
    return a


def _calls(calls: List[policy.Call]) -> List[dict]:
    return [{"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
             "function": {"name": n, "arguments": a}} for n, a in calls]


def _step(msgs: List[dict], ep: Episode, name: str, args: Dict[str, Any]) -> str:
    """One assistant message with exactly one tool call, then its result (Muse Glimmer's format)."""
    (c,) = _calls([(name, args)])
    msgs.append({"role": "assistant", "content": "", "tool_calls": [c]})
    result = ep.tools.call(name, args)
    msgs.append({"role": "tool", "tool_call_id": c["id"], "name": name, "content": result})
    return result


def trajectory(ep: Episode, rng: random.Random) -> Dict[str, Any]:
    msgs: List[dict] = [{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt(ep.swarm.context())}]
    inv = policy.investigate(ep.swarm)
    results = [_step(msgs, ep, n, a) for n, a in inv]
    res = policy.parse_results(inv, results)
    a = teacher_assessment(ep, res, rng)
    for n, args in policy.actions_for(a) + [("submit_assessment", a)]:
        _step(msgs, ep, n, args)
    msgs.append({"role": "assistant", "content": policy.sitrep(ep.swarm, a, res, rng)})
    return {"messages": msgs, "tools": TOOL_SCHEMAS,
            "meta": {"scenario": ep.scenario, "seed": ep.seed, "verdict": a["verdict"], "cause": a["cause"]}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-scenario", type=int, default=250)
    ap.add_argument("--eval-per-scenario", type=int, default=30)
    ap.add_argument("--val-frac", type=float, default=0.05)
    ap.add_argument("--out", default="data")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(1234)
    rows, stats = [], Counter()
    for sc in [s for s in sim.SCENARIOS if s not in HOLDOUT]:
        for seed in range(args.per_scenario):
            row = trajectory(make_episode(sc, seed), rng)
            rows.append(row)
            stats[(row["meta"]["verdict"], row["meta"]["cause"])] += 1
    rng.shuffle(rows)
    n_val = max(1, int(len(rows) * args.val_frac))
    for name, part in (("val", rows[:n_val]), ("train", rows[n_val:])):
        with open(out / f"{name}.jsonl", "w") as f:
            for r in part:
                f.write(json.dumps(r) + "\n")
    with open(out / "eval_scenarios.jsonl", "w") as f:
        for sc in sim.SCENARIOS:
            for i in range(args.eval_per_scenario):
                f.write(json.dumps({"scenario": sc, "seed": EVAL_SEED_OFFSET + i}) + "\n")
    json.dump({"holdout": list(HOLDOUT), "train_scenarios": [s for s in sim.SCENARIOS if s not in HOLDOUT]},
              open(out / "manifest.json", "w"), indent=1)
    chars = sorted(sum(len(m.get("content") or json.dumps(m.get("tool_calls"))) for m in r["messages"]) for r in rows)
    print(f"train={len(rows) - n_val} val={n_val} eval={len(sim.SCENARIOS) * args.eval_per_scenario}")
    print(f"chars/example p50={chars[len(chars) // 2]} p95={chars[int(len(chars) * .95)]} max={chars[-1]}")
    for k, v in sorted(stats.items()):
        print(f"  {k[0]:8s} {k[1]:13s} {v}")


if __name__ == "__main__":
    main()
