"""USB-serial bridge to the gateway XIAO (see protocol/SPEC.md section 3)."""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from . import protocol as elp


class SerialBridge:
    synchronous = False

    def __init__(self, port: str, on_rx: Callable[[elp.RxLine], None], baud: int = 115200,
                 on_log: Optional[Callable[[str], None]] = None):
        try:
            import serial  # pyserial
        except ImportError as e:
            raise SystemExit("pyserial is required for hardware mode: pip install pyserial") from e
        self.ser = serial.Serial(port, baud, timeout=0.5)
        self.on_rx, self.on_log = on_rx, on_log or (lambda s: None)
        self._wlock = threading.Lock()
        self._stop = False
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        buf = b""
        while not self._stop:
            try:
                buf += self.ser.read(256)
            except Exception as e:   # unplugged
                self.on_log(f"serial error: {e}")
                time.sleep(1)
                continue
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("ascii", "replace").strip()
                if not line:
                    continue
                if line.startswith("LOG|"):
                    self.on_log(line[4:])
                    continue
                try:
                    rx = elp.parse_serial(line)
                except (elp.ProtocolError, ValueError) as e:
                    self.on_log(f"bad line {line!r}: {e}")
                    continue
                if rx:
                    self.on_rx(rx)

    def send(self, pkt: elp.Packet) -> None:
        with self._wlock:
            self.ser.write(elp.tx_line(pkt).encode())

    def close(self) -> None:
        self._stop = True
        self.ser.close()
