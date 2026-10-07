"""Phase 9 -- the flow meter reproduces CICFlowMeter, quirks included.

Every rule in `app/flowmeter.py` was read off the training rows; these tests pin
each one on packets small enough to work out by hand, so a "fix" that made the
meter more correct and less like the data the models learned would fail here.
"""

from __future__ import annotations

import statistics
from pathlib import Path

import pandas as pd
import pytest

from app.config import settings
from app.flowmeter import (
    ACK,
    CWR,
    ECE,
    FEATURE_COLUMNS,
    FIN,
    FLAG_COLUMNS,
    PSH,
    SYN,
    TCP,
    UDP,
    Decoder,
    FlowMeter,
    Packet,
    meter_pcap,
    read_pcap,
)
from app.release import DEMO_FLOWS_FILE
from tests.packets import (
    CLIENT,
    SERVER,
    ethernet_ipv4_tcp,
    ethernet_ipv4_udp,
    ethernet_ipv6_udp,
    vlan,
    write_pcap,
)


def tcp(ts, forward, flags, payload=0, header=32, window=502):
    src, dst, sport, dport = (CLIENT, SERVER, 40000, 80) if forward else (SERVER, CLIENT, 80, 40000)
    return Packet(ts, src, dst, sport, dport, TCP, payload, header, flags, window)


def handshake_and_request() -> list[Packet]:
    """A short HTTP exchange, closed by the client's FIN."""
    return [
        tcp(0, True, SYN, header=40, window=64240),
        tcp(1_000, False, SYN | ACK, header=40, window=65160),
        tcp(1_500, True, ACK),
        tcp(2_000, True, PSH | ACK, payload=100),
        tcp(3_000, False, ACK, window=509),
        tcp(4_000, False, PSH | ACK, payload=300, window=501),
        tcp(5_000, True, FIN | ACK),
    ]


def meter(packets: list[Packet]) -> tuple[list, FlowMeter]:
    flow_meter = FlowMeter()
    finished = []
    for packet in packets:
        finished.extend(flow_meter.add(packet))
    return finished, flow_meter


def test_a_tcp_exchange_produces_the_columns_cicflowmeter_would() -> None:
    finished, _ = meter(handshake_and_request())

    assert len(finished) == 1, "the client's FIN ends the flow (rule 2)"
    row = finished[0].features
    assert list(row) == list(FEATURE_COLUMNS)

    assert row["destination_port"] == 80
    assert row["flow_duration"] == 5_000
    assert (row["total_fwd_packets"], row["total_backward_packets"]) == (4, 3)
    assert (row["total_length_of_fwd_packets"], row["total_length_of_bwd_packets"]) == (100, 300)
    assert row["fwd_packet_length_std"] == pytest.approx(statistics.stdev([0, 0, 100, 0]))
    assert row["bwd_packet_length_std"] == pytest.approx(statistics.stdev([0, 0, 300]))
    assert row["flow_bytes_s"] == pytest.approx(400 / 0.005)
    assert row["flow_packets_s"] == pytest.approx(7 / 0.005)
    flow_iats = [1_000, 500, 500, 1_000, 1_000, 1_000]
    assert row["flow_iat_mean"] == pytest.approx(statistics.mean(flow_iats))
    assert row["flow_iat_std"] == pytest.approx(statistics.stdev(flow_iats))
    assert (row["fwd_iat_total"], row["fwd_iat_max"], row["fwd_iat_min"]) == (5_000, 3_000, 500)
    assert (row["bwd_iat_total"], row["bwd_iat_mean"]) == (3_000, 1_500)
    assert (row["fwd_header_length"], row["bwd_header_length"]) == (136, 104)
    assert row["fwd_header_length_1"] == row["fwd_header_length"]

    # Rule 3: the first packet counted twice in the all-packet statistics.
    lengths = [0, 0, 0, 0, 100, 0, 300, 0]
    assert row["packet_length_mean"] == pytest.approx(statistics.mean(lengths))
    assert row["packet_length_variance"] == pytest.approx(statistics.variance(lengths))
    assert row["average_packet_size"] == pytest.approx(400 / 7)
    assert row["average_packet_size"] == pytest.approx(row["packet_length_mean"] * 8 / 7)

    # Rule 6: the first packet's window forward, the last one backward, and the
    # first packet's payload never counts as forward data.
    assert row["init_win_bytes_forward"] == 64240
    assert row["init_win_bytes_backward"] == 501
    assert row["act_data_pkt_fwd"] == 1
    assert row["min_seg_size_forward"] == 32

    # Rule 8 and the ratio CICFlowMeter computed with integer division.
    assert (row["subflow_fwd_packets"], row["subflow_bwd_bytes"]) == (4, 300)
    assert row["down_up_ratio"] == 0


def test_the_flag_columns_hold_the_first_packets_flags_permuted() -> None:
    """Rule 5. A SYN-first flow lights the column CICIDS2017 calls PSH."""
    finished, _ = meter(handshake_and_request())
    row = finished[0].features

    lit = {column for column in FLAG_COLUMNS if row[column] == 1}
    assert lit == {"psh_flag_count"}
    assert row["fwd_psh_flags"] == 0  # the first packet carried no PSH

    # The ECN-negotiating SYN, the combination that pinned the mapping down.
    ecn = [tcp(0, True, SYN | ECE | CWR), tcp(800, False, SYN | ACK), tcp(900, True, FIN | ACK)]
    finished, _ = meter(ecn)
    lit = {column for column in FLAG_COLUMNS if finished[0].features[column] == 1}
    assert lit == {"psh_flag_count", "rst_flag_count", "ece_flag_count"}


def test_the_teardown_after_the_first_fin_becomes_its_own_flow() -> None:
    """Rule 2's 'TCP appendix': the server's FIN/ACK and the last ACK."""
    packets = [
        *handshake_and_request(),
        tcp(6_000, False, FIN | ACK, window=501),
        tcp(6_500, True, ACK),
    ]
    finished, flow_meter = meter(packets)
    assert len(finished) == 1
    appendix = flow_meter.flush_all()

    assert len(appendix) == 1
    # The server sent the appendix's first packet, so it is the forward side.
    assert (appendix[0].src_ip, appendix[0].dst_port) == (SERVER, 40000)
    assert appendix[0].features["total_fwd_packets"] == 1
    assert appendix[0].features["urg_flag_count"] == 1  # FIN, in the column that holds it


def test_a_udp_packet_carries_the_last_tcp_header_length() -> None:
    """Rule 7, the reused-decoder artifact the training data is full of."""
    decoder = Decoder()
    decoder.decode(ethernet_ipv4_tcp(header=40), 0)
    udp = decoder.decode(ethernet_ipv4_udp(payload=b"x" * 30), 10)

    assert udp is not None and udp.proto == UDP
    assert udp.header == 40
    assert udp.payload == 30
    assert udp.window == -1


def test_a_flow_older_than_the_timeout_restarts_in_the_same_orientation() -> None:
    first = [tcp(0, True, SYN, header=40), tcp(500, False, SYN | ACK)]
    late = tcp(121_000_000, False, ACK)  # from the server, two minutes on
    finished, flow_meter = meter([*first, late])

    assert len(finished) == 1 and finished[0].features["flow_duration"] == 500
    restarted = next(iter(flow_meter.active.values()))
    assert (restarted.src, restarted.dst) == (CLIENT, SERVER)


def test_zero_duration_and_single_packet_flows_are_counted_not_scored() -> None:
    flow_meter = FlowMeter()
    flow_meter.add(tcp(7, True, SYN))
    flow_meter.add(tcp(7, False, ACK | 0x04))  # same microsecond: duration zero
    flow_meter.add(Packet(9, "10.0.0.9", SERVER, 5353, 53, UDP, 40, 20))  # never answered

    assert flow_meter.flush_all() == []
    assert flow_meter.counts.unscoreable == 2


def test_frames_decode_across_link_layers_and_ip_versions() -> None:
    decoder = Decoder()
    plain = decoder.decode(ethernet_ipv4_tcp(flags=SYN), 1)
    tagged = decoder.decode(vlan(ethernet_ipv4_tcp(flags=SYN)), 1)
    six = decoder.decode(ethernet_ipv6_udp(), 1)

    assert plain is not None and (plain.src, plain.dport, plain.flags) == (CLIENT, 80, SYN)
    assert tagged is not None and tagged.dport == 80
    assert six is not None and six.proto == UDP and six.dst == "2001:db8::1"
    assert decoder.decode(b"\x00" * 10, 1) is None  # truncated: skipped, not fatal


def test_a_pcap_round_trips_through_the_meter(tmp_path: Path) -> None:
    frames = [
        (0, ethernet_ipv4_tcp(flags=SYN, header=40)),
        (1_200, ethernet_ipv4_tcp(flags=SYN | ACK, header=40, forward=False)),
        (2_000, ethernet_ipv4_tcp(flags=FIN | ACK)),
    ]
    path = tmp_path / "capture.pcap"
    write_pcap(path, frames)

    assert [ts for ts, _, _ in read_pcap(path)] == [0, 1_200, 2_000]
    flows, counts = meter_pcap(path)
    assert counts.packets == 3 and counts.flows == 1
    assert flows[0].features["flow_duration"] == 2_000
    assert flows[0].as_record() == {
        "src_ip": CLIENT, "src_port": 40000, "dst_ip": SERVER, "dst_port": 80, "protocol": "tcp",
    }  # fmt: skip

    (tmp_path / "next.pcapng").write_bytes(b"\x0a\x0d\x0d\x0a" + b"\x00" * 40)
    with pytest.raises(ValueError, match="pcapng"):
        list(read_pcap(tmp_path / "next.pcapng"))


def test_the_meter_writes_exactly_the_columns_the_models_were_trained_on() -> None:
    demo = pd.read_parquet(settings.release_path / DEMO_FLOWS_FILE)
    assert list(FEATURE_COLUMNS) == [column for column in demo.columns if column != "label"]
