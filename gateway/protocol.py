"""Ember Link Protocol (ELP) v1 encode/parse. See protocol/SPEC.md."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

MAX_BODY = 200
TYPES = ("H", "E", "C", "R")
EVENT_CODES = ("FLM", "SMK", "CRK", "MOT", "OK")
COMMANDS = ("PING", "CONFIRM", "SET", "ALERT")
RESPONSES = ("PONG", "CONF", "ACK")

_KV = re.compile(r"^([a-z]+)(-?\d+(?:\.\d+)?)$")


class ProtocolError(ValueError):
    pass


@dataclass
class Packet:
    type: str                      # H | E | C | R
    target: str                    # "n3" or "*"
    codes: List[str] = field(default_factory=list)       # FLM, CONFIRM, CONF, ...
    fields: Dict[str, float] = field(default_factory=dict)  # s, c, hz, snd, b, up, q, ...
    raw: str = ""

    @property
    def node(self) -> Optional[int]:
        return int(self.target[1:]) if self.target.startswith("n") else None

    @property
    def code(self) -> Optional[str]:
        return self.codes[0] if self.codes else None

    def get(self, key: str, default=None):
        return self.fields.get(key, default)

    def encode(self) -> str:
        parts = [self.type, self.target]
        # ordering mirrors the spec examples: id-ish keys first, then codes, then readings
        for k in ("s", "q"):
            if k in self.fields:
                parts.append(f"{k}{_fmt(self.fields[k])}")
        parts.extend(self.codes)
        for k, v in self.fields.items():
            if k not in ("s", "q"):
                parts.append(f"{k}{_fmt(v)}")
        body = "|".join(parts)
        if len(body) > MAX_BODY:
            raise ProtocolError(f"body {len(body)}B exceeds {MAX_BODY}B")
        return body


def _fmt(v: float) -> str:
    if float(v).is_integer():
        return str(int(v))
    return f"{v:.1f}"


def parse(body: str) -> Packet:
    body = body.strip()
    if not body:
        raise ProtocolError("empty body")
    if len(body) > MAX_BODY:
        raise ProtocolError(f"body {len(body)}B exceeds {MAX_BODY}B")
    parts = body.split("|")
    if len(parts) < 2 or parts[0] not in TYPES:
        raise ProtocolError(f"bad type in {body!r}")
    target = parts[1]
    if not (target == "*" or re.fullmatch(r"n\d{1,3}", target)):
        raise ProtocolError(f"bad target in {body!r}")
    pkt = Packet(type=parts[0], target=target, raw=body)
    for tok in parts[2:]:
        if not tok:
            continue
        m = _KV.match(tok)
        if m:
            num = float(m.group(2))
            pkt.fields[m.group(1)] = int(num) if num.is_integer() else num
        elif tok.isupper():
            pkt.codes.append(tok)
        else:
            raise ProtocolError(f"bad token {tok!r} in {body!r}")
    return pkt


def heartbeat(node: int, seq: int, batt: float, uptime: int, haze: int, sound: int) -> Packet:
    return Packet("H", f"n{node}", fields={"s": seq, "b": round(batt, 1), "up": uptime,
                                            "hz": haze, "snd": sound})


def event(node: int, seq: int, code: str, conf: int, haze: int, sound: int) -> Packet:
    assert code in EVENT_CODES
    return Packet("E", f"n{node}", [code], {"s": seq, "c": conf, "hz": haze, "snd": sound})


def command(target: str, qid: int, cmd: str, **args: float) -> Packet:
    assert cmd in COMMANDS
    return Packet("C", target, [cmd], {"q": qid, **args})


def response(node: int, qid: int, kind: str, codes: Optional[List[str]] = None, **vals: float) -> Packet:
    assert kind in RESPONSES
    return Packet("R", f"n{node}", [kind] + (codes or []), {"q": qid, **vals})


# --- gateway serial lines -------------------------------------------------

@dataclass
class RxLine:
    src: int
    hops: int
    rssi: int
    packet: Packet


def parse_serial(line: str) -> Optional[RxLine]:
    """Parse `RX|src|hops|rssi|body`. Returns None for LOG and other non-RX lines."""
    line = line.strip()
    if not line.startswith("RX|"):
        return None
    _, src, hops, rssi, body = line.split("|", 4)
    return RxLine(int(src), int(hops), int(rssi), parse(body))


def tx_line(pkt: Packet) -> str:
    return f"TX|{pkt.encode()}\n"
