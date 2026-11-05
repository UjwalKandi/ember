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
        # firmware console output is never mistaken for packets
        for line in ("LOG|selftest PASS", "LOG|cfg role=node id=1 fw=v2-dev", "LOG|unprovisioned: send CFG|id=<1-254>"):
            self.assertIsNone(elp.parse_serial(line))
        own = elp.parse_serial("RX|1|0|0|H|n1|s1|up5|hz4|snd5")     # a node bridging its own packet
        self.assertEqual((own.src, own.hops, own.packet.node), (1, 0, 1))
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

    def test_context_lists_only_node_events(self):
        ep = make_episode("wildfire", 3)
        policy.HeuristicAgent(ep.swarm, ep.tools).run()       # leaves CONF/ACK replies in the log
        self.assertFalse([l for l in ep.swarm.context().splitlines() if " R|" in l])

    def test_missing_battery_is_not_unreliable(self):
        ep = make_episode("quiet", 0)
        for nid in ep.swarm.nodes:
            ep.swarm.nodes[nid].batt = None
        ep.swarm.on_rx(elp.RxLine(1, 0, -50, elp.parse("H|n1|s99|b0|up10|hz4|snd4")))
        self.assertIsNone(ep.swarm.nodes[1].batt)
        a = policy.heuristic_assessment(ep.swarm, {})
        self.assertEqual(a["unreliable_nodes"], [])

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

    def test_one_tool_call_per_message(self):
        row = trajectory(make_episode("wildfire_plus_glare", 2), random.Random(0))
        msgs = row["messages"]
        for i, m in enumerate(msgs):
            if m.get("tool_calls"):
                self.assertEqual(len(m["tool_calls"]), 1)
                self.assertEqual(msgs[i + 1]["role"], "tool")      # result follows its call
                self.assertEqual(msgs[i + 1]["tool_call_id"], m["tool_calls"][0]["id"])

    def test_holdout_scenarios_not_in_training(self):
        from training.gen_data import HOLDOUT
        self.assertTrue(set(HOLDOUT) <= set(sim.SCENARIOS))


class AirtimeTests(unittest.TestCase):
    def test_semtech_reference(self):
        from gateway.airtime import LoRaPreset, time_on_air_ms
        # well-known reference: 20 B at SF7 / 125 kHz / CR4/5 / 8-symbol preamble = 56.6 ms
        self.assertAlmostEqual(time_on_air_ms(20, LoRaPreset(sf=7, bw_hz=125_000, preamble=8)), 56.6, delta=2.8)

    def test_hops_multiply(self):
        from gateway.airtime import packet_ms
        self.assertAlmostEqual(packet_ms("C|n3|q7|CONFIRM", 2), 3 * packet_ms("C|n3|q7|CONFIRM", 0))

    def test_exhausted_budget_refuses_radio_but_not_history(self):
        ep = make_episode("wildfire", 1, budget="exhausted")
        self.assertIn("airtime budget exhausted", ep.tools.call("request_confirm", {"node_id": 1}))
        self.assertIn("packets", ep.tools.call("get_node_history", {"node_id": 1}))

    def test_tight_budget_limits_radio_queries(self):
        for sc in ("wildfire", "fog", "sun_glare"):
            ep = make_episode(sc, 4, budget="tight")
            calls, _ = policy.investigate_plan(ep.swarm)
            self.assertLessEqual(sum(n in policy.MESH_TOOLS for n, _ in calls), 1, sc)


class EdgeTuningTests(unittest.TestCase):
    def _after(self, ep, seconds=300):
        from training.eval import spurious_after
        return spurious_after(ep, seconds)

    def test_rebase_stops_fog_false_alarms(self):
        ep = make_episode("fog", 100001, budget="generous")
        out = json.loads(ep.tools.call("tune_detectors", {"node_id": "all", "rebase": True}))
        self.assertEqual(len(out["acked"]), len(ep.net.nodes))
        control = make_episode("fog", 100001, budget="generous")
        self.assertLess(self._after(ep), self._after(control))

    def test_flicker_check_suppresses_glare(self):
        ep = make_episode("sun_glare", 100002, budget="generous")
        glare = next(n.id for n in ep.net.nodes.values() if n.glare)
        ep.tools.call("tune_detectors", {"node_id": glare, "flicker_check": True})
        self.assertTrue(ep.swarm.nodes[glare].tuning.get("flk"))
        control = make_episode("sun_glare", 100002, budget="generous")
        self.assertLessEqual(self._after(ep), self._after(control))

    def test_teacher_reprograms_edge_for_false_alarm_causes(self):
        want = {"fog": "rebase", "sun_glare": "flicker_check", "stuck_sensor": "smoke_threshold"}
        for sc, key in want.items():
            row = trajectory(make_episode(sc, 7, budget="generous"), random.Random(0))
            tunes = [c["function"]["arguments"] for m in row["messages"] for c in m.get("tool_calls", [])
                     if c["function"]["name"] == "tune_detectors"]
            if row["meta"]["cause"] in ("weather", "sun_glare", "sensor_fault"):
                self.assertTrue(any(key in t for t in tunes), sc)


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


class FreePathTests(unittest.TestCase):
    def test_llm_extra_fields_and_length_retry(self):
        import io
        from unittest import mock
        from gateway.llm import ChatClient
        sent = []
        replies = [
            {"choices": [{"finish_reason": "length", "message": {"content": "", "reasoning_content": "..."}}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]},
        ]

        def fake_urlopen(req, timeout):
            sent.append(json.loads(req.data))
            return io.BytesIO(json.dumps(replies.pop(0)).encode())

        client = ChatClient("http://x/v1", "muse-glimmer", "k",
                            extra={"chat_template_kwargs": {"reasoning_strength": "low"}})
        with mock.patch("urllib.request.urlopen", fake_urlopen):
            out = client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(out["content"], "ok")
        self.assertEqual(sent[0]["chat_template_kwargs"], {"reasoning_strength": "low"})
        self.assertEqual(sent[1]["max_tokens"], 2 * sent[0]["max_tokens"])   # retried with more budget

    def test_context_event_cap(self):
        ep = make_episode("wildfire", 6)
        row = trajectory(ep, random.Random(0), max_events=5)
        events = [l for l in row["messages"][1]["content"].splitlines() if l.startswith("T-")]
        self.assertLessEqual(len(events), 5)

    def test_kaggle_label_mask_covers_only_assistant_turns(self):
        from training.kaggle_qlora import build_example

        class CharTok:
            """Toy tokenizer: one token per character, template = role-tagged lines."""
            def apply_chat_template(self, msgs, tools=None, tokenize=False):
                return "".join(f"<{m['role']}>{m.get('content') or json.dumps(m.get('tool_calls'))}\n" for m in msgs)

            def __call__(self, text, add_special_tokens=False, return_offsets_mapping=False):
                return {"input_ids": [ord(c) for c in text], "offset_mapping": [(i, i + 1) for i in range(len(text))]}

        row = trajectory(make_episode("fog", 2), random.Random(0))
        ex = build_example(CharTok(), row, max_len=100_000)
        text = CharTok().apply_chat_template(row["messages"])
        trained = "".join(chr(t) for t, l in zip(ex["input_ids"], ex["labels"]) if l != -100)
        self.assertNotIn("<user>", trained)
        self.assertNotIn("<system>", trained)
        self.assertIn("submit_assessment", trained)
        self.assertIn("EMBER SITREP", trained)
        self.assertLess(len(trained), len(text))
        self.assertIsNone(build_example(CharTok(), row, max_len=10))     # too long -> dropped


if __name__ == "__main__":
    unittest.main()
