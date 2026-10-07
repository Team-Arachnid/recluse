"""Phase 9 -- packets to flows, the way CICIDS2017's flows were made.

The models were trained on rows that CICFlowMeter-V3 produced from the 2017
captures, so a live flow is only scoreable if it is *the same quantity*: the
same columns, computed the same way, quirks included. A meter that computed
"correct" features would hand both models a distribution they never saw, and
the train/serve-skew defence the feature contract exists for would be lost one
layer earlier than `training/features.py` can see.

So this reproduces CICFlowMeter's behaviour, and each rule below was checked
against the training rows themselves (`backend/tests/test_flowmeter.py` pins
them, and the module's docstrings cite the evidence):

1. **Bidirectional flows on the 5-tuple.** The first packet's direction is
   "forward" and `destination_port` is its destination.
2. **A flow ends at the first FIN**, in either direction, or when a packet
   arrives more than 120 s after the flow began. Ending at the first FIN is a
   known CICFlowMeter-V3 behaviour: the rest of the TCP teardown becomes a new,
   short flow (the "TCP appendix" Engelen et al., 2021, documented in the
   dataset).
3. **Lengths are payload bytes**, not frame or IP lengths, and the all-packet
   length statistics count the flow's *first packet twice* -- verified:
   `average_packet_size == packet_length_mean * (n + 1) / n` on every row.
4. **Standard deviations are sample (n - 1)**, and 0 below two values.
5. **The eight flag columns are the first packet's flags, permuted.**
   CICFlowMeter wrote the values by iterating a Java `HashMap` while the CSV
   header lists them in a fixed order, so each column holds a different flag
   (`FLAG_COLUMNS`). Every flag combination in the training data decodes to a
   real first packet under this mapping -- the ECN-negotiating SYN (SYN, ECE,
   CWR) among them -- and `fwd_psh_flags`/`fwd_urg_flags` likewise see only the
   first packet.
6. **`act_data_pkt_fwd` skips the first packet**, and
   **`init_win_bytes_backward` is the last backward packet's window**, not the
   first. `init_win_bytes_*` is -1 where there is no TCP window.
7. **A UDP packet's header length is the last TCP header length decoded.**
   No UDP flow in CICIDS2017 carries UDP's real 8-byte header; they carry
   20, 32, 40 -- TCP values from a reused decoder object. Reproduced, not fixed.
8. **Subflow columns equal the flow totals** (true of every training row).
9. **Active and idle periods come only from gaps over 5 s within the flow**;
   nothing is added when a flow ends (the training rows show active and idle
   time on the same 16% of flows, which only that rule produces).
10. **Zero-duration flows are not scored.** Their byte and packet rates are
    infinite, Phase 1 dropped every such row, and neither model has seen one.
    They are counted, so the blind spot -- it includes single-packet probes --
    is a number rather than a silence.

One deliberate departure: a flow that has been silent for `idle_flush_s` is
emitted. CICFlowMeter worked on finished pcaps and could wait for the end of
the file; a live meter that did would never score a long-lived quiet
connection.

Only TCP and UDP over IPv4 and IPv6 are metered, as CICFlowMeter did.
"""

from __future__ import annotations

import ipaddress
import math
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

# TCP flag bits.
FIN, SYN, RST, PSH, ACK, URG, ECE, CWR = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80

# CSV column -> the first-packet flag it actually holds (rule 5).
FLAG_COLUMNS: dict[str, int] = {
    "fin_flag_count": RST,
    "syn_flag_count": PSH,
    "rst_flag_count": ECE,
    "psh_flag_count": SYN,
    "ack_flag_count": ACK,
    "urg_flag_count": FIN,
    "cwe_flag_count": URG,
    "ece_flag_count": CWR,
}

FLOW_TIMEOUT_US = 120_000_000
ACTIVITY_TIMEOUT_US = 5_000_000
DEFAULT_IDLE_FLUSH_S = 60.0

TCP, UDP = 6, 17

# The 70 columns of a processed CICIDS2017 row, in the order Phase 1 wrote them.
FEATURE_COLUMNS: tuple[str, ...] = (
    "destination_port", "flow_duration", "total_fwd_packets", "total_backward_packets",
    "total_length_of_fwd_packets", "total_length_of_bwd_packets",
    "fwd_packet_length_max", "fwd_packet_length_min", "fwd_packet_length_mean",
    "fwd_packet_length_std", "bwd_packet_length_max", "bwd_packet_length_min",
    "bwd_packet_length_mean", "bwd_packet_length_std", "flow_bytes_s", "flow_packets_s",
    "flow_iat_mean", "flow_iat_std", "flow_iat_max", "flow_iat_min",
    "fwd_iat_total", "fwd_iat_mean", "fwd_iat_std", "fwd_iat_max", "fwd_iat_min",
    "bwd_iat_total", "bwd_iat_mean", "bwd_iat_std", "bwd_iat_max", "bwd_iat_min",
    "fwd_psh_flags", "fwd_urg_flags", "fwd_header_length", "bwd_header_length",
    "fwd_packets_s", "bwd_packets_s", "min_packet_length", "max_packet_length",
    "packet_length_mean", "packet_length_std", "packet_length_variance",
    "fin_flag_count", "syn_flag_count", "rst_flag_count", "psh_flag_count",
    "ack_flag_count", "urg_flag_count", "cwe_flag_count", "ece_flag_count",
    "down_up_ratio", "average_packet_size", "avg_fwd_segment_size", "avg_bwd_segment_size",
    "fwd_header_length_1", "subflow_fwd_packets", "subflow_fwd_bytes",
    "subflow_bwd_packets", "subflow_bwd_bytes", "init_win_bytes_forward",
    "init_win_bytes_backward", "act_data_pkt_fwd", "min_seg_size_forward",
    "active_mean", "active_std", "active_max", "active_min",
    "idle_mean", "idle_std", "idle_max", "idle_min",
)  # fmt: skip


# ---------------------------------------------------------------------------
# Packets
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Packet:
    """One decoded TCP or UDP packet: what the meter needs and nothing else."""

    ts_us: int
    src: str
    dst: str
    sport: int
    dport: int
    proto: int
    payload: int
    header: int
    flags: int = 0
    window: int = -1  # -1 where there is no TCP window (rule 6)


# Link types a capture can arrive in.
LINKTYPE_ETHERNET = 1
LINKTYPE_RAW = 101
LINKTYPE_LINUX_SLL = 113
LINKTYPE_IPV4 = 228
LINKTYPE_IPV6 = 229

_IPV6_EXTENSIONS = {0, 43, 44, 60}


class Decoder:
    """Frames to `Packet`s, carrying the one piece of state CICFlowMeter did.

    The state is rule 7: the header length of the last TCP segment decoded,
    which CICFlowMeter recorded for every UDP packet after it.
    """

    def __init__(self) -> None:
        self.last_tcp_header = 20

    def decode(self, frame: bytes, ts_us: int, linktype: int = LINKTYPE_ETHERNET) -> Packet | None:
        try:
            if linktype == LINKTYPE_ETHERNET:
                offset, ethertype = 14, struct.unpack_from("!H", frame, 12)[0]
                while ethertype in (0x8100, 0x88A8):  # VLAN tags
                    ethertype = struct.unpack_from("!H", frame, offset + 2)[0]
                    offset += 4
            elif linktype == LINKTYPE_LINUX_SLL:
                offset, ethertype = 16, struct.unpack_from("!H", frame, 14)[0]
            elif linktype in (LINKTYPE_RAW, LINKTYPE_IPV4, LINKTYPE_IPV6):
                offset = 0
                ethertype = 0x86DD if frame[0] >> 4 == 6 else 0x0800
            else:
                return None
            if ethertype == 0x0800:
                return self._ipv4(frame, offset, ts_us)
            if ethertype == 0x86DD:
                return self._ipv6(frame, offset, ts_us)
        except (struct.error, IndexError, ValueError):
            return None  # a truncated or malformed frame is skipped, not fatal
        return None

    def _ipv4(self, frame: bytes, offset: int, ts_us: int) -> Packet | None:
        version_ihl, total_length = frame[offset], struct.unpack_from("!H", frame, offset + 2)[0]
        if version_ihl >> 4 != 4:
            return None
        ihl = (version_ihl & 0x0F) * 4
        fragment = struct.unpack_from("!H", frame, offset + 6)[0] & 0x1FFF
        if fragment:  # a later fragment carries no transport header
            return None
        proto = frame[offset + 9]
        src = str(ipaddress.IPv4Address(frame[offset + 12 : offset + 16]))
        dst = str(ipaddress.IPv4Address(frame[offset + 16 : offset + 20]))
        return self._transport(frame, offset + ihl, total_length - ihl, proto, src, dst, ts_us)

    def _ipv6(self, frame: bytes, offset: int, ts_us: int) -> Packet | None:
        payload_length = struct.unpack_from("!H", frame, offset + 4)[0]
        proto = frame[offset + 6]
        src = str(ipaddress.IPv6Address(frame[offset + 8 : offset + 24]))
        dst = str(ipaddress.IPv6Address(frame[offset + 24 : offset + 40]))
        cursor, remaining = offset + 40, payload_length
        while proto in _IPV6_EXTENSIONS:
            next_header, length = frame[cursor], (frame[cursor + 1] + 1) * 8
            if proto == 44:
                length = 8
            proto, cursor, remaining = next_header, cursor + length, remaining - length
        return self._transport(frame, cursor, remaining, proto, src, dst, ts_us)

    def _transport(
        self, frame: bytes, offset: int, length: int, proto: int, src: str, dst: str, ts_us: int
    ) -> Packet | None:
        if proto == TCP:
            sport, dport = struct.unpack_from("!HH", frame, offset)
            header = (frame[offset + 12] >> 4) * 4
            flags = frame[offset + 13]
            window = struct.unpack_from("!H", frame, offset + 14)[0]
            self.last_tcp_header = header
            return Packet(
                ts_us, src, dst, sport, dport, TCP, max(0, length - header), header, flags, window
            )
        if proto == UDP:
            sport, dport, udp_length = struct.unpack_from("!HHH", frame, offset)
            return Packet(
                ts_us, src, dst, sport, dport, UDP, max(0, udp_length - 8), self.last_tcp_header
            )
        return None


def read_pcap(path: Path) -> Iterator[tuple[int, bytes, int]]:
    """`(timestamp in µs, frame, linktype)` for every record of a classic pcap.

    The classic format only (what `tcpdump -w` writes); pcapng is refused with
    a message rather than misread.
    """
    with Path(path).open("rb") as handle:
        yield from _pcap_records(handle)


def _pcap_records(handle: BinaryIO) -> Iterator[tuple[int, bytes, int]]:
    header = handle.read(24)
    if len(header) < 24:
        raise ValueError("not a pcap file: shorter than a pcap header")
    magic = header[:4]
    formats = {
        b"\xd4\xc3\xb2\xa1": ("<", 1), b"\xa1\xb2\xc3\xd4": (">", 1),
        b"\x4d\x3c\xb2\xa1": ("<", 1000), b"\xa1\xb2\x3c\x4d": (">", 1000),
    }  # fmt: skip
    if magic == b"\x0a\x0d\x0d\x0a":
        raise ValueError("pcapng is not supported; write classic pcap (tcpdump -w does)")
    if magic not in formats:
        raise ValueError("not a pcap file: unknown magic number")
    endian, divisor = formats[magic]
    linktype = struct.unpack(endian + "I", header[20:24])[0]
    record = struct.Struct(endian + "IIII")
    while True:
        raw = handle.read(16)
        if len(raw) < 16:
            return
        seconds, fraction, captured, _original = record.unpack(raw)
        frame = handle.read(captured)
        if len(frame) < captured:
            return
        yield seconds * 1_000_000 + fraction // divisor, frame, linktype


# ---------------------------------------------------------------------------
# Flows
# ---------------------------------------------------------------------------


class Stats:
    """Count, sum, extremes and a sample variance (Welford), like commons-math."""

    __slots__ = ("n", "total", "low", "high", "_mean", "_m2")

    def __init__(self) -> None:
        self.n = 0
        self.total = 0.0
        self.low = math.inf
        self.high = -math.inf
        self._mean = 0.0
        self._m2 = 0.0

    def add(self, value: float) -> None:
        self.n += 1
        self.total += value
        self.low = min(self.low, value)
        self.high = max(self.high, value)
        delta = value - self._mean
        self._mean += delta / self.n
        self._m2 += delta * (value - self._mean)

    @property
    def mean(self) -> float:
        return self._mean if self.n else 0.0

    @property
    def variance(self) -> float:
        return self._m2 / (self.n - 1) if self.n > 1 else 0.0

    @property
    def std(self) -> float:
        return math.sqrt(self.variance)

    @property
    def minimum(self) -> float:
        return self.low if self.n else 0.0

    @property
    def maximum(self) -> float:
        return self.high if self.n else 0.0


FlowKey = tuple[str, int, str, int, int]


@dataclass(slots=True)
class Flow:
    """One bidirectional flow in progress, accumulating what the columns need."""

    src: str
    sport: int
    dst: str
    dport: int
    proto: int
    start: int
    last: int
    first_flags: int
    start_active: int
    end_active: int
    length: Stats = field(default_factory=Stats)
    fwd: Stats = field(default_factory=Stats)
    bwd: Stats = field(default_factory=Stats)
    flow_iat: Stats = field(default_factory=Stats)
    fwd_iat: Stats = field(default_factory=Stats)
    bwd_iat: Stats = field(default_factory=Stats)
    active: Stats = field(default_factory=Stats)
    idle: Stats = field(default_factory=Stats)
    fwd_header: int = 0
    bwd_header: int = 0
    init_win_fwd: int = -1
    init_win_bwd: int = -1
    min_seg_fwd: int = 0
    act_data_fwd: int = 0
    fwd_last: int | None = None
    bwd_last: int | None = None

    @classmethod
    def begin(cls, packet: Packet, orientation: FlowKey | None = None) -> Flow:
        """CICFlowMeter's `firstPacket`: the first packet is always forward.

        `orientation` is the previous flow's, kept across a timeout restart the
        way CICFlowMeter kept it: the restarting packet is still accounted as
        forward, and only later packets are judged against the old direction.
        """
        src, sport, dst, dport, proto = orientation or (
            packet.src,
            packet.sport,
            packet.dst,
            packet.dport,
            packet.proto,
        )
        flow = cls(
            src, sport, dst, dport, proto,
            start=packet.ts_us, last=packet.ts_us, first_flags=packet.flags,
            start_active=packet.ts_us, end_active=packet.ts_us,
        )  # fmt: skip
        flow.length.add(packet.payload)  # the first packet, counted twice (rule 3)
        flow.length.add(packet.payload)
        flow.fwd.add(packet.payload)
        flow.fwd_header = packet.header
        flow.min_seg_fwd = packet.header
        flow.init_win_fwd = packet.window
        flow.fwd_last = packet.ts_us
        return flow

    @property
    def packets(self) -> int:
        return self.fwd.n + self.bwd.n

    def is_forward(self, packet: Packet) -> bool:
        return packet.src == self.src and packet.sport == self.sport

    def update_active_idle(self, now: int) -> None:
        """Rule 9: a gap over the activity timeout closes an active period."""
        if now - self.end_active > ACTIVITY_TIMEOUT_US:
            if self.end_active - self.start_active > 0:
                self.active.add(self.end_active - self.start_active)
            self.idle.add(now - self.end_active)
            self.start_active = self.end_active = now
        else:
            self.end_active = now

    def add(self, packet: Packet) -> None:
        """CICFlowMeter's `addPacket`, bidirectional branch."""
        now = packet.ts_us
        self.length.add(packet.payload)
        if self.is_forward(packet):
            if packet.payload >= 1:
                self.act_data_fwd += 1  # the first packet never reaches here (rule 6)
            self.fwd.add(packet.payload)
            self.fwd_header += packet.header
            if self.fwd_last is not None and self.fwd.n > 1:
                self.fwd_iat.add(max(0, now - self.fwd_last))
            self.fwd_last = now
            self.min_seg_fwd = min(packet.header, self.min_seg_fwd)
        else:
            self.bwd.add(packet.payload)
            self.init_win_bwd = packet.window  # the last backward window (rule 6)
            self.bwd_header += packet.header
            if self.bwd_last is not None and self.bwd.n > 1:
                self.bwd_iat.add(max(0, now - self.bwd_last))
            self.bwd_last = now
        self.flow_iat.add(max(0, now - self.last))
        self.last = now

    def features(self) -> dict[str, float]:
        """The 70 columns, as CICFlowMeter would have written them."""
        duration = self.last - self.start
        seconds = duration / 1_000_000
        fwd_n, bwd_n = self.fwd.n, self.bwd.n
        row: dict[str, float] = {
            "destination_port": self.dport,
            "flow_duration": duration,
            "total_fwd_packets": fwd_n,
            "total_backward_packets": bwd_n,
            "total_length_of_fwd_packets": self.fwd.total,
            "total_length_of_bwd_packets": self.bwd.total,
            "fwd_packet_length_max": self.fwd.maximum,
            "fwd_packet_length_min": self.fwd.minimum,
            "fwd_packet_length_mean": self.fwd.mean,
            "fwd_packet_length_std": self.fwd.std,
            "bwd_packet_length_max": self.bwd.maximum,
            "bwd_packet_length_min": self.bwd.minimum,
            "bwd_packet_length_mean": self.bwd.mean,
            "bwd_packet_length_std": self.bwd.std,
            "flow_bytes_s": (self.fwd.total + self.bwd.total) / seconds,
            "flow_packets_s": (fwd_n + bwd_n) / seconds,
            "flow_iat_mean": self.flow_iat.mean,
            "flow_iat_std": self.flow_iat.std,
            "flow_iat_max": self.flow_iat.maximum,
            "flow_iat_min": self.flow_iat.minimum,
            "fwd_iat_total": self.fwd_iat.total,
            "fwd_iat_mean": self.fwd_iat.mean,
            "fwd_iat_std": self.fwd_iat.std,
            "fwd_iat_max": self.fwd_iat.maximum,
            "fwd_iat_min": self.fwd_iat.minimum,
            "bwd_iat_total": self.bwd_iat.total,
            "bwd_iat_mean": self.bwd_iat.mean,
            "bwd_iat_std": self.bwd_iat.std,
            "bwd_iat_max": self.bwd_iat.maximum,
            "bwd_iat_min": self.bwd_iat.minimum,
            "fwd_psh_flags": int(bool(self.first_flags & PSH)),
            "fwd_urg_flags": int(bool(self.first_flags & URG)),
            "fwd_header_length": self.fwd_header,
            "bwd_header_length": self.bwd_header,
            "fwd_packets_s": fwd_n / seconds,
            "bwd_packets_s": bwd_n / seconds,
            "min_packet_length": self.length.minimum,
            "max_packet_length": self.length.maximum,
            "packet_length_mean": self.length.mean,
            "packet_length_std": self.length.std,
            "packet_length_variance": self.length.variance,
            "down_up_ratio": bwd_n // fwd_n if fwd_n else 0,
            "average_packet_size": self.length.total / (fwd_n + bwd_n),
            "avg_fwd_segment_size": self.fwd.total / fwd_n if fwd_n else 0.0,
            "avg_bwd_segment_size": self.bwd.total / bwd_n if bwd_n else 0.0,
            "fwd_header_length_1": self.fwd_header,
            "subflow_fwd_packets": fwd_n,
            "subflow_fwd_bytes": self.fwd.total,
            "subflow_bwd_packets": bwd_n,
            "subflow_bwd_bytes": self.bwd.total,
            "init_win_bytes_forward": self.init_win_fwd,
            "init_win_bytes_backward": self.init_win_bwd,
            "act_data_pkt_fwd": self.act_data_fwd,
            "min_seg_size_forward": self.min_seg_fwd,
            "active_mean": self.active.mean,
            "active_std": self.active.std,
            "active_max": self.active.maximum,
            "active_min": self.active.minimum,
            "idle_mean": self.idle.mean,
            "idle_std": self.idle.std,
            "idle_max": self.idle.maximum,
            "idle_min": self.idle.minimum,
        }
        for column, bit in FLAG_COLUMNS.items():
            row[column] = int(bool(self.first_flags & bit))
        return {column: float(row[column]) for column in FEATURE_COLUMNS}


@dataclass(slots=True)
class MeteredFlow:
    """A finished flow: its features, and the observed endpoints the CSVs stripped."""

    features: dict[str, float]
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    protocol: int
    started_us: int
    ended_us: int

    def as_record(self) -> dict[str, Any]:
        return {
            "src_ip": self.src_ip,
            "src_port": self.src_port,
            "dst_ip": self.dst_ip,
            "dst_port": self.dst_port,
            "protocol": "tcp" if self.protocol == TCP else "udp",
        }


@dataclass
class MeterCounts:
    packets: int = 0
    undecoded: int = 0
    flows: int = 0
    unscoreable: int = 0  # rule 10: zero-duration flows


class FlowMeter:
    """Packets in, finished flows out. Not thread-safe: one capture feeds one meter."""

    def __init__(self, idle_flush_s: float = DEFAULT_IDLE_FLUSH_S) -> None:
        self.decoder = Decoder()
        self.active: dict[FlowKey, Flow] = {}
        self.idle_flush_us = int(idle_flush_s * 1_000_000)
        self.counts = MeterCounts()

    @staticmethod
    def _keys(packet: Packet) -> tuple[FlowKey, FlowKey]:
        forward = (packet.src, packet.sport, packet.dst, packet.dport, packet.proto)
        backward = (packet.dst, packet.dport, packet.src, packet.sport, packet.proto)
        return forward, backward

    def _finish(self, flow: Flow) -> MeteredFlow | None:
        if flow.last - flow.start <= 0:
            self.counts.unscoreable += 1
            return None
        self.counts.flows += 1
        return MeteredFlow(
            flow.features(), flow.src, flow.sport, flow.dst, flow.dport, flow.proto,
            flow.start, flow.last,
        )  # fmt: skip

    def feed(
        self, frame: bytes, ts_us: int, linktype: int = LINKTYPE_ETHERNET
    ) -> list[MeteredFlow]:
        """Decode one frame and meter it; returns any flows it finished."""
        self.counts.packets += 1
        packet = self.decoder.decode(frame, ts_us, linktype)
        if packet is None:
            self.counts.undecoded += 1
            return []
        return self.add(packet)

    def add(self, packet: Packet) -> list[MeteredFlow]:
        """CICFlowMeter's `FlowGenerator.addPacket`, V3 semantics (rule 2)."""
        finished: list[MeteredFlow] = []
        forward, backward = self._keys(packet)
        key = forward if forward in self.active else backward if backward in self.active else None
        if key is None:
            self.active[forward] = Flow.begin(packet)
            return finished

        flow = self.active[key]
        if packet.ts_us - flow.start > FLOW_TIMEOUT_US:
            if flow.packets > 1:
                done = self._finish(flow)
                if done is not None:
                    finished.append(done)
            orientation = (flow.src, flow.sport, flow.dst, flow.dport, flow.proto)
            self.active[key] = Flow.begin(packet, orientation)
        elif packet.proto == TCP and packet.flags & FIN:
            flow.add(packet)
            del self.active[key]
            done = self._finish(flow)
            if done is not None:
                finished.append(done)
        else:
            flow.update_active_idle(packet.ts_us)
            flow.add(packet)
        return finished

    def flush_idle(self, now_us: int) -> list[MeteredFlow]:
        """Emit flows silent for longer than the idle flush (the live departure)."""
        stale = [
            key for key, flow in self.active.items() if now_us - flow.last > self.idle_flush_us
        ]
        return self._drain(stale)

    def flush_all(self) -> list[MeteredFlow]:
        """Emit everything still open: the end of a pcap, or a stopped capture."""
        return self._drain(list(self.active))

    def _drain(self, keys: list[FlowKey]) -> list[MeteredFlow]:
        finished = []
        for key in keys:
            flow = self.active.pop(key)
            if flow.packets > 1:
                done = self._finish(flow)
                if done is not None:
                    finished.append(done)
            else:
                self.counts.unscoreable += 1
        return finished


def meter_pcap(
    path: Path, idle_flush_s: float = DEFAULT_IDLE_FLUSH_S
) -> tuple[list[MeteredFlow], MeterCounts]:
    """Every flow in a pcap, in the order they finished."""
    meter = FlowMeter(idle_flush_s)
    flows: list[MeteredFlow] = []
    for ts_us, frame, linktype in read_pcap(path):
        flows.extend(meter.feed(frame, ts_us, linktype))
    flows.extend(meter.flush_all())
    return flows, meter.counts
