"""Model bake-off on EMBER's own eval (Lab 03): which small model is the best starting point?

For each model it starts llama-server (downloading the GGUF on first use), waits until it is
healthy, runs the agent eval on a few episodes per scenario, stops the server and collects the
results into results/bakeoff.md. Choosing a model from measurements on *your* task is the skill.

    python training/bakeoff.py --models qwen3-0.6b,qwen3-1.7b,twil-lm3 --limit 1
    python training/bakeoff.py --list
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("EMBER_LLM_PORT", "8081"))


def wait_healthy(proc: subprocess.Popen, timeout: float) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(3)
    return False


def run_model(m: dict, limit: int, ctx: int, download_timeout: float) -> dict:
    results, logs = ROOT / "results", ROOT / "results" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = open(logs / f"llama-server-{m['name']}.log", "w")
    cmd = ["llama-server", "-hf", m["gguf"], "--alias", "local", "--port", str(PORT),
           "-c", str(ctx), "-np", "1", "--jinja", *m.get("server_args", [])]
    print(f"\n=== {m['name']}: starting {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    try:
        if not wait_healthy(proc, download_timeout):
            return {"name": m["name"], "error": f"server did not start (see results/logs/llama-server-{m['name']}.log)"}
        env = {**os.environ, "EMBER_LLM_EXTRA": json.dumps(m.get("request_extra") or {})}
        name = f"bakeoff-{m['name']}"
        t0 = time.time()
        subprocess.run([sys.executable, "-m", "training.eval", "--agent", "llm", "--url", f"http://127.0.0.1:{PORT}/v1",
                        "--model", "local", "--key", "none", "--limit", str(limit), "--workers", "1", "--name", name],
                       cwd=ROOT, env=env, check=True)
        summary = json.load(open(results / f"{name}.json"))["summary"]
        return {"name": m["name"], "summary": summary, "minutes": round((time.time() - t0) / 60, 1)}
    except subprocess.CalledProcessError as e:
        return {"name": m["name"], "error": f"eval failed: {e}"}
    finally:
        proc.terminate()
        try:
            proc.wait(20)
        except subprocess.TimeoutExpired:
            proc.kill()


def write_report(models: dict, runs: list) -> Path:
    out = ROOT / "results" / "bakeoff.md"
    head = ["model", "size (B)", "license", "valid", "verdict", "cause", "false alarms", "fallback parse",
            "sec/episode", "tok/s", "failures"]
    lines = ["# Model bake-off (EMBER agent eval)", "",
             "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in runs:
        m = models[r["name"]]
        if "error" in r:
            lines.append(f"| {r['name']} | {m['size_b']} | {m['license']} | error: {r['error']} |" + " |" * (len(head) - 4))
            continue
        s = r["summary"]
        lines.append("| " + " | ".join(str(x) for x in [
            r["name"], m["size_b"], m["license"], s["valid_assessment"], s["verdict_acc"], s["cause_acc"],
            s["false_alarm_rate"], s.get("fallback_parse_rate"), s.get("sec_per_episode"), s.get("tok_per_s"),
            json.dumps(s.get("failures"))]) + " |")
    lines += ["", "Read it like an engineer: `valid` = the model managed to call submit_assessment at all (tool-use "
              "competence); verdict/cause = decision quality; failures = *why* it failed. Pick the smallest model "
              "with good tool-use competence; SFT (Lab 04) mostly fixes decision quality."]
    out.write_text("\n".join(lines) + "\n")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="qwen3-0.6b,qwen3-1.7b,qwen3-4b,smollm3-3b,twil-lm3")
    ap.add_argument("--limit", type=int, default=1, help="episodes per scenario (14 scenarios)")
    ap.add_argument("--ctx", type=int, default=16384)
    ap.add_argument("--download-timeout", type=float, default=1800)
    ap.add_argument("--config", default=str(ROOT / "config" / "models.json"))
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    models = {m["name"]: m for m in json.load(open(args.config))["models"]}
    if args.list:
        for m in models.values():
            print(f"{m['name']:18s} {m['size_b']:>5}B  {m['license']:28s} {m['gguf']}  {m['notes']}")
        return
    names = [n.strip() for n in args.models.split(",") if n.strip()]
    unknown = [n for n in names if n not in models]
    if unknown:
        raise SystemExit(f"unknown models {unknown}; see --list")
    runs = [run_model(models[n], args.limit, args.ctx, args.download_timeout) for n in names]
    print("\nreport:", write_report(models, runs))


if __name__ == "__main__":
    main()
