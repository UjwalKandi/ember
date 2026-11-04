"""Command-center view of the swarm: node table, packet log, query/response
matching, and the compact text context that Muse Glimmer reasons over."""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional, Tuple

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
        self._lock = threading.Lock()

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

    def request(self, node: int, cmd: str, timeout: float = 8.0, **args) -> Optional[elp.Packet]:
        """Send a command to one node and wait for its R packet (None on timeout)."""
        qid = self.next_qid()
        ev, box = threading.Event(), []
        with self._lock:
            self._waiters[(node, qid)] = (ev, box)
        try:
            self.send(elp.command(f"n{node}", qid, cmd, **args))
            if not box and not all(getattr(t, "synchronous", False) for t in self.transports):
                ev.wait(timeout)
            return box[0] if box else None
        finally:
            with self._lock:
                self._waiters.pop((node, qid), None)

    def broadcast(self, cmd: str, **args) -> int:
        qid = self.next_qid()
        self.send(elp.command("*", qid, cmd, **args))
        return qid

    # --- views -----------------------------------------------------------

    def history(self, node: int, limit: int = 12) -> List[LogEntry]:
        with self._lock:
            return [e for e in self.log if e.src == node][-limit:]

    def context(self, window: float = 600.0, max_events: int = 40) -> str:
        """Render the swarm as compact text for the model (the 'observation')."""
        now = self.clock()
        lines = [
            f"AREA {self.area} | WIND {self.wind.describe()} | units: meters, +x=east +y=north",
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
                "hz": list(n.hz_hist)[-1:] or [0],
            } for n in sorted(self.nodes.values(), key=lambda n: n.id)],
        }
