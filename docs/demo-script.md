# Demo script (target 2.5 min)

**Setup before judging:** 3 flashed nodes plus the gateway XIAO on USB. The EMBER endpoint on Modal is warm (hit it once ~5 min before). Run:
`python3 -m gateway.server --serial <port> --nodes config/nodes.json --sim quiet --agent llm --auto --show-truth`

1. **Hook (15 s).** "Wildfire sensor networks already exist, and they detect. EMBER investigates. A fine-tuned Muse Glimmer questions $15 camera nodes over a long-range mesh, pays for every question in radio airtime, and re-programs the sensors when they're wrong."
2. **The mesh (30 s).** Show the map: n1-n3 are the real boards on the table, n4-n9 simulated. Point at the live packet feed: "Every message is at most 200 bytes, Meshtastic-class. The nodes do the vision and audio on-device and send codes like `E|n3|FLM|c81`." Walk one node away so it relays through another: the hop count goes h0 → h1.
3. **Real detection (30 s).** Hold a lighter (or a flame video on a phone) in front of n2. The node turns red and the auto-assessment starts. In the trace panel, Muse Glimmer calls `request_confirm(2)` and the downwind neighbor's confirm. **The CONFIRM command physically goes back over the mesh to the board**, and the board re-samples and answers.
4. **The call + report (30 s).** The verdict appears (WATCH for a single small flame, like a campfire), along with the SITREP. Then click the map near n5/n6 to ignite a spreading simulated fire. Within ~40 s: ALERT, location estimate X on the map, `broadcast_alert` → **the real boards blink their LEDs.**
5. **The model re-programs the edge (40 s).** Load `fog`. The packet feed fills with SMK from every node; the rule-based system would raise a wildfire alarm (its fog score is 0%). EMBER spends ~1 s of airtime on one re-sample, calls it weather, then sends **one broadcast: `C|*|q9|SET|rebase1`**. The map shows ⚙ rebased on every node and **the event storm stops**. "It didn't just ignore the false alarm. It fixed the sensors that caused it, over a 1 kbps radio."
   Then point at the airtime gauge: "Every question costs LoRa airtime under a 1% duty cycle. The fine-tuned model learned when a question is worth asking." Show the README table: base vs EMBER vs rules (accuracy under tight airtime, false-alarm events after acting).
6. **Close (15 s).** "Swap ESP-NOW for LoRa and it's a real Meshtastic network; nothing above the radio changes. Everything you saw was built during the hackathon."

**Fallbacks:** if hardware misbehaves, run without `--serial` (all nodes simulated). If Modal is slow, use `--agent heuristic` and show the recorded LLM trace or video.
