"""Frame and pcap builders for the Phase 9 tests: real bytes, built by hand.

Ethernet, IPv4/IPv6, TCP and UDP headers packed with `struct`, so the flow
meter and the live path are exercised on the same byte layouts a capture
delivers, with no capture library and no network involved.
"""

from __future__ import annotations

import ipaddress
import struct
from pathlib import Path

from app.flowmeter import ACK, FIN, PSH, SYN, TCP, UDP

CLIENT, SERVER = "10.0.0.2", "10.0.0.1"


def ethernet(ethertype: int, payload: bytes) -> bytes:
    return b"\x02" * 6 + b"\x04" * 6 + struct.pack("!H", ethertype) + payload


def vlan(frame: bytes) -> bytes:
    return frame[:12] + struct.pack("!HH", 0x8100, 7) + frame[12:]


def ipv4(proto: int, src: str, dst: str, body: bytes) -> bytes:
    header = struct.pack(
        "!BBHHHBBH4s4s", 0x45, 0, 20 + len(body), 1, 0, 64, proto, 0,
        ipaddress.IPv4Address(src).packed, ipaddress.IPv4Address(dst).packed,
    )  # fmt: skip
    return header + body


def tcp_frame(
    src: str,
    dst: str,
    sport: int,
    dport: int,
    flags: int,
    payload: bytes = b"",
    header: int = 20,
    window: int = 1024,
) -> bytes:
    segment = struct.pack("!HHIIBBHHH", sport, dport, 1, 0, (header // 4) << 4, flags, window, 0, 0)
    segment += b"\x01" * (header - 20) + payload
    return ethernet(0x0800, ipv4(TCP, src, dst, segment))


def ethernet_ipv4_tcp(flags: int = ACK, header: int = 20, forward: bool = True) -> bytes:
    if forward:
        return tcp_frame(CLIENT, SERVER, 40000, 80, flags, header=header)
    return tcp_frame(SERVER, CLIENT, 80, 40000, flags, header=header)


def ethernet_ipv4_udp(payload: bytes = b"") -> bytes:
    datagram = struct.pack("!HHHH", 5353, 53, 8 + len(payload), 0) + payload
    return ethernet(0x0800, ipv4(UDP, CLIENT, SERVER, datagram))


def ethernet_ipv6_udp() -> bytes:
    datagram = struct.pack("!HHHH", 5353, 53, 12, 0) + b"abcd"
    header = struct.pack("!IHBB", 6 << 28, len(datagram), UDP, 64)
    header += (
        ipaddress.IPv6Address("2001:db8::2").packed + ipaddress.IPv6Address("2001:db8::1").packed
    )
    return ethernet(0x86DD, header + datagram)


def http_exchange(client: str, client_port: int, start_us: int) -> list[tuple[int, bytes]]:
    """One short client-server exchange, closed by the client's FIN."""
    out = [(client, SERVER, client_port, 80), (SERVER, client, 80, client_port)]
    request, response = b"GET / HTTP/1.1\r\n\r\n", b"HTTP/1.1 200 OK\r\n\r\n" + b"x" * 200
    steps = [
        (0, 0, SYN, b"", 40),
        (900, 1, SYN | ACK, b"", 40),
        (1_400, 0, ACK, b"", 32),
        (2_000, 0, PSH | ACK, request, 32),
        (5_000, 1, PSH | ACK, response, 32),
        (6_000, 0, FIN | ACK, b"", 32),
    ]
    return [
        (start_us + offset, tcp_frame(*out[side], flags, payload, header))
        for offset, side, flags, payload, header in steps
    ]


def write_pcap(path: Path, frames: list[tuple[int, bytes]]) -> None:
    with path.open("wb") as handle:
        handle.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for ts_us, frame in frames:
            handle.write(
                struct.pack("<IIII", ts_us // 1_000_000, ts_us % 1_000_000, len(frame), len(frame))
            )
            handle.write(frame)
