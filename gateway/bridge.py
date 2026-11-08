"""USB-serial bridge to the gateway XIAO (see protocol/SPEC.md section 3)."""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from . import protocol as elp


def handle_line(line: str, on_rx: Callable[[elp.RxLine], None], on_log: Callable[[str], None]) -> None:
    """One board line: LOG| -> on_log, RX| -> on_rx, anything unparseable -> on_log."""
    if line.startswith("LOG|"):
        on_log(line[4:])
        return
    try:
        rx = elp.parse_serial(line)
    except (elp.ProtocolError, ValueError) as e:
        on_log(f"bad line {line!r}: {e}")
        return
    if rx:
        on_rx(rx)


class SerialBridge:
    synchronous = False

    def __init__(self, port: str, on_rx: Callable[[elp.RxLine], None], baud: int = 115200,
                 on_log: Optional[Callable[[str], None]] = None,
                 on_raw: Optional[Callable[[str], None]] = None):
        try:
            import serial  # pyserial
        except ImportError as e:
            raise SystemExit("pyserial is required for hardware mode: pip install pyserial") from e
        try:
            self.ser = serial.Serial(port, baud, timeout=0.5)
        except serial.SerialException as e:
            if "busy" in str(e).lower():
                raise SystemExit(f"\n{port} is busy: another program has it open. Close the EMBER console tab "
                                 f"(or click Disconnect), quit any `pio device monitor`, then run this again.\n")
            raise SystemExit(f"\ncould not open {port}: {e}\nCheck the cable and `ls /dev/cu.usbmodem*`.\n")
        self.on_rx, self.on_log = on_rx, on_log or (lambda s: None)
        self.on_raw = on_raw or (lambda s: None)   # every line as received (capture recording)
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
                if line:
                    self.on_raw(line)
                    handle_line(line, self.on_rx, self.on_log)

    def send(self, pkt: elp.Packet) -> None:
        with self._wlock:
            self.ser.write(elp.tx_line(pkt).encode())

    def close(self) -> None:
        self._stop = True
        self.ser.close()
