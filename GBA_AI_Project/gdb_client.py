"""
mGBA GDB RSP Client
Connects to mGBA's built-in GDB server (Tools -> Start GDB Server).
Provides direct memory read/write without any Lua scripting.
"""

import socket
import time


class GDBClient:
    def __init__(self, host: str = "localhost", port: int = 2345):
        self.host = host
        self.port = port
        self.sock: socket.socket | None = None

    def connect(self) -> bool:
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(3.0)
            self.sock.connect((self.host, self.port))
            # mGBA may send an initial '+' ack — drain it
            try:
                self.sock.recv(16)
            except socket.timeout:
                pass
            print(f"[GDB] Connected to mGBA at {self.host}:{self.port}")
            return True
        except (ConnectionRefusedError, OSError) as e:
            print(f"[GDB] Connection failed: {e}")
            return False

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    # --------------------------------------------------------
    # Low-level GDB RSP protocol
    # --------------------------------------------------------
    @staticmethod
    def _checksum(payload: str) -> int:
        return sum(ord(c) for c in payload) % 256

    def _send_packet(self, payload: str):
        cs = self._checksum(payload)
        packet = f"${payload}#{cs:02x}"
        self.sock.sendall(packet.encode("latin-1"))

    def _recv_packet(self) -> str:
        """Read one GDB RSP packet, return its payload."""
        buf = b""
        self.sock.settimeout(2.0)
        try:
            # Skip any leading '+' acks
            while True:
                ch = self.sock.recv(1)
                if ch == b"$":
                    break
                # '+' or '-' are acks — ignore
            # Read until '#xx'
            while True:
                ch = self.sock.recv(1)
                if ch == b"#":
                    self.sock.recv(2)  # consume checksum bytes
                    break
                buf += ch
        except socket.timeout:
            pass
        # Send '+' ack
        try:
            self.sock.sendall(b"+")
        except OSError:
            pass
        return buf.decode("latin-1")

    def _cmd(self, payload: str) -> str:
        """Send a command and return the response payload."""
        if not self.sock:
            raise RuntimeError("Not connected")
        self._send_packet(payload)
        return self._recv_packet()

    # --------------------------------------------------------
    # Memory access
    # --------------------------------------------------------
    def read_memory(self, addr: int, length: int) -> bytes | None:
        """Read `length` bytes from GBA bus address `addr`."""
        response = self._cmd(f"m{addr:x},{length:x}")
        if not response or response.startswith("E"):
            return None
        try:
            return bytes.fromhex(response)
        except ValueError:
            return None

    def write_memory(self, addr: int, data: bytes) -> bool:
        """Write `data` to GBA bus address `addr`."""
        hex_data = data.hex()
        response = self._cmd(f"M{addr:x},{len(data):x}:{hex_data}")
        return response == "OK"

    def read_u8(self, addr: int) -> int:
        data = self.read_memory(addr, 1)
        return data[0] if data else 0

    def read_u16(self, addr: int) -> int:
        data = self.read_memory(addr, 2)
        return int.from_bytes(data, "little") if data else 0

    def read_u32(self, addr: int) -> int:
        data = self.read_memory(addr, 4)
        return int.from_bytes(data, "little") if data else 0

    def write_u8(self, addr: int, value: int):
        self.write_memory(addr, bytes([value & 0xFF]))

    def read_string(self, addr: int, max_len: int = 16) -> bytes:
        """Read raw bytes until 0xFF terminator (Pokemon encoding)."""
        data = self.read_memory(addr, max_len)
        if not data:
            return b""
        end = len(data)
        for i, b in enumerate(data):
            if b in (0x00, 0xFF):
                end = i
                break
        return data[:end]
