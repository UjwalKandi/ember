"""Evaluate an EMBER command-center agent on held-out simulated scenarios.

    python -m training.eval --agent heuristic
    python -m training.eval --agent llm --url https://<modal>/v1 --model muse-glimmer   # base
    python -m training.eval --agent llm --url https://<modal>/v1 --model ember          # fine-tuned
    python -m training.eval --report results/*.json                                   # comparison table
"""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gateway.agent import GlimmerAgent  # noqa: E402
from gateway.episode import make_episode  # noqa: E402
from gateway.llm import ChatClient  # noqa: E402
from gateway.policy import HeuristicAgent  # noqa: E402


AFTER_S = 300          # after-action window: does the agent's response stop the false alarms?


def spurious_after(ep, seconds: float = AFTER_S) -> int:
    """FLM/SMK events the swarm emits over the next `seconds` (only meaningful when there is no fire)."""
    n = [0]
    ep.swarm.listeners.append(lambda e: n.__setitem__(0, n[0] + (e.pkt.type == "E" and e.pkt.code in ("FLM", "SMK"))))
    ep.net.run(seconds)
    return n[0]


def run_one(spec: Dict[str, Any], args) -> Dict[str, Any]:
    ep = make_episode(spec["scenario"], spec["seed"], spec.get("budget", "generous"))
    air0 = ep.swarm.airtime_used_ms()
    try:
        if args.agent == "heuristic":
            out = HeuristicAgent(ep.swarm, ep.tools).run()
        elif args.agent == "oracle":   # SFT teacher: sees simulator ground truth, an upper bound only
            from training.gen_data import trajectory
            row = trajectory(ep, random.Random(spec["seed"]))
            out = {"assessment": ep.tools.assessment, "report": row["messages"][-1]["content"],
                   "steps": sum(1 for m in row["messages"] if m.get("tool_calls"))}
        else:
            client = ChatClient(args.url, args.model, args.key)
            out = GlimmerAgent(ep.swarm, ep.tools, client).run()
        err = None
    except Exception as e:   # count crashes/timeouts as failures, keep going
        out, err = {"assessment": ep.tools.assessment, "report": "", "steps": 0}, repr(e)
    row = score(ep, out, err)
    row["failure"] = failure_type(row, out)
    row["seconds"] = out.get("seconds")
    row.update({k: v for k, v in (out.get("stats") or {}).items()})
    row["budget"] = ep.budget
    row["airtime_s"] = round((ep.swarm.airtime_used_ms() - air0) / 1000, 2)
    if ep.truth.cause not in ("wildfire", "campfire"):
        row["spurious_act"] = spurious_after(ep)
        row["spurious_ctrl"] = spurious_after(make_episode(spec["scenario"], spec["seed"], ep.budget))
    return row


def failure_type(row: Dict[str, Any], out: Dict[str, Any]) -> str:
    """One label per episode, so model comparisons say *why* a model fails (Lab 03/06)."""
    st = out.get("stats") or {}
    if row.get("error"):
        return "crash/timeout"
    if row["valid"]:
        return "ok" if row["verdict_ok"] and row["cause_ok"] else "wrong_decision"
    if st.get("turn_limit"):
        return "turn_limit"
    if st.get("unknown_tools") or st.get("tool_errors"):
        return "bad_tool_calls"
    return "no_assessment"   # answered in prose / never called submit_assessment


def score(ep, out: Dict[str, Any], err) -> Dict[str, Any]:
    a, tr = out.get("assessment"), ep.truth
    row = {"scenario": ep.scenario, "seed": ep.seed, "truth_verdict": tr.verdict, "truth_cause": tr.cause,
           "valid": a is not None, "error": err, "tool_calls": out.get("steps", 0),
           "report_chars": len(out.get("report") or "")}
    if a is None:
        row.update(verdict=None, cause=None, verdict_ok=False, cause_ok=False, loc_err=None, unrel_f1=0.0)
        return row
    row.update(verdict=a.get("verdict"), cause=a.get("cause"),
               verdict_ok=a.get("verdict") == tr.verdict, cause_ok=a.get("cause") == tr.cause)
    loc = a.get("location")
    if tr.fire_xy and tr.cause == "wildfire":
        try:
            row["loc_err"] = round(math.hypot(float(loc["x"]) - tr.fire_xy[0], float(loc["y"]) - tr.fire_xy[1]))
        except (TypeError, KeyError, ValueError):
            row["loc_err"] = None
    else:
        row["loc_err"] = None
    pred, gold = set(map(int, a.get("unreliable_nodes") or [])), set(tr.unreliable)
    if not pred and not gold:
        row["unrel_f1"] = 1.0
    else:
        tp = len(pred & gold)
        row["unrel_f1"] = round(2 * tp / (len(pred) + len(gold)), 3)
    return row


def _acc(rows: List[Dict[str, Any]]):
    return round(sum(r["verdict_ok"] and r["cause_ok"] for r in rows) / len(rows), 3) if rows else None


def _reduction(rows: List[Dict[str, Any]]):
    """1 - (false-alarm events after the agent acted) / (events with no agent), non-fire episodes."""
    act = sum(r["spurious_act"] for r in rows if "spurious_act" in r)
    ctrl = sum(r["spurious_ctrl"] for r in rows if "spurious_ctrl" in r)
    return round(1 - act / ctrl, 3) if ctrl else None


def summarize(rows: List[Dict[str, Any]], holdout=()) -> Dict[str, Any]:
    n = len(rows)
    ood = [r for r in rows if r["scenario"] in holdout]
    fire = [r for r in rows if r["truth_verdict"] == "ALERT"]
    nofire = [r for r in rows if r["truth_verdict"] != "ALERT"]
    locs = [r["loc_err"] for r in fire if r["loc_err"] is not None]
    per = {}
    for r in rows:
        per.setdefault(r["scenario"], []).append(r["verdict_ok"] and r["cause_ok"])
    return {
        "n": n,
        "valid_assessment": round(sum(r["valid"] for r in rows) / n, 3),
        "verdict_acc": round(sum(r["verdict_ok"] for r in rows) / n, 3),
        "cause_acc": round(sum(r["cause_ok"] for r in rows) / n, 3),
        "false_alarm_rate": round(sum(r["verdict"] == "ALERT" for r in nofire) / max(1, len(nofire)), 3),
        "miss_rate": round(sum(r["verdict"] != "ALERT" for r in fire) / max(1, len(fire)), 3),
        "loc_err_m_median": sorted(locs)[len(locs) // 2] if locs else None,
        "unreliable_f1": round(sum(r["unrel_f1"] for r in rows) / n, 3),
        "avg_tool_calls": round(sum(r["tool_calls"] for r in rows) / n, 2),
        "failures": dict(sorted(Counter(r.get("failure", "ok") for r in rows).items())),
        "sec_per_episode": round(sum(r.get("seconds") or 0 for r in rows) / n, 1),
        "fallback_parse_rate": round(sum(bool(r.get("fallback_parses")) for r in rows) / n, 3),
        "tok_per_s": (lambda v: round(sum(v) / len(v), 1) if v else None)([r["tok_per_s"] for r in rows if r.get("tok_per_s")]),
        "tight_budget_acc": _acc([r for r in rows if r.get("budget") in ("tight", "exhausted")]),
        "airtime_s_per_episode": round(sum(r.get("airtime_s", 0) for r in rows) / n, 2),
        "correct_per_airtime_s": round(sum(r["verdict_ok"] and r["cause_ok"] for r in rows)
                                       / max(0.01, sum(r.get("airtime_s", 0) for r in rows)), 2),
        "spurious_event_reduction": _reduction(rows),
        "unseen_composition_acc": round(sum(r["verdict_ok"] and r["cause_ok"] for r in ood) / len(ood), 3)
        if ood else None,
        "per_scenario": {k: round(sum(v) / len(v), 2) for k, v in sorted(per.items())},
    }


def report(paths: List[str]) -> None:
    res = [json.load(open(p)) for p in paths]
    keys = ["valid_assessment", "verdict_acc", "cause_acc", "false_alarm_rate", "miss_rate",
            "loc_err_m_median", "unreliable_f1", "unseen_composition_acc", "tight_budget_acc",
            "airtime_s_per_episode", "correct_per_airtime_s", "spurious_event_reduction", "avg_tool_calls",
            "failures", "fallback_parse_rate", "sec_per_episode", "tok_per_s"]
    print("| metric | " + " | ".join(r["name"] for r in res) + " |")
    print("|---|" + "---|" * len(res))
    for k in keys:
        print(f"| {k} | " + " | ".join(str(r["summary"].get(k)) for r in res) + " |")
    for sc in res[0]["summary"]["per_scenario"]:
        print(f"| {sc} | " + " | ".join(str(r["summary"]["per_scenario"].get(sc)) for r in res) + " |")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", choices=["heuristic", "oracle", "llm"], default="heuristic")
    ap.add_argument("--url")
    ap.add_argument("--model")
    ap.add_argument("--key")
    ap.add_argument("--name")
    ap.add_argument("--specs", default="data/eval_scenarios.jsonl")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="results")
    ap.add_argument("--report", nargs="*")
    args = ap.parse_args()
    if args.report:
        return report(args.report)
    specs = [json.loads(l) for l in open(args.specs)]
    if args.limit:   # stratified: first N per scenario
        by: Dict[str, list] = {}
        for s in specs:
            by.setdefault(s["scenario"], []).append(s)
        specs = [s for v in by.values() for s in v[:args.limit]]
    name = args.name or (args.model if args.agent == "llm" else args.agent)
    t0 = time.time()
    workers = args.workers if args.agent == "llm" else 1
    with ThreadPoolExecutor(workers) as ex:
        rows = list(ex.map(lambda s: run_one(s, args), specs))
    manifest = Path(args.specs).parent / "manifest.json"
    holdout = json.load(open(manifest))["holdout"] if manifest.exists() else []
    summ = summarize(rows, holdout)
    Path(args.out).mkdir(exist_ok=True)
    path = Path(args.out) / f"{name}.json"
    json.dump({"name": name, "summary": summ, "rows": rows, "seconds": round(time.time() - t0)},
              open(path, "w"), indent=1)
    print(json.dumps(summ, indent=1))
    print("saved", path)


if __name__ == "__main__":
    main()
