"""v2 RF arm tests (R1): ELP v2 tokens, the airspace mirror, capture replay, and the firmware's
pure C++ parsing (compiled and run on the host when a C++ compiler is available).

    python -m unittest discover -s tests -v
"""
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway import protocol as elp  # noqa: E402
from gateway.airspace import W_FIELDS, Airspace, is_rf, read_capture  # noqa: E402
from gateway.bridge import handle_line  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "airspace_sample.log"

V2_BODIES = [
    "H|n1|s40|up3600|ap23|ble14|trk1|drp0|occ:1=34,6=71",
    "A|n1|s41|b:a1b2c3d4e5f6|ss:Home%20Net|ch6|sec:W2|pmf1|r-61|bi100|ie:9f3a",
    "A|n1|s42|b:02ab12cd34ef|ss:|ch3|sec:W2|pmf1|r-69|bi100|ie:b0d1|hid1",
    "E|n1|s43|DEA|ch6|b:a1b2c3d4e5f6|cnt20|rate66|bc20|spf20|r-40",
    "E|n1|s44|BSP|ch1|nn12|nb14",
    "B|n1|s45|h:7c19aa02|trk1|mf:004C|rnd1|r-70",
    "W|n1|s46|ch6|dt300|bcn12|prq3|prs4|ath0|asc0|dea0|dis0|act0|bc0|spf0|nb5|nn0|nss4|ds0|pd2|sqa0|tsj0|iec0"
    "|rx-41|ra-67",
]


def load_fixture() -> Airspace:
    sp = Airspace(clock=lambda: 45.0)
    for t, line in read_capture(FIXTURE.read_text().splitlines()):
        handle_line(line, lambda rx, t=t: sp.on_rx(rx, t), lambda s: None)
    return sp


class ProtocolV2Tests(unittest.TestCase):
    def test_round_trip(self):
        for body in V2_BODIES:
            self.assertEqual(elp.parse(body).encode(), body)

    def test_string_tokens(self):
        p = elp.parse(V2_BODIES[1])
        self.assertEqual((p.get("b"), p.get("ss"), p.get("sec"), p.get("r")), ("a1b2c3d4e5f6", "Home Net", "W2", -61))
        self.assertEqual(elp.parse(V2_BODIES[2]).get("ss"), "")

    def test_ssid_escaping(self):
        # pipes, percent signs, spaces, UTF-8 and invalid bytes all survive the trip
        for ssid in ["a|b", "100%", "café wifi", "\udcff\x01x", ""]:
            body = elp.Packet("A", "n1", fields={"s": 1, "b": "00", "ss": ssid}).encode()
            self.assertNotIn(" ", body)
            self.assertEqual(body.count("|"), 4)
            self.assertEqual(elp.parse(body).get("ss"), ssid)
        self.assertEqual(elp.escape("a b|%"), "a%20b%7C%25")

    def test_v1_packets_unchanged(self):
        for body in ["H|n3|s41|b3.9|up812|hz2|snd3", "E|n3|s42|FLM|c81|hz34|snd12", "C|*|q8|ALERT|lvl2"]:
            self.assertEqual(elp.parse(body).encode(), body)

    def test_fits_budget(self):
        # worst-case A packet: a 32-byte SSID of bytes that all need escaping (96 chars)
        p = elp.Packet("A", "n254", fields={"s": 999999, "b": "a1b2c3d4e5f6", "ss": "|" * 32, "ch": 13, "sec": "W23",
                                             "pmf": 2, "r": -100, "bi": 65535, "ie": "ffff", "hid": 1})
        self.assertLessEqual(len(p.encode()), elp.MAX_BODY)

    def test_routing(self):
        rf = [elp.parse(b) for b in V2_BODIES]
        self.assertTrue(all(is_rf(p) for p in rf))
        for body in ["H|n3|s41|b3.9|up812|hz2|snd3", "E|n3|s42|FLM|c81|hz34|snd12", "R|n3|q7|ACK"]:
            self.assertFalse(is_rf(elp.parse(body)), body)


class AirspaceTests(unittest.TestCase):
    def test_fixture_snapshot(self):
        s = load_fixture().snapshot()
        self.assertEqual(len(s["arms"]), 1)
        self.assertEqual(len(s["aps"]), 5)
        aps = {a["bssid"]: a for a in s["aps"]}
        home = aps["a1b2c3d4e5f6"]
        self.assertEqual((home["ssid"], home["ch"], home["sec"], home["pmf"]), ("HomeNet", 6, "W23", 2))
        self.assertEqual(home["same_ssid"], 1)                 # its mesh sibling a1b2c3d4e5f7
        self.assertTrue(aps["02ab12cd34ef"]["hidden"])
        self.assertTrue(any("open network" in n for n in aps["f4f26d00beef"]["notes"]))
        self.assertTrue(any("no PMF" in n for n in aps["0c8063aa1102"]["notes"]))
        self.assertEqual(aps["0c8063aa1102"]["ssid"], "Neighbour Wifi")
        self.assertGreaterEqual(s["deauth"]["total"], 76 + 88)
        self.assertEqual(s["deauth"]["spoofed"], 76 + 88)
        self.assertEqual(s["deauth"]["by_ch"][5], 76 + 88)     # channel 6, minus benign stragglers
        self.assertEqual([e["code"] for e in s["events"]], ["DEA"])
        self.assertEqual(s["trackers"][0]["name"], "Apple Find My")
        self.assertEqual(s["trackers"][0]["reports"], 2)
        self.assertEqual(s["arms"][0]["occ"], {1: 21, 3: 9, 6: 38, 11: 6})
        self.assertEqual(set(s["windows"][-1]) - {"t", "arm"}, set(W_FIELDS))

    def test_read_capture_formats(self):
        lines = ["# comment", "", "1.5\tRX|1|0|0|E|n1|s1|BSP|ch1|nn9|nb9", "LOG|plain serial log line"]
        self.assertEqual(list(read_capture(lines)),
                         [(1.5, "RX|1|0|0|E|n1|s1|BSP|ch1|nn9|nb9"), (None, "LOG|plain serial log line")])

    def test_firmware_w_format_matches_mirror(self):
        # the arm's W snprintf and W_FIELDS (the classifier's feature order) must agree
        src = (ROOT / "firmware" / "src" / "arm.cpp").read_text()
        fmt = "".join(re.findall(r'"([^"]*)"', src[src.index('"W|n%u'):src.index("selfId, (unsigned long)++seq, w.ch")]))
        keys = re.findall(r"\|([a-z]+)%", fmt)
        self.assertEqual(tuple(keys[2:]), W_FIELDS)            # after n (target) and s (seq)


class ServerReplayTests(unittest.TestCase):
    def test_replay_feeds_airspace_and_records_nothing_to_swarm(self):
        from gateway.server import CommandCenter, parse_args
        cc = CommandCenter(parse_args(["--replay", str(FIXTURE), "--replay-speed", "1000"]))
        for _ in range(100):
            if cc.airspace.counts.get("W", 0) >= 150:
                break
            time.sleep(0.05)
        s = cc.airspace.snapshot()
        self.assertEqual(len(s["aps"]), 5)
        self.assertEqual(s["counts"]["E"], 1)
        self.assertFalse(any(e.pkt.type in elp.RF_TYPES for e in cc.swarm.log))

    def test_record_writes_replayable_capture(self):
        from gateway.server import CommandCenter, parse_args
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cap" / "x.log"
            cc = CommandCenter(parse_args(["--record", str(path), "--sim", "quiet"]))
            cc._record("RX|1|0|0|" + V2_BODIES[5])
            cc.recording.close()
            got = list(read_capture(path.read_text().splitlines()))
            self.assertEqual(got[0][1], "RX|1|0|0|" + V2_BODIES[5])
            self.assertIsInstance(got[0][0], float)


@unittest.skipUnless(shutil.which("c++"), "no C++ compiler")
class FirmwareHostTests(unittest.TestCase):
    def test_frames_and_features(self):
        fw = ROOT / "firmware"
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "t"
            build = subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(fw / "src"),
                                    str(fw / "test_host" / "test_frames.cpp"), str(fw / "src" / "frames.cpp"),
                                    str(fw / "src" / "features.cpp"), "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout)


if __name__ == "__main__":
    unittest.main()
