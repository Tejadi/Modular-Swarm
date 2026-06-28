"""Async serial transport for the gateway nRF link.

Uses raw file-descriptor I/O plus the asyncio reader, so it works identically on
a pseudo-terminal (the simulator) and on a real USB-CDC device (/dev/ttyACM0)
with no third-party serial dependency. Incoming bytes are deframed through
swarm_proto.SerialReader (COBS + CRC16, resynchronizing on corruption) and each
recovered payload is handed to the on_payload callback.
"""

from __future__ import annotations

import logging
import os

import swarm_proto as sp

log = logging.getLogger("olympus_link.serial")

try:
    import termios
    _HAVE_TERMIOS = True
except ImportError:  # pragma: no cover - non-POSIX
    _HAVE_TERMIOS = False

_BAUD = {}
if _HAVE_TERMIOS:
    for _b in (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600, 1000000):
        _name = f"B{_b}"
        if hasattr(termios, _name):
            _BAUD[_b] = getattr(termios, _name)


class AsyncSerial:
    def __init__(self, path: str, on_payload, baud: int = 115200) -> None:
        self.path = path
        self.on_payload = on_payload
        self._reader = sp.SerialReader()
        self._loop = None
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        if _HAVE_TERMIOS and os.isatty(self.fd):
            self._set_raw(baud)

    def _set_raw(self, baud: int) -> None:
        attrs = termios.tcgetattr(self.fd)
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs
        # cfmakeraw: drop all input/output processing and line discipline.
        iflag &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK
                   | termios.ISTRIP | termios.INLCR | termios.IGNCR
                   | termios.ICRNL | termios.IXON)
        oflag &= ~termios.OPOST
        lflag &= ~(termios.ECHO | termios.ECHONL | termios.ICANON
                   | termios.ISIG | termios.IEXTEN)
        cflag &= ~(termios.CSIZE | termios.PARENB)
        cflag |= termios.CS8 | termios.CLOCAL | termios.CREAD
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = 0
        if baud in _BAUD:
            ispeed = ospeed = _BAUD[baud]
        termios.tcsetattr(self.fd, termios.TCSANOW,
                          [iflag, oflag, cflag, lflag, ispeed, ospeed, cc])
        # Assert DTR (+RTS). The nRF's USB-CDC gates TX on DTR, so without this
        # the device transmits nothing (opening with plain `cat` reads empty).
        try:
            import fcntl
            import struct
            TIOCMBIS = 0x5416
            TIOCM_DTR, TIOCM_RTS = 0x002, 0x004
            fcntl.ioctl(self.fd, TIOCMBIS,
                        struct.pack("I", TIOCM_DTR | TIOCM_RTS))
        except Exception:  # pragma: no cover - best effort
            pass

    def start(self, loop) -> None:
        self._loop = loop
        loop.add_reader(self.fd, self._on_readable)

    def _on_readable(self) -> None:
        try:
            data = os.read(self.fd, 4096)
        except (BlockingIOError, InterruptedError):
            return
        except OSError as e:
            log.error("serial read error: %s", e)
            return
        if not data:
            return
        for payload in self._reader.feed(data):
            try:
                self.on_payload(payload)
            except Exception:  # never let one bad frame kill the reader
                log.exception("on_payload handler raised")

    def write_payload(self, payload: bytes) -> None:
        frame = sp.frame_serial(payload)
        try:
            os.write(self.fd, frame)
        except OSError as e:
            log.error("serial write error: %s", e)

    def close(self) -> None:
        if self._loop is not None:
            try:
                self._loop.remove_reader(self.fd)
            except Exception:
                pass
        try:
            os.close(self.fd)
        except OSError:
            pass
