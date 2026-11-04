"""Command-center view of the swarm: node table, packet log, query/response
matching, and the compact text context that Muse Glimmer reasons over."""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional, Tuple

from . import airtime
from . import protocol as elp
from .sim import Wind


@dataclass
class LogEntry:
    t: float
    src: int
    hops: int
    rssi: int
    pkt: elp.Packet


@dataclass
class NodeState:
    id: int
    x: float
    y: float
    last_seen: Optional[float] = None
    hops: int = 0
    rssi: int = 0
    batt: Optional[float] = None
    rate: int = 30
    code: str = "OK"
    conf: int = 0
    hz_hist: Deque[int] = field(default_factory=lambda: deque(maxlen=5))
    snd_hist: Deque[int] = field(default_factory=lambda: deque(maxlen=5))
    tuning: Dict[str, int] = field(default_factory=dict)    # detector params set over the mesh


class AirtimeExhausted(RuntimeError):
    pass


# typical reply bodies, used to price a request before the reply exists
_REPLY_EST = {"PING": "R|n3|q77|PONG|b3.9", "CONFIRM": "R|n3|q77|CONF|FLM|c88|hz40|snd20",
              "SET": "R|n3|q77|ACK"}


class Transport:
    """Anything that can flood a packet into the mesh (serial gateway, SimNetwork, ...).
    Synchronous transports deliver any reply inside send(), so there is nothing to wait for."""
    synchronous = False

    def send(self, pkt: elp.Packet) -> None: ...


class Swarm:
    def __init__(self, nodes: Dict[int, Tuple[float, float]], wind: Wind,
                 clock: Callable[[], float] = time.monotonic, area: str = "Sector A"):
        self.nodes = {nid: NodeState(nid, x, y) for nid, (x, y) in nodes.items()}
        self.wind = wind
        self.clock = clock
        self.area = area
        self.log: List[LogEntry] = []
        self.transports: List[Transport] = []
        self.listeners: List[Callable[[LogEntry], None]] = []
        self._qid = 0
        self._waiters: Dict[Tuple[int, int], Tuple[threading.Event, List[elp.Packet]]] = {}
        self._collect: Dict[int, List[elp.Packet]] = {}          # broadcast qid -> replies
        self._lock = threading.Lock()
        # channel airtime the command center spends (commands + the replies they trigger),
        # against a regulatory duty-cycle budget: 1% of an hour = 36 s
        self.airtime_budget_ms = 36_000.0
        self.airtime_log: List[Tuple[float, float]] = []

    # --- ingest ----------------------------------------------------------

    def on_rx(self, rx: elp.RxLine, t: Optional[float] = None) -> None:
        t = self.clock() if t is None else t
        p = rx.packet
        nid = p.node if p.node is not None else rx.src
        n = self.nodes.get(nid)
        if n is None:   # unknown node joined: place it at the origin until configured
            n = self.nodes[nid] = NodeState(nid, 0.0, 0.0)
        n.last_seen, n.hops, n.rssi = t, rx.hops, rx.rssi
        if p.type == "H":
            n.batt = p.get("b") or n.batt      # boards without a battery divider omit b (or send b0)
            n.hz_hist.append(int(p.get("hz", 0)))
            n.snd_hist.append(int(p.get("snd", 0)))
        elif p.type == "E":
            n.code, n.conf = p.code or "OK", int(p.get("c", 0))
        elif p.type == "R":
            if p.code == "PONG":
                n.batt = p.get("b") or n.batt
            key = (nid, int(p.get("q", -1)))
            with self._lock:
                w = self._waiters.get(key)
                coll = self._collect.get(key[1])
            if coll is not None:
                coll.append(p)
                self.spend(airtime.packet_ms(p.raw or p.encode(), rx.hops))
            if w:
                w[1].append(p)
                w[0].set()
        entry = LogEntry(t, nid, rx.hops, rx.rssi, p)
        with self._lock:
            self.log.append(entry)
        for cb in self.listeners:
            cb(entry)

    # --- commands --------------------------------------------------------

    def next_qid(self) -> int:
        self._qid = (self._qid % 999) + 1
        return self._qid

    def send(self, pkt: elp.Packet) -> None:
        for tr in self.transports:
            tr.send(pkt)

    # --- airtime ledger ---------------------------------------------------

    def airtime_used_ms(self) -> float:
        now = self.clock()
        return sum(ms for t, ms in self.airtime_log if now - t < 3600)

    def airtime_left_ms(self) -> float:
        return max(0.0, self.airtime_budget_ms - self.airtime_used_ms())

    def spend(self, ms: float) -> None:
        self.airtime_log.append((self.clock(), ms))

    def max_hops(self) -> int:
        return max((n.hops for n in self.nodes.values()), default=0)

    def request_cost_ms(self, node: int, cmd: str, **args) -> float:
        body = elp.command(f"n{node}", 999, cmd, **args).encode()
        return airtime.round_trip_ms(body, _REPLY_EST.get(cmd, "R|n3|q77|ACK"), self.nodes[node].hops)

    def broadcast_cost_ms(self, cmd: str, expect_replies: bool = False, **args) -> float:
        body = elp.command("*", 999, cmd, **args).encode()
        cost = airtime.packet_ms(body, self.max_hops())
        if expect_replies:
            cost += sum(airtime.packet_ms("R|n3|q77|ACK", n.hops) for n in self.nodes.values())
        return cost

    # --- commands --------------------------------------------------------

    def request(self, node: int, cmd: str, timeout: float = 8.0, **args) -> Optional[elp.Packet]:
        """Send a command to one node and wait for its R packet (None on timeout).
        Raises AirtimeExhausted if the duty-cycle budget can't cover the round trip."""
        est = self.request_cost_ms(node, cmd, **args)
        if est > self.airtime_left_ms():
            raise AirtimeExhausted(f"{cmd} to n{node} needs ~{est / 1000:.1f}s airtime, "
                                   f"{self.airtime_left_ms() / 1000:.1f}s left this hour")
        qid = self.next_qid()
        ev, box = threading.Event(), []
        with self._lock:
            self._waiters[(node, qid)] = (ev, box)
        try:
            pkt = elp.command(f"n{node}", qid, cmd, **args)
            self.send(pkt)
            hops = self.nodes[node].hops
            self.spend(airtime.packet_ms(pkt.encode(), hops))
            if not box and not all(getattr(t, "synchronous", False) for t in self.transports):
                ev.wait(timeout)
            if box:
                self.spend(airtime.packet_ms(box[0].raw or box[0].encode(), hops))
            return box[0] if box else None
        finally:
            with self._lock:
                self._waiters.pop((node, qid), None)

    def broadcast(self, cmd: str, collect_s: float = 0.0, **args) -> Tuple[int, List[elp.Packet]]:
        """Flood a command to every node. Optionally collect replies for collect_s seconds."""
        est = self.broadcast_cost_ms(cmd, expect_replies=collect_s > 0, **args)
        if est > self.airtime_left_ms():
            raise AirtimeExhausted(f"broadcast {cmd} needs ~{est / 1000:.1f}s airtime, "
                                   f"{self.airtime_left_ms() / 1000:.1f}s left this hour")
        qid = self.next_qid()
        replies: List[elp.Packet] = []
        with self._lock:
            self._collect[qid] = replies
        try:
            pkt = elp.command("*", qid, cmd, **args)
            self.send(pkt)
            self.spend(airtime.packet_ms(pkt.encode(), self.max_hops()))
            if collect_s and not all(getattr(t, "synchronous", False) for t in self.transports):
                time.sleep(collect_s)
            return qid, list(replies)
        finally:
            with self._lock:
                self._collect.pop(qid, None)

    # --- views -----------------------------------------------------------

    def history(self, node: int, limit: int = 12) -> List[LogEntry]:
        with self._lock:
            return [e for e in self.log if e.src == node][-limit:]

    def context(self, window: float = 600.0, max_events: int = 40) -> str:
        """Render the swarm as compact text for the model (the 'observation')."""
        now = self.clock()
        h0 = self.request_cost_ms(next(iter(self.nodes)), "CONFIRM") / (next(iter(self.nodes.values())).hops + 1) \
            if self.nodes else 0.0
        lines = [
            f"AREA {self.area} | WIND {self.wind.describe()} | units: meters, +x=east +y=north",
            f"AIRTIME used {self.airtime_used_ms() / 1000:.1f}s of {self.airtime_budget_ms / 1000:.0f}s this hour "
            f"(LoRa LongFast, 1% duty) | CONFIRM round trip ~{h0 / 1000:.1f}s x (hops+1); history is free",
            "NODES id x,y | seen | hops | batt | hz(last5) | snd(last5) | state",
        ]
        for n in sorted(self.nodes.values(), key=lambda n: n.id):
            seen = "never" if n.last_seen is None else f"{int(now - n.last_seen)}s ago"
            batt = f"{n.batt:.1f}V" if n.batt is not None else "?"
            hz = ",".join(map(str, n.hz_hist)) or "-"
            snd = ",".join(map(str, n.snd_hist)) or "-"
            state = n.code if n.code == "OK" else f"{n.code} c{n.conf}"
            lines.append(f"n{n.id} {n.x:.0f},{n.y:.0f} | {seen} | h{n.hops} | {batt} | {hz} | {snd} | {state}")
        with self._lock:
            # only node-initiated events: command replies reach the model through tool results
            recent = [e for e in self.log if now - e.t <= window and e.pkt.type == "E"]
        recent = recent[-max_events:]
        lines.append(f"EVENTS last {int(window)}s ({len(recent)}):")
        for e in recent:
            lines.append(f"T-{int(now - e.t):04d} h{e.hops} {e.pkt.raw or e.pkt.encode()}")
        if not recent:
            lines.append("(none)")
        return "\n".join(lines)

    def context_json(self, window: float = 600.0, max_events: int = 40) -> str:
        """The same observation as context(), as conventional JSON telemetry (for token comparisons)."""
        import json
        now = self.clock()
        names = {"s": "seq", "c": "confidence", "hz": "haze", "snd": "sound", "b": "battery_v", "up": "uptime_s"}
        with self._lock:
            recent = [e for e in self.log if now - e.t <= window and e.pkt.type == "E"][-max_events:]
        return json.dumps({
            "area": self.area, "wind": {"toward_deg": round(self.wind.toward_deg), "kmh": round(self.wind.kmh)},
            "airtime": {"used_s": round(self.airtime_used_ms() / 1000, 1), "budget_s": self.airtime_budget_ms / 1000},
            "nodes": [{"id": n.id, "x": round(n.x), "y": round(n.y),
                       "last_seen_s": None if n.last_seen is None else int(now - n.last_seen),
                       "hops": n.hops, "battery_v": n.batt, "haze_last5": list(n.hz_hist),
                       "sound_last5": list(n.snd_hist), "state": n.code, "confidence": n.conf}
                      for n in sorted(self.nodes.values(), key=lambda n: n.id)],
            "events": [{"age_s": int(now - e.t), "hops": e.hops, "type": "event", "node": e.src,
                        "code": e.pkt.code, **{names.get(k, k): v for k, v in e.pkt.fields.items()}}
                       for e in recent],
        })

    def snapshot(self) -> dict:
        """JSON-able state for the dashboard."""
        now = self.clock()
        return {
            "t": now,
            "wind": {"toward_deg": self.wind.toward_deg, "kmh": self.wind.kmh},
            "nodes": [{
                "id": n.id, "x": n.x, "y": n.y, "code": n.code, "conf": n.conf,
                "batt": n.batt, "hops": n.hops, "rssi": n.rssi,
                "age": None if n.last_seen is None else round(now - n.last_seen, 1),
                "hz": list(n.hz_hist)[-1:] or [0], "tuning": n.tuning,
            } for n in sorted(self.nodes.values(), key=lambda n: n.id)],
            "airtime": {"used_s": round(self.airtime_used_ms() / 1000, 1),
                        "budget_s": round(self.airtime_budget_ms / 1000)},
        }
