"""LoRa airtime accounting: every byte the command center sends costs radio time.

Semtech time-on-air formula (SX127x/SX126x datasheets, AN1200.13) with Meshtastic's
LongFast preset defaults. Flooding means a packet that travels h hops is transmitted
h+1 times on the shared channel, so remote nodes are proportionally more expensive.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

MESHTASTIC_OVERHEAD = 28      # bytes: 16 B Meshtastic header + protobuf/encryption framing (approx.)


@dataclass(frozen=True)
class LoRaPreset:
    sf: int = 11              # LongFast
    bw_hz: int = 250_000
    cr: int = 1               # coding rate 4/(4+cr) -> 4/5
    preamble: int = 16
    explicit_header: bool = True
    crc: bool = True


LONG_FAST = LoRaPreset()


def time_on_air_ms(payload_bytes: int, p: LoRaPreset = LONG_FAST) -> float:
    t_sym = (2 ** p.sf) / p.bw_hz * 1000.0
    de = 1 if t_sym > 16.0 else 0                      # low-data-rate optimisation
    ih = 0 if p.explicit_header else 1
    num = 8 * payload_bytes - 4 * p.sf + 28 + 16 * int(p.crc) - 20 * ih
    n_payload = 8 + max(math.ceil(num / (4 * (p.sf - 2 * de))) * (p.cr + 4), 0)
    return (p.preamble + 4.25) * t_sym + n_payload * t_sym


def packet_ms(body: str, hops: int, p: LoRaPreset = LONG_FAST) -> float:
    """Channel airtime for one ELP body flooded across `hops` relays."""
    return time_on_air_ms(len(body) + MESHTASTIC_OVERHEAD, p) * (hops + 1)


def round_trip_ms(cmd_body: str, reply_body: str, hops: int, p: LoRaPreset = LONG_FAST) -> float:
    return packet_ms(cmd_body, hops, p) + packet_ms(reply_body, hops, p)
