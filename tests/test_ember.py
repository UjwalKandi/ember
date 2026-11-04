"""Stdlib unittest suite: python -m unittest discover -s tests -v"""
import json
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gateway import policy, protocol as elp, sim  # noqa: E402
from gateway.agent import GlimmerAgent  # noqa: E402
from gateway.episode import make_episode  # noqa: E402
from gateway.llm import _wire, parse_atem  # noqa: E402
from gateway.tools import TOOL_SCHEMAS  # noqa: E402
from training.gen_data import trajectory  # noqa: E402


class ProtocolTests(unittest.TestCase):
    def test_round_trip(self):
        for body in ["H|n3|s41|b3.9|up812|hz2|snd3", "E|n3|s42|FLM|c81|hz34|snd12",
                     "C|n3|q7|CONFIRM", "C|n3|q7|SET|rate5", "C|*|q8|ALERT|lvl2",
                     "R|n3|q7|PONG|b3.9", "R|n3|q7|CONF|FLM|c88|hz40|snd20", "R|n3|q7|ACK"]:
            self.assertEqual(elp.parse(body).encode(), body)

    def test_fields(self):
        p = elp.parse("E|n12|s42|SMK|c55|hz60|snd4")
        self.assertEqual((p.type, p.node, p.code, p.get("hz")), ("E", 12, "SMK", 60))

    def test_rejects(self):
        for bad in ["", "X|n1|s1", "H|node1|s1", "H|n1|s1|lower", "E|n1|" + "A" * 200]:
            with self.assertRaises(elp.ProtocolError):
                elp.parse(bad)

    def test_serial(self):
        rx = elp.parse_serial("RX|4|2|-71|E|n4|s9|CRK|c60|hz20|snd55")
        self.assertEqual((rx.src, rx.hops, rx.rssi, rx.packet.code), (4, 2, -71, "CRK"))
        self.assertIsNone(elp.parse_serial("LOG|boot ok"))
        self.assertEqual(elp.tx_line(elp.command("n2", 5, "PING")), "TX|C|n2|q5|PING\n")

    def test_sim_packets_fit_meshtastic_budget(self):
        net = sim.build("wildfire_node_lost", 3)
        seen = []
        net.on_rx = lambda rx, t: seen.append(rx.packet.encode())
        net.run(600)
        self.assertTrue(seen)
        self.assertLessEqual(max(map(len, seen)), elp.MAX_BODY)
        for body in seen:
            elp.parse(body)


class SwarmTests(unittest.TestCase):
    def test_every_scenario_runs_and_heuristic_submits(self):
        for sc in sim.SCENARIOS:
            ep = make_episode(sc, 1)
            out = policy.HeuristicAgent(ep.swarm, ep.tools).run()
            self.assertIsNotNone(out["assessment"], sc)
            self.assertIn("EMBER SITREP", out["report"])

    def test_context_is_compact(self):
        ep = make_episode("wildfire", 2)
        self.assertLess(len(ep.swarm.context()), 4000)

    def test_request_response(self):
        ep = make_episode("quiet", 0)
        r = ep.swarm.request(1, "PING", timeout=0)
        self.assertEqual(r.code, "PONG")

    def test_dead_node_times_out(self):
        ep = make_episode("node_offline", 0)
        dead = [n.id for n in ep.net.nodes.values() if not n.alive]
        if dead:
            res = json.loads(ep.tools.call("ping_node", {"node_id": dead[0]}))
            self.assertIn("TIMEOUT", res["reply"])

    def test_unknown_tool_and_node(self):
        ep = make_episode("quiet", 0)
        self.assertIn("error", json.loads(ep.tools.call("nope", {})))
        self.assertIn("error", json.loads(ep.tools.call("ping_node", {"node_id": 99})))


class TrainingDataTests(unittest.TestCase):
    def test_trajectory_shape(self):
        row = trajectory(make_episode("vehicle_dust", 4), random.Random(0))
        roles = [m["role"] for m in row["messages"]]
        self.assertEqual(roles[:2], ["system", "user"])
        self.assertEqual(roles[-1], "assistant")
        submits = [c for m in row["messages"] for c in m.get("tool_calls", [])
                   if c["function"]["name"] == "submit_assessment"]
        self.assertEqual(len(submits), 1)
        self.assertIsInstance(submits[0]["function"]["arguments"], dict)   # template needs dicts
        self.assertEqual(row["tools"], TOOL_SCHEMAS)


class ReplayClient:
    """Fake LLM that replays a teacher trajectory, one assistant turn per call."""
    def __init__(self, row):
        self.turns = [m for m in row["messages"] if m["role"] == "assistant"]
        self.seen = []

    def chat(self, messages, tools=None, **kw):
        self.seen.append(_wire(messages))
        m = self.turns.pop(0)
        return {"content": m.get("content") or "", "tool_calls": m.get("tool_calls", []), "reasoning": None}


class AgentLoopTests(unittest.TestCase):
    def test_glimmer_loop_with_replayed_model(self):
        row = trajectory(make_episode("wildfire", 5), random.Random(0))
        ep = make_episode("wildfire", 5)
        client = ReplayClient(row)
        out = GlimmerAgent(ep.swarm, ep.tools, client).run()
        self.assertEqual(out["assessment"]["verdict"], "ALERT")
        self.assertIn("SITREP", out["report"])
        # arguments go over the wire as JSON strings
        wired = [c for m in client.seen[-1] for c in m.get("tool_calls", [])]
        self.assertTrue(all(isinstance(c["function"]["arguments"], str) for c in wired))

    def test_atem_parser(self):
        raw = ('to=self thinking about <atem:invoke name="ping_node"><atem:parameter name="node_id">9'
               '</atem:parameter></atem:invoke> to=functions.request_confirm '
               '<atem:function_calls><atem:invoke name="request_confirm">'
               '<atem:parameter name="node_id">3</atem:parameter></atem:invoke></atem:function_calls>')
        _, calls = parse_atem(raw)
        self.assertEqual([(c["function"]["name"], c["function"]["arguments"]) for c in calls],
                         [("request_confirm", {"node_id": 3})])


if __name__ == "__main__":
    unittest.main()
