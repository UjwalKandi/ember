"""EMBER command center: live swarm dashboard + agent runner (stdlib only).

    python -m gateway.server --sim wildfire                       # all-virtual swarm
    python -m gateway.server --serial /dev/cu.usbmodem1101        # real nodes via gateway XIAO
    python -m gateway.server --serial /dev/cu.usbmodem1101 --sim quiet --nodes config/nodes.json   # mixed
    add --agent llm (default if EMBER_LLM_URL is set) or --agent heuristic
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import protocol as elp
from . import sim
from .agent import GlimmerAgent
from .llm import ChatClient
from .policy import HOT, HeuristicAgent
from .swarm import LogEntry, Swarm
from .tools import Toolbox

STATIC = Path(__file__).parent / "static"


class LockedSim:
    """Serializes the sim between the stepping thread and agent tool calls."""
    synchronous = True

    def __init__(self, net: sim.SimNetwork):
        self.net, self.lock = net, threading.RLock()

    def send(self, pkt: elp.Packet) -> None:
        with self.lock:
            self.net.send(pkt)


class CommandCenter:
    def __init__(self, args):
        self.args = args
        self.clients: List[queue.Queue] = []
        self.busy = threading.Lock()
        self.last_assess_t = -1e9
        self.pending_auto: Optional[float] = None
        self.auto = args.auto
        self.history: List[Dict[str, Any]] = []
        self.trace: List[Dict[str, Any]] = []
        self.bridge = None
        self.t0 = time.monotonic()
        self.real_ids: set = set()
        self.reset(args.sim, args.seed)
        if args.serial:
            from .bridge import SerialBridge
            self.bridge = SerialBridge(args.serial, lambda rx: self.swarm.on_rx(rx),
                                       on_log=lambda s: self.publish({"type": "log", "text": s}))
            self.swarm.transports.append(self.bridge)
            self._reset_hardware()
        threading.Thread(target=self._loop, daemon=True).start()

    # --- world setup -------------------------------------------------------

    def _layout(self) -> Tuple[Dict[int, Tuple[float, float]], Dict[int, Tuple[float, float]], sim.Wind]:
        """Returns (all node positions, virtual-only positions, wind)."""
        if self.args.nodes:
            cfg = json.load(open(self.args.nodes))
            nodes = {int(n["id"]): (n["x"], n["y"]) for n in cfg["nodes"]}
            real = {int(n["id"]) for n in cfg["nodes"] if n.get("real")}
            if not self.args.serial:
                real = set()
            w = cfg.get("wind", {"toward_deg": 45, "kmh": 15})
            return nodes, {k: v for k, v in nodes.items() if k not in real}, sim.Wind(w["toward_deg"], w["kmh"])
        rng = random.Random(self.args.seed)
        nodes = sim.random_layout(rng)
        return nodes, ({} if self.args.serial else nodes), sim.Wind(rng.uniform(0, 360), rng.uniform(8, 25))

    def reset(self, scenario: Optional[str], seed: int) -> None:
        nodes, virtual, wind = self._layout()
        self.real_ids = set(nodes) - set(virtual) if self.args.serial else set()
        self.realtime = bool(self.args.serial)
        self.net: Optional[sim.SimNetwork] = None
        self.locked: Optional[LockedSim] = None
        if scenario and virtual:
            self.net = sim.build(scenario, seed, nodes=virtual, wind=wind)
            self.locked = LockedSim(self.net)
        clock = (lambda: time.monotonic() - self.t0) if self.realtime else (lambda: self.net.t if self.net else 0.0)
        self.swarm = Swarm(nodes, wind, clock=clock, area=self.args.area)
        self.swarm.airtime_budget_ms = self.args.airtime_budget * 1000
        self.swarm.listeners.append(self._on_packet)
        if self.net:
            self.net.on_rx = self.swarm.on_rx
            self.swarm.transports.append(self.locked)
        if self.bridge:
            self.swarm.transports.append(self.bridge)
            self._reset_hardware()
        self.scenario, self.seed = scenario, seed
        self.last_assess_t, self.pending_auto, self.trace = -1e9, None, []
        self.publish({"type": "reset", "scenario": scenario, "seed": seed})

    def _reset_hardware(self) -> None:
        """Real boards keep detector tuning (SET smk/flk) in RAM across dashboard restarts and
        scenario changes; put them back to defaults so every session starts clean."""
        self.bridge.send(elp.command("*", self.swarm.next_qid(), "SET", smk=25, flk=0))
        self.publish({"type": "log", "text": "reset detector tuning on real boards (smk25, flk0)"})

    def ignite(self, x: float, y: float) -> None:
        if not self.net:
            return
        with self.locked.lock:
            self.net.fires.append(sim.Fire(x, y, self.net.t, spread_mps=0.35))
        self.publish({"type": "log", "text": f"ignition injected at ({x:.0f}, {y:.0f})"})

    # --- loop ----------------------------------------------------------------

    def _loop(self) -> None:
        last_state = 0.0
        while True:
            time.sleep(0.25)
            if self.net:
                with self.locked.lock:
                    if self.realtime:
                        target = time.monotonic() - self.t0
                        while self.net.t < target:
                            self.net.step(1.0)
                    else:
                        self.net.run(self.args.speed * 0.25, dt=1.0)
            now = self.swarm.clock()
            if self.pending_auto is not None and now >= self.pending_auto and not self.busy.locked():
                self.pending_auto = None
                self.assess(trigger="auto")
            if time.monotonic() - last_state > 0.5:
                last_state = time.monotonic()
                self.publish({"type": "state", **self.state()})

    def _on_packet(self, e: LogEntry) -> None:
        self.publish({"type": "pkt", "t": round(e.t, 1), "src": e.src, "hops": e.hops, "rssi": e.rssi,
                      "raw": e.pkt.raw or e.pkt.encode()})
        if (self.auto and e.pkt.type == "E" and e.pkt.code in HOT and self.pending_auto is None
                and e.t - self.last_assess_t > self.args.cooldown):
            self.pending_auto = e.t + self.args.settle     # let neighbors report before assessing

    # --- agent -------------------------------------------------------------

    def assess(self, trigger: str = "manual") -> bool:
        if not self.busy.acquire(blocking=False):
            return False
        threading.Thread(target=self._assess, args=(trigger,), daemon=True).start()
        return True

    def _assess(self, trigger: str) -> None:
        try:
            self.last_assess_t = self.swarm.clock()
            tools = Toolbox(self.swarm, timeout=8.0 if self.realtime else 0.0,
                            on_call=lambda n, a, r: None)
            self.trace = []

            def step(ev: Dict[str, Any]) -> None:
                self.trace.append(ev)
                self.publish({"type": "agent", **ev})

            self.publish({"type": "agent", "kind": "start", "trigger": trigger, "agent": self.args.agent})
            if self.args.agent == "llm":
                out = GlimmerAgent(self.swarm, tools, ChatClient()).run(on_step=step)
            else:
                def traced(name, args, result):
                    step({"kind": "call", "name": name, "args": args})
                    step({"kind": "result", "name": name, "result": result})
                tools.on_call = traced
                out = HeuristicAgent(self.swarm, tools).run()
                step({"kind": "report", "text": out["report"]})
            rec = {"t": round(self.swarm.clock()), "trigger": trigger, "assessment": out["assessment"],
                   "report": out["report"], "steps": out["steps"]}
            if self.net:
                rec["truth"] = sim.finalize_truth(self.net).__dict__
            self.history.append(rec)
            self.publish({"type": "assessment", **rec})
        except Exception as e:
            self.publish({"type": "agent", "kind": "error", "text": repr(e)})
        finally:
            self.busy.release()

    # --- views ---------------------------------------------------------------

    def state(self) -> Dict[str, Any]:
        s = self.swarm.snapshot()
        s.update(scenario=self.scenario, auto=self.auto, busy=self.busy.locked(), agent=self.args.agent,
                 real=bool(self.bridge), real_ids=sorted(self.real_ids))
        if self.net and self.args.show_truth:
            s["fires"] = [{"x": f.center(self.net.t, self.net.wind)[0], "y": f.center(self.net.t, self.net.wind)[1],
                           "r": max(0.0, f.radius(self.net.t, self.net.wind)), "kind": f.kind}
                          for f in self.net.fires if f.t0 <= self.net.t]
        return s

    def publish(self, ev: Dict[str, Any]) -> None:
        data = json.dumps(ev, default=str)
        for q in list(self.clients):
            try:
                q.put_nowait(data)
            except queue.Full:
                pass


def make_handler(cc: CommandCenter):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _json(self, obj: Any, code: int = 200) -> None:
            body = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                body = (STATIC / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/state":
                self._json({**cc.state(), "history": cc.history[-5:], "trace": cc.trace,
                            "scenarios": list(sim.SCENARIOS)})
            elif self.path == "/api/context":
                self._json({"context": cc.swarm.context()})
            elif self.path == "/events":
                q: queue.Queue = queue.Queue(maxsize=500)
                cc.clients.append(q)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    while True:
                        try:
                            data = q.get(timeout=15)
                            self.wfile.write(f"data: {data}\n\n".encode())
                        except queue.Empty:
                            self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    cc.clients.remove(q)
            else:
                self.send_error(404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/assess":
                self._json({"started": cc.assess()})
            elif self.path == "/api/scenario":
                cc.reset(body.get("scenario"), int(body.get("seed", random.randint(0, 9999))))
                self._json({"ok": True})
            elif self.path == "/api/ignite":
                cc.ignite(float(body["x"]), float(body["y"]))
                self._json({"ok": True})
            elif self.path == "/api/auto":
                cc.auto = bool(body.get("on"))
                self._json({"auto": cc.auto})
            else:
                self.send_error(404)

    return H


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", default=None, choices=list(sim.SCENARIOS), help="virtual scenario to run")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--serial", help="gateway XIAO serial port")
    ap.add_argument("--nodes", help="JSON layout (marks which ids are real hardware)")
    ap.add_argument("--agent", choices=["llm", "heuristic"],
                    default="llm" if os.environ.get("EMBER_LLM_URL") else "heuristic")
    ap.add_argument("--speed", type=float, default=8.0, help="sim seconds per wall second (sim-only)")
    ap.add_argument("--auto", action="store_true", help="assess automatically on fire indicators")
    ap.add_argument("--cooldown", type=float, default=180.0)
    ap.add_argument("--settle", type=float, default=40.0)
    ap.add_argument("--show-truth", action="store_true", help="draw simulated fires on the map")
    ap.add_argument("--area", default="Sector A")
    ap.add_argument("--airtime-budget", type=float, default=36.0,
                    help="command-center LoRa airtime per hour, seconds (36 = 1%% duty cycle)")
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to share the dashboard on the LAN")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    if not args.sim and not args.serial:
        args.sim = "quiet"
    cc = CommandCenter(args)
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(cc))
    print(f"EMBER command center on http://localhost:{args.port}  agent={args.agent} "
          f"sim={args.sim} serial={args.serial}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
