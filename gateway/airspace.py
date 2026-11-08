"""Airspace state mirror for v2 RF arms (protocol/SPEC.md §4): access points, BLE trackers,
deauth/disassoc counts, live feature windows and arm health. Fed with RxLines from the serial
bridge or a capture replay; read by the dashboard (/airspace) as a JSON snapshot.

Captures are the raw board lines with a time prefix, `<seconds>\\t<line>` (see `read_capture`):
the same files are replayed into the dashboard and become L2 training data.
"""
from __future__ import annotations

import threading
import time
from collections import Counter, deque
from typing import Any, Callable, Deque, Dict, Iterator, List, Optional, Tuple

from . import protocol as elp

TRACKERS = {1: "Apple Find My", 2: "Tile", 3: "Samsung SmartTag", 4: "Google Find My"}
# W packet fields in wire order (= the L2 classifier's input vector)
W_FIELDS = ("ch", "dt", "bcn", "prq", "prs", "ath", "asc", "dea", "dis", "act", "bc", "spf", "nb", "nn", "nss",
            "ds", "pd", "sqa", "tsj", "iec", "rx", "ra")
WEAK_SEC = {"OPN": "open network: traffic is unencrypted", "WEP": "WEP: broken encryption",
            "W1": "WPA1/TKIP: deprecated"}


def is_rf(pkt: elp.Packet) -> bool:
    """True for packets from a v2 arm (v1 node packets go to the swarm)."""
    return (pkt.type in elp.RF_TYPES or (pkt.type == "E" and pkt.code in elp.RF_EVENT_CODES)
            or (pkt.type == "H" and "ap" in pkt.fields))


def parse_occ(s: str) -> Dict[int, int]:
    """`1=34,6=71` -> {1: 34, 6: 71} (frames/s per channel)."""
    out = {}
    for part in filter(None, (s or "").split(",")):
        ch, _, v = part.partition("=")
        if ch.isdigit() and v.isdigit():
            out[int(ch)] = int(v)
    return out


def read_capture(lines) -> Iterator[Tuple[Optional[float], str]]:
    """Capture lines -> (t or None, board line). `#` comments and blanks are skipped; lines
    without a time prefix (a plain serial log) yield t=None."""
    for raw in lines:
        raw = raw.rstrip("\n")
        if not raw.strip() or raw.startswith("#"):
            continue
        head, sep, rest = raw.partition("\t")
        try:
            yield (float(head), rest.strip()) if sep else (None, raw.strip())
        except ValueError:
            yield None, raw.strip()


class Airspace:
    def __init__(self, clock: Callable[[], float] = time.monotonic, max_windows: int = 240):
        self.clock = clock
        self._lock = threading.Lock()
        self.arms: Dict[int, Dict[str, Any]] = {}
        self.aps: Dict[str, Dict[str, Any]] = {}
        self.trackers: Dict[str, Dict[str, Any]] = {}
        self.windows: Deque[Dict[str, Any]] = deque(maxlen=max_windows)
        self.events: Deque[Dict[str, Any]] = deque(maxlen=50)
        self.deauth_total = 0
        self.spoofed_total = 0
        self.deauth_by_ch = [0] * 14
        self.counts: Counter = Counter()

    def on_rx(self, rx: elp.RxLine, t: Optional[float] = None) -> None:
        t = self.clock() if t is None else t
        p = rx.packet
        arm_id = p.node if p.node is not None else rx.src
        with self._lock:
            self.counts[p.type] += 1
            arm = self.arms.setdefault(arm_id, {"id": arm_id, "first_t": t, "windows": 0})
            arm["last_t"] = t
            if p.type == "W":
                self._window(arm, p, t)
            elif p.type == "A":
                self._ap(arm_id, p, t)
            elif p.type == "B":
                self._tracker(arm_id, p, t)
            elif p.type == "H":
                arm.update(up=p.get("up"), ap=p.get("ap"), ble=p.get("ble"), trk=p.get("trk"),
                           drp=p.get("drp"), occ=parse_occ(p.get("occ", "")))
            elif p.type == "E":
                self.events.append({"t": t, "arm": arm_id, "code": p.code,
                                    **{k: v for k, v in p.fields.items() if k != "s"}})

    def _window(self, arm: Dict[str, Any], p: elp.Packet, t: float) -> None:
        w = {k: int(p.get(k, 0)) for k in W_FIELDS}
        w.update(t=t, arm=arm["id"])
        self.windows.append(w)
        arm["windows"] += 1
        dea = w["dea"] + w["dis"]
        self.deauth_total += dea
        self.spoofed_total += w["spf"]
        if 1 <= w["ch"] <= 13:
            self.deauth_by_ch[w["ch"]] += dea

    def _ap(self, arm_id: int, p: elp.Packet, t: float) -> None:
        bssid = str(p.get("b", ""))
        ap = self.aps.get(bssid)
        if ap is None:
            ap = self.aps[bssid] = {"bssid": bssid, "first_t": t, "ie_changes": 0, "rssi_by_arm": {}}
        elif ap.get("ie") not in (None, "0000") and p.get("ie") != ap.get("ie"):
            ap["ie_changes"] += 1
        hidden = bool(p.get("hid", 0))
        ap.update(ssid="" if hidden else str(p.get("ss", "")), hidden=hidden, ch=p.get("ch"), sec=p.get("sec"),
                  pmf=p.get("pmf", 0), rssi=p.get("r"), bi=p.get("bi"), ie=p.get("ie"), last_t=t)
        ap["rssi_by_arm"][arm_id] = p.get("r")

    def _tracker(self, arm_id: int, p: elp.Packet, t: float) -> None:
        h = str(p.get("h", ""))
        tr = self.trackers.setdefault(h, {"hash": h, "first_t": t, "reports": 0})
        kind = int(p.get("trk", 0))
        tr.update(kind=kind, name=TRACKERS.get(kind, "unknown tracker"), mfr=p.get("mf"),
                  random_addr=bool(p.get("rnd", 0)), rssi=p.get("r"), arm=arm_id, last_t=t)
        tr["reports"] += 1

    @staticmethod
    def ap_notes(ap: Dict[str, Any], same_ssid: int) -> List[str]:
        notes = []
        if ap.get("sec") in WEAK_SEC:
            notes.append(WEAK_SEC[ap["sec"]])
        elif ap.get("sec") not in ("OWE",) and not ap.get("pmf"):
            notes.append("no PMF: clients can be knocked off with spoofed deauths")
        if ap.get("hidden"):
            notes.append("hidden SSID")
        if same_ssid:
            notes.append(f"SSID shared with {same_ssid} other BSSID(s): mesh/extender, or an impostor")
        if ap.get("ie_changes"):
            notes.append(f"beacon fingerprint changed {ap['ie_changes']}x")
        return notes

    def snapshot(self, n_windows: int = 40) -> Dict[str, Any]:
        now = self.clock()
        with self._lock:
            by_ssid = Counter(a["ssid"] for a in self.aps.values() if a.get("ssid"))
            aps = []
            for a in self.aps.values():
                same = by_ssid[a["ssid"]] - 1 if a.get("ssid") else 0
                aps.append({**a, "rssi_by_arm": dict(a["rssi_by_arm"]), "age": round(now - a["last_t"], 1),
                            "same_ssid": same, "notes": self.ap_notes(a, same)})
            aps.sort(key=lambda a: (a["age"] > 180, -(a.get("rssi") or -127)))
            recent = [w for w in self.windows if now - w["t"] <= 60]
            return {
                "t": round(now, 1),
                "arms": [{**a, "age": round(now - a["last_t"], 1)} for a in self.arms.values()],
                "aps": aps,
                "trackers": sorted(({**tr, "age": round(now - tr["last_t"], 1)} for tr in self.trackers.values()),
                                   key=lambda tr: tr["age"]),
                "windows": list(self.windows)[-n_windows:],
                "events": list(self.events)[::-1],
                "deauth": {"total": self.deauth_total, "spoofed": self.spoofed_total,
                           "last_60s": sum(w["dea"] + w["dis"] for w in recent),
                           "by_ch": self.deauth_by_ch[1:],
                           "series": [[round(w["t"], 1), w["ch"], w["dea"] + w["dis"]] for w in self.windows]},
                "counts": dict(self.counts),
            }
