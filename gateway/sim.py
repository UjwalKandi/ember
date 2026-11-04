"""Virtual EMBER swarm: wildfire + false-alarm scenarios with known ground truth.

The simulator speaks ELP exactly like the firmware does, so the same gateway,
agent and tools run against virtual nodes, real nodes, or a mix of both. It is
also the source of all SFT training data (labels come from ground truth).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from . import protocol as elp

SCENARIOS = (
    "quiet", "wildfire", "wildfire_early", "wildfire_node_lost",
    "campfire", "sun_glare", "vehicle", "stuck_sensor", "node_offline",
    "wildfire_plus_glare", "fog", "vehicle_dust", "wildfire_in_fog", "wildfire_plus_stuck",
)

DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def compass(deg: float) -> str:
    return DIRS[int(((deg % 360) + 22.5) // 45) % 8]


@dataclass
class Wind:
    toward_deg: float   # direction the wind blows toward, 0 = north, 90 = east
    kmh: float

    @property
    def vec(self) -> Tuple[float, float]:
        r = math.radians(self.toward_deg)
        return math.sin(r), math.cos(r)

    def describe(self) -> str:
        return f"toward {compass(self.toward_deg)} ({self.toward_deg:.0f}deg) {self.kmh:.0f} km/h"


@dataclass
class Fire:
    x: float
    y: float
    t0: float
    spread_mps: float = 0.25          # base radial spread
    kind: str = "wildfire"           # wildfire | campfire
    consumes: bool = False           # nodes inside the burned area go offline

    def radius(self, t: float, wind: Wind) -> float:
        if t < self.t0:
            return -1.0
        if self.kind == "campfire":
            return 2.0
        return 5.0 + self.spread_mps * (1 + wind.kmh / 20) * (t - self.t0)

    def center(self, t: float, wind: Wind) -> Tuple[float, float]:
        # fire front drifts downwind as it grows
        if self.kind == "campfire" or t < self.t0:
            return self.x, self.y
        drift = 0.4 * self.radius(t, wind)
        wx, wy = wind.vec
        return self.x + wx * drift, self.y + wy * drift


@dataclass
class Truth:
    scenario: str
    verdict: str                     # ALERT | WATCH | DISMISS
    cause: str                       # wildfire | campfire | sun_glare | vehicle | sensor_fault | none
    fire_xy: Optional[Tuple[float, float]] = None
    severity: int = 0                # 0-3
    affected: List[int] = field(default_factory=list)
    unreliable: List[int] = field(default_factory=list)


@dataclass
class Readings:
    flame: int = 0
    haze: int = 0
    sound: int = 0
    crackle: int = 0
    motion: int = 0


class VirtualNode:
    def __init__(self, nid: int, x: float, y: float, rng: random.Random, rate: int = 30):
        self.id, self.x, self.y = nid, x, y
        self.rng = rng
        self.rate = rate
        self.seq = 0
        self.batt = round(rng.uniform(3.7, 4.1), 2)
        self.drain = 0.0                       # volts per second (node_offline scenario)
        self.uptime0 = rng.randint(600, 40000)
        self.alive = True
        self.stuck_haze: Optional[int] = None
        self.glare: Optional[Tuple[float, float, float]] = None   # (period, on_seconds, phase)
        self.cand, self.cand_n = "OK", 0
        self.code = "OK"
        self.last_conf = 0
        self.next_hb = rng.uniform(0, rate)
        self.max_hz = 0
        self.max_snd = 0
        self.hops = 0
        self.rssi = -50

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq


class SimNetwork:
    """A virtual mesh. Implements the gateway Transport interface (send + on_rx)."""
    synchronous = True

    def __init__(self, nodes: Dict[int, Tuple[float, float]], wind: Wind, seed: int = 0,
                 gateway_xy: Tuple[float, float] = (0.0, 0.0)):
        self.rng = random.Random(seed)
        self.wind = wind
        self.t = 0.0
        self.nodes = {nid: VirtualNode(nid, x, y, self.rng) for nid, (x, y) in nodes.items()}
        self.fires: List[Fire] = []
        self.vehicle: Optional[Tuple[float, float, float, float, float]] = None  # x0,y0,vx,vy,t0
        self.vehicle_dust = False         # dirt road: the vehicle leaves a short-lived dust plume
        self.fog: Optional[Tuple[float, float]] = None   # (haze level, onset time): regional, uniform
        self.on_rx: Optional[Callable[[elp.RxLine, float], None]] = None
        self.truth = Truth("quiet", "DISMISS", "none")
        self.packet_loss = 0.03
        gx, gy = gateway_xy
        for n in self.nodes.values():
            d = math.hypot(n.x - gx, n.y - gy)
            n.hops = min(3, int(d // 220))
            n.rssi = int(-45 - 0.08 * (d % 220) - self.rng.uniform(0, 8))

    # --- physics ---------------------------------------------------------

    def readings(self, n: VirtualNode, t: float, burst: bool = False) -> Readings:
        noise = (lambda s: self.rng.gauss(0, s * (0.5 if burst else 1.0)))
        flame = smoke = sound = 0.0
        wx, wy = self.wind.vec
        for f in self.fires:
            r = f.radius(t, self.wind)
            if r < 0:
                continue
            cx, cy = f.center(t, self.wind)
            d = math.hypot(n.x - cx, n.y - cy)
            if f.kind == "campfire":
                flame = max(flame, 0.55 if d < 40 else 0.0)
                smoke = max(smoke, 0.08 if d < 40 else 0.0)
                continue
            flame = max(flame, max(0.0, 1 - max(0.0, d - r) / 90))
            sound = max(sound, max(0.0, 1 - max(0.0, d - r) / 60))
            # gaussian-ish smoke plume downwind of the front
            dx, dy = n.x - cx, n.y - cy
            a = dx * wx + dy * wy                        # downwind distance
            lat = abs(-dx * wy + dy * wx)                # crosswind distance
            age = t - f.t0
            plume_len = 80 + age * (self.wind.kmh / 3.6) * 0.35
            if a > -r - 30:
                sigma = 40 + 0.35 * max(a, 0) + r
                conc = math.exp(-lat ** 2 / (2 * sigma ** 2)) * math.exp(-max(a, 0) / plume_len)
                conc *= min(1.0, age / 240)
                smoke = max(smoke, conc)
            smoke = max(smoke, max(0.0, 1 - max(0.0, d - r) / 140))
        rd = Readings(
            flame=int(_clip(95 * flame + noise(4))),
            haze=int(_clip(3 + 90 * smoke + noise(2.5))),
            sound=int(_clip(3 + 70 * sound + noise(2))),
            crackle=int(_clip(100 * sound - 10 + noise(5))),
        )
        if any(f.kind == "campfire" for f in self.fires) and flame > 0:
            rd.flame = int(_clip(rd.flame + self.rng.uniform(-12, 12)))
        if n.glare and (t + n.glare[2]) % n.glare[0] < n.glare[1]:
            # low sun reflecting into the lens: intense but static, no haze, no sound. The
            # CONFIRM burst checks flicker across frames, so glare mostly fails it.
            p = 0.15 if burst else 0.9
            if self.rng.random() < p:
                rd.flame = max(rd.flame, int(self.rng.uniform(70, 92)))
        if self.fog and t >= self.fog[1]:
            ramp = min(1.0, (t - self.fog[1]) / 120)
            rd.haze = int(_clip(rd.haze + ramp * (self.fog[0] + 4 * math.sin(t / 90 + n.id)) + noise(2)))
        if self.vehicle:
            x0, y0, vx, vy, vt0 = self.vehicle
            if t >= vt0:
                vxp, vyp = x0 + vx * (t - vt0), y0 + vy * (t - vt0)
                d = math.hypot(n.x - vxp, n.y - vyp)
                if self.vehicle_dust:
                    # dust hangs for ~30 s along the track behind the vehicle
                    for lag in (8, 16, 24, 32):
                        if t - lag >= vt0:
                            dl = math.hypot(n.x - (x0 + vx * (t - lag - vt0)), n.y - (y0 + vy * (t - lag - vt0)))
                            if dl < 55:
                                rd.haze = max(rd.haze, int(_clip(25 + 30 * (1 - dl / 55) * (1 - lag / 40) + noise(3))))
                if d < 60:
                    rd.motion = int(_clip(90 - d + noise(5)))
                    rd.sound = max(rd.sound, int(_clip(55 - d / 2 + noise(4))))
                    if d < 30 and self.rng.random() < 0.5:   # tail-lights read as flame color
                        rd.flame = max(rd.flame, int(self.rng.uniform(42, 60)))
        if n.stuck_haze is not None:
            rd.haze = n.stuck_haze
        return rd

    @staticmethod
    def classify(rd: Readings) -> Tuple[str, int]:
        """Same priority/thresholds as the firmware event state machine."""
        if rd.flame >= 40:
            return "FLM", rd.flame
        if rd.haze >= 25:
            return "SMK", rd.haze
        if rd.crackle >= 40:
            return "CRK", rd.crackle
        if rd.motion >= 40:
            return "MOT", rd.motion
        return "OK", 0

    # --- stepping --------------------------------------------------------

    def step(self, dt: float = 2.0) -> None:
        self.t += dt
        for n in self.nodes.values():
            if not n.alive:
                continue
            n.batt -= n.drain * dt
            if n.batt < 3.3 or self._burned(n):
                n.alive = False
                continue
            rd = self.readings(n, self.t)
            n.max_hz = max(n.max_hz, rd.haze)
            n.max_snd = max(n.max_snd, rd.sound)
            code, conf = self.classify(rd)
            # debounce like the firmware: MOT fires at once, detections need 2 samples, OK needs 3
            if code == n.code:
                n.cand_n = 0
                if code != "OK" and abs(conf - n.last_conf) > 15:
                    n.last_conf = conf
                    self._emit(n, elp.event(n.id, n.next_seq(), code, conf, rd.haze, rd.sound))
            else:
                n.cand_n = n.cand_n + 1 if code == n.cand else 1
                n.cand = code
                if n.cand_n >= {"MOT": 1, "OK": 3}.get(code, 2):
                    n.code, n.last_conf, n.cand_n = code, conf, 0
                    self._emit(n, elp.event(n.id, n.next_seq(), code, conf, rd.haze, rd.sound))
            if self.t >= n.next_hb:
                n.next_hb = self.t + n.rate
                up = int(n.uptime0 + self.t)
                self._emit(n, elp.heartbeat(n.id, n.next_seq(), n.batt, up, n.max_hz, n.max_snd))
                n.max_hz = n.max_snd = 0

    def run(self, seconds: float, dt: float = 2.0) -> None:
        end = self.t + seconds
        while self.t < end:
            self.step(dt)

    def _burned(self, n: VirtualNode) -> bool:
        for f in self.fires:
            if f.kind != "wildfire" or not f.consumes:
                continue
            cx, cy = f.center(self.t, self.wind)
            if math.hypot(n.x - cx, n.y - cy) < f.radius(self.t, self.wind) - 15:
                return True
        return False

    def _emit(self, n: VirtualNode, pkt: elp.Packet) -> None:
        if self.rng.random() < self.packet_loss * (1 + n.hops):
            return
        if self.on_rx:
            self.on_rx(elp.RxLine(n.id, n.hops, n.rssi + self.rng.randint(-3, 3), pkt), self.t)

    # --- Transport interface: commands from the gateway --------------------

    def send(self, pkt: elp.Packet) -> None:
        targets = list(self.nodes.values()) if pkt.target == "*" else (
            [self.nodes[pkt.node]] if pkt.node in self.nodes else [])
        qid = pkt.get("q", 0)
        for n in targets:
            if not n.alive:
                continue
            cmd = pkt.code
            if cmd == "PING":
                resp = elp.response(n.id, qid, "PONG", b=round(n.batt, 1))
            elif cmd == "CONFIRM":
                rd = self.readings(n, self.t, burst=True)
                code, conf = self.classify(rd)
                resp = elp.response(n.id, qid, "CONF", [code], c=conf, hz=rd.haze, snd=rd.sound)
            elif cmd == "SET":
                n.rate = int(pkt.get("rate", n.rate))
                n.next_hb = min(n.next_hb, self.t + n.rate)
                resp = elp.response(n.id, qid, "ACK")
            elif cmd == "ALERT":
                resp = elp.response(n.id, qid, "ACK")
            else:
                continue
            if self.on_rx:   # responses are retried by the firmware, so no loss here
                self.on_rx(elp.RxLine(n.id, n.hops, n.rssi, resp), self.t)

    def node_positions(self) -> Dict[int, Tuple[float, float]]:
        return {nid: (n.x, n.y) for nid, n in self.nodes.items()}


def _glare_schedule(rng: random.Random) -> Tuple[float, float, float]:
    period = rng.uniform(150, 260)
    return period, rng.uniform(50, 100), rng.uniform(0, period)


def _clip(v: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, v))


# --- scenario construction ---------------------------------------------------

def random_layout(rng: random.Random) -> Dict[int, Tuple[float, float]]:
    rows, cols = rng.choice([(2, 2), (2, 3), (3, 3), (3, 4), (2, 4)])
    sp = rng.uniform(120, 220)
    nodes, nid = {}, 1
    for r in range(rows):
        for c in range(cols):
            nodes[nid] = (round(c * sp + rng.uniform(-25, 25)), round(r * sp + rng.uniform(-25, 25)))
            nid += 1
    return nodes


def build(scenario: str, seed: int, nodes: Optional[Dict[int, Tuple[float, float]]] = None,
          wind: Optional[Wind] = None) -> SimNetwork:
    """Create a SimNetwork with the scenario armed. Returns it at t=0 (nothing run yet)."""
    rng = random.Random(seed * 7919 + SCENARIOS.index(scenario))
    nodes = nodes or random_layout(rng)
    wind = wind or Wind(rng.uniform(0, 360), rng.uniform(4, 35))
    net = SimNetwork(nodes, wind, seed=seed)
    ids = sorted(nodes)
    xs = [p[0] for p in nodes.values()]
    ys = [p[1] for p in nodes.values()]
    pick = lambda: rng.choice(ids)
    truth = Truth(scenario, "DISMISS", "none")

    def fire_near(nid: int, kind: str = "wildfire", t0: float = 60.0) -> Fire:
        x, y = nodes[nid]
        reach = 60 if kind == "wildfire" else 28      # campfires must be inside a camera's view
        ang, d = rng.uniform(0, 2 * math.pi), rng.uniform(8, reach)
        f = Fire(x + d * math.cos(ang), y + d * math.sin(ang), t0, kind=kind)
        net.fires.append(f)
        return f

    if scenario == "quiet":
        pass
    elif scenario.startswith("wildfire"):
        f = fire_near(pick())
        f.spread_mps = rng.uniform(0.15, 0.45)
        if scenario == "wildfire_node_lost":
            f.consumes = True
            f.spread_mps = rng.uniform(0.35, 0.6)
        truth = Truth(scenario, "ALERT", "wildfire")
        far = max(ids, key=lambda i: math.hypot(nodes[i][0] - f.x, nodes[i][1] - f.y))
        if scenario == "wildfire_plus_glare":
            net.nodes[far].glare = _glare_schedule(rng)
        elif scenario == "wildfire_in_fog":
            net.fog = (rng.uniform(22, 32), rng.uniform(0, 60))
        elif scenario == "wildfire_plus_stuck":
            net.nodes[far].stuck_haze = rng.randint(85, 99)
            truth.unreliable = [far]
    elif scenario == "campfire":
        fire_near(pick(), kind="campfire", t0=rng.uniform(0, 120))
        truth = Truth(scenario, "WATCH", "campfire")
    elif scenario == "sun_glare":
        net.nodes[pick()].glare = _glare_schedule(rng)
        truth = Truth(scenario, "DISMISS", "sun_glare")
    elif scenario == "fog":
        net.fog = (rng.uniform(25, 40), rng.uniform(0, 120))
        truth = Truth(scenario, "DISMISS", "weather")
    elif scenario in ("vehicle", "vehicle_dust"):
        net.vehicle_dust = scenario == "vehicle_dust"
        # a trail passing right by one node, crossing the whole grid
        ang = rng.uniform(0, 2 * math.pi)
        cx, cy = nodes[pick()]
        cx, cy = cx + rng.uniform(-20, 20), cy + rng.uniform(-20, 20)
        span = max(max(xs) - min(xs), max(ys) - min(ys), 200) + 100
        sp = rng.uniform(6, 14)
        x0, y0 = cx - math.cos(ang) * span, cy - math.sin(ang) * span
        net.vehicle = (x0, y0, math.cos(ang) * sp, math.sin(ang) * sp, rng.uniform(60, 150))
        truth = Truth(scenario, "DISMISS", "vehicle")
    elif scenario == "stuck_sensor":
        k = pick()
        net.nodes[k].stuck_haze = rng.randint(85, 99)
        truth = Truth(scenario, "DISMISS", "sensor_fault", unreliable=[k])
    elif scenario == "node_offline":
        k = pick()
        net.nodes[k].batt = 3.45
        net.nodes[k].drain = rng.uniform(0.0006, 0.0012)
        truth = Truth(scenario, "DISMISS", "none", unreliable=[k])
    else:
        raise ValueError(scenario)
    net.truth = truth
    return net


def assessment_time(scenario: str, rng: random.Random) -> float:
    """How long to run a scenario before the command center assesses it."""
    if scenario == "wildfire_early":
        return rng.uniform(150, 260)
    if scenario.startswith("wildfire"):
        return rng.uniform(300, 700)
    if scenario == "node_offline":
        return rng.uniform(400, 700)
    if scenario == "vehicle":
        return rng.uniform(200, 450)
    return rng.uniform(240, 600)


def finalize_truth(net: SimNetwork) -> Truth:
    """Fill in time-dependent truth (location, severity, affected nodes) at net.t."""
    tr = net.truth
    wild = [f for f in net.fires if f.kind == "wildfire"]
    camp = [f for f in net.fires if f.kind == "campfire"]
    if wild:
        f = wild[0]
        cx, cy = f.center(net.t, net.wind)
        tr.fire_xy = (round(cx), round(cy))
        r = f.radius(net.t, net.wind)
        tr.severity = 1 if r < 60 else 2 if r < 150 else 3
        tr.affected = sorted(n.id for n in net.nodes.values()
                             if not n.alive or _hot(net, n))
        tr.unreliable = sorted(set(tr.unreliable) | {n.id for n in net.nodes.values() if not n.alive})
    elif camp:
        tr.fire_xy = (round(camp[0].x), round(camp[0].y))
        tr.severity = 0
        tr.affected = sorted(n.id for n in net.nodes.values() if _hot(net, n))
    elif tr.scenario == "node_offline":
        tr.unreliable = sorted(n.id for n in net.nodes.values() if not n.alive or n.batt < 3.5)
    return tr


def _hot(net: SimNetwork, n: VirtualNode) -> bool:
    saved, fog = net.rng.getstate(), net.fog
    net.fog = None                      # fog haze is weather, not fire impact
    rd = net.readings(n, net.t, burst=True)
    net.rng.setstate(saved)
    net.fog = fog
    return (rd.flame >= 40 and not n.glare) or (rd.haze >= 25 and n.stuck_haze is None) or rd.crackle >= 40
