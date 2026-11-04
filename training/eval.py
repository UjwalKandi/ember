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


def run_one(spec: Dict[str, Any], args) -> Dict[str, Any]:
    ep = make_episode(spec["scenario"], spec["seed"])
    try:
        if args.agent == "heuristic":
            out = HeuristicAgent(ep.swarm, ep.tools).run()
        else:
            client = ChatClient(args.url, args.model, args.key)
            out = GlimmerAgent(ep.swarm, ep.tools, client).run()
        err = None
    except Exception as e:   # count crashes/timeouts as failures, keep going
        out, err = {"assessment": ep.tools.assessment, "report": "", "steps": 0}, repr(e)
    return score(ep, out, err)


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


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
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
        "per_scenario": {k: round(sum(v) / len(v), 2) for k, v in sorted(per.items())},
    }


def report(paths: List[str]) -> None:
    res = [json.load(open(p)) for p in paths]
    keys = ["valid_assessment", "verdict_acc", "cause_acc", "false_alarm_rate", "miss_rate",
            "loc_err_m_median", "unreliable_f1", "avg_tool_calls"]
    print("| metric | " + " | ".join(r["name"] for r in res) + " |")
    print("|---|" + "---|" * len(res))
    for k in keys:
        print(f"| {k} | " + " | ".join(str(r["summary"][k]) for r in res) + " |")
    for sc in res[0]["summary"]["per_scenario"]:
        print(f"| {sc} | " + " | ".join(str(r["summary"]["per_scenario"].get(sc)) for r in res) + " |")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", choices=["heuristic", "llm"], default="heuristic")
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
    name = args.name or (args.model if args.agent == "llm" else "heuristic")
    t0 = time.time()
    workers = 1 if args.agent == "heuristic" else args.workers
    with ThreadPoolExecutor(workers) as ex:
        rows = list(ex.map(lambda s: run_one(s, args), specs))
    summ = summarize(rows)
    Path(args.out).mkdir(exist_ok=True)
    path = Path(args.out) / f"{name}.json"
    json.dump({"name": name, "summary": summ, "rows": rows, "seconds": round(time.time() - t0)},
              open(path, "w"), indent=1)
    print(json.dumps(summ, indent=1))
    print("saved", path)


if __name__ == "__main__":
    main()
