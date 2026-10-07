"""Phase 9 -- live traffic, through the same features, models and pipeline.

A second traffic source beside replay, never a replacement for it. Captured
packets become flows in `app.flowmeter` -- the 70 columns, computed the way
CICIDS2017's were -- and those flows go through `ModelBundle.score_batch` and
`app.pipeline.ingest_batch` exactly as replayed rows do. Live traffic needing
its own scoring code would break the train/serve-skew defence the feature
contract exists to provide.

**Authorisation is a hard precondition, and it is configuration.** Capture is
lawful only on a network, device or lab you own or are explicitly authorised to
monitor. So the API captures only on interfaces listed in `IDS_LIVE_INTERFACES`
-- written by whoever owns the network, empty by default -- and reads pcaps only
from `IDS_LIVE_PCAP_DIR`. No request can name an interface or a path outside
those, and the refusal says why.

**Shadow first.** A model trained on 2017 lab traffic over-fires on a real
network: different applications, mostly TLS, a different "normal". So a capture
runs in one of two modes:

* `shadow` -- score everything, alert no one. Each flow's Stage 1 confidence,
  its Stage 2 error and what the dataset thresholds *would* have raised go to
  `shadow_scores`. `python -m training.calibrate_live` cuts a local `tau_anom`
  from them: the same percentile the dataset threshold was cut at, of this
  network's own reconstruction error.
* `alert` -- flows reach the queue with their observed endpoints, and Stage 2
  decides at the local threshold. Refused until a calibration exists for the
  Stage 2 model that is serving, so no live alert can reach the queue before a
  burn-in has priced this network's normal.

Ground truth is never attached to a live alert: nobody labelled this traffic.
Replay and live capture do not run at once -- one writer keeps the dedupe
upsert's single-writer invariant (`app.dedupe.upsert_alert`).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import queue
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config import settings
from app.db import session_scope
from app.flowmeter import FlowMeter, MeteredFlow, read_pcap
from app.models import ShadowScore
from app.pipeline import ingest_batch
from app.sampling import sample_scored_flows

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.events import EventBroker
    from app.inference import ModelBundle

logger = logging.getLogger(__name__)

LOCAL_THRESHOLD_FILE = "local_threshold.json"
MODES: tuple[str, ...] = ("shadow", "alert")
SOURCES: tuple[str, ...] = ("interface", "pcap")

# Most flows a single scoring call takes; the replay's batch size, so a live
# batch costs what a replay batch costs.
MAX_BATCH_FLOWS = 500

# Linux AF_PACKET: every protocol, and the packet-type value marking a frame the
# host sent. On loopback each packet is seen once sent and once received; the
# sent copy is dropped, as libpcap does, or every loopback flow would double.
ETH_P_ALL = 0x0003
PACKET_OUTGOING = 4


class CaptureNotAuthorised(PermissionError):
    """The interface is not on the operator's allow-list."""


class UnknownPcap(FileNotFoundError):
    """The pcap is not a file in the configured capture directory."""


class IngestAlreadyRunning(RuntimeError):
    """A capture -- or a replay -- is already writing alerts."""


class IngestNotRunning(RuntimeError):
    """A stop was requested with nothing capturing."""


class NotCalibrated(RuntimeError):
    """Alert mode was requested before a shadow burn-in set a local threshold."""


@dataclass
class IngestState:
    """What the capture has done so far, as `/ingest/status` reports it."""

    running: bool = False
    mode: str | None = None
    source: str | None = None
    session_id: str | None = None
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    packets: int = 0
    undecoded: int = 0
    flows: int = 0
    unscoreable: int = 0
    scored: int = 0
    shadow_rows: int = 0
    alerts: int = 0
    tau_anom: float | None = None
    error: str | None = None
    task: asyncio.Task[None] | None = None
    stop_event: threading.Event | None = None

    def as_status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "mode": self.mode,
            "source": self.source,
            "session_id": self.session_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "packets": self.packets,
            "undecoded": self.undecoded,
            "flows": self.flows,
            "unscoreable": self.unscoreable,
            "scored": self.scored,
            "shadow_rows": self.shadow_rows,
            "alerts": self.alerts,
            "tau_anom": self.tau_anom,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# What a capture may touch
# ---------------------------------------------------------------------------


def check_interface(name: str) -> None:
    allowed = settings.live_interface_list
    if name not in allowed:
        raise CaptureNotAuthorised(
            f"{name!r} is not in IDS_LIVE_INTERFACES ({allowed or 'empty'}). Capture is "
            "lawful only on a network you own or are authorised to monitor, so the "
            "interfaces it may run on are configuration written by whoever owns the "
            "network, never a request parameter."
        )


def resolve_pcap(name: str) -> Path:
    """A bare file name inside the capture directory, or a refusal."""
    directory = settings.live_pcap_path
    if not name or Path(name).name != name or name in (".", ".."):
        raise UnknownPcap(f"{name!r} is not a file name; pcaps are read only from {directory}.")
    path = directory / name
    if not path.is_file():
        raise UnknownPcap(f"no pcap named {name!r} in {directory}.")
    return path


def available_pcaps() -> list[str]:
    directory = settings.live_pcap_path
    if not directory.is_dir():
        return []
    return sorted(path.name for path in directory.iterdir() if path.suffix in (".pcap", ".cap"))


# ---------------------------------------------------------------------------
# The local threshold
# ---------------------------------------------------------------------------


def read_local_threshold(artifacts_dir: Path | None = None) -> dict[str, Any] | None:
    path = (artifacts_dir or settings.artifacts_path) / LOCAL_THRESHOLD_FILE
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def usable_local_threshold(bundle: ModelBundle) -> dict[str, Any] | None:
    """The calibration, if it was cut against the Stage 2 model now serving.

    A threshold is a percentile of one model's error distribution; read against
    another model's errors it is a number with no meaning, so a calibration for
    a replaced autoencoder is ignored rather than trusted.
    """
    calibration = read_local_threshold(bundle.artifacts_dir)
    if calibration is None or calibration.get("stage2_version") != bundle.stage2_version:
        return None
    return calibration


def observed_provenance(
    tau_anom: float | None, calibration: dict[str, Any] | None
) -> dict[str, Any]:
    """What a live alert can claim about itself: everything was observed."""
    fields: dict[str, Any] = dict.fromkeys(
        ("src_ip", "dst_ip", "src_port", "dst_port", "protocol"), "observed"
    )
    fields["detected_at"] = "capture_clock"
    fields["tau_anom"] = tau_anom
    fields["calibrated_at"] = None if calibration is None else calibration.get("computed_at")
    fields["note"] = (
        "Captured live on a network this deployment is authorised to monitor: every "
        "address, port and the protocol were observed on the wire, and detected_at is "
        "when the flow finished. Stage 2 decided at the locally calibrated threshold "
        "recorded here, cut from a shadow-mode burn-in on this network, not at the "
        "CICIDS2017 one. No ground truth exists for live traffic."
    )
    return fields


# ---------------------------------------------------------------------------
# Capture threads: packets in, finished flows onto a queue
# ---------------------------------------------------------------------------


def _capture_interface(
    interface: str, meter: FlowMeter, out: queue.Queue[MeteredFlow], stop: threading.Event
) -> None:
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(ETH_P_ALL))
    try:
        sock.bind((interface, 0))
        sock.settimeout(0.5)
        loopback = interface == "lo"
        last_flush = time.monotonic()
        while not stop.is_set():
            try:
                frame, address = sock.recvfrom(65_535)
            except TimeoutError:
                frame, address = None, None
            now_us = time.time_ns() // 1_000
            if frame is not None and not (loopback and address[2] == PACKET_OUTGOING):
                for flow in meter.feed(frame, now_us):
                    out.put(flow)
            if time.monotonic() - last_flush >= 1.0:
                for flow in meter.flush_idle(now_us):
                    out.put(flow)
                last_flush = time.monotonic()
    finally:
        sock.close()
        for flow in meter.flush_all():
            out.put(flow)


def _capture_pcap(
    path: Path, meter: FlowMeter, out: queue.Queue[MeteredFlow], stop: threading.Event
) -> None:
    for ts_us, frame, linktype in read_pcap(path):
        if stop.is_set():
            break
        for flow in meter.feed(frame, ts_us, linktype):
            out.put(flow)
    for flow in meter.flush_all():
        out.put(flow)


# ---------------------------------------------------------------------------
# Scoring: the same calls a replay makes
# ---------------------------------------------------------------------------


def _drain(out: queue.Queue[MeteredFlow], limit: int) -> list[MeteredFlow]:
    batch: list[MeteredFlow] = []
    while len(batch) < limit:
        try:
            batch.append(out.get_nowait())
        except queue.Empty:
            break
    return batch


def score_flows(
    *,
    bundle: ModelBundle,
    broker: EventBroker | None,
    state: IngestState,
    flows: list[MeteredFlow],
    calibration: dict[str, Any] | None,
) -> None:
    """Score one batch of finished flows, in shadow or alert mode."""
    features = [flow.features for flow in flows]
    endpoints = [flow.as_record() for flow in flows]
    finished_at = dt.datetime.fromtimestamp(max(f.ended_us for f in flows) / 1e6, dt.UTC)

    with session_scope() as session:
        if state.mode == "shadow":
            # The dataset thresholds, on purpose: `would_alert` is what the
            # shipped model would have put in front of an analyst on this network.
            decisions = bundle.score_batch(features)
            session.add_all(
                ShadowScore(
                    session_id=state.session_id,
                    captured_at=dt.datetime.fromtimestamp(flow.ended_us / 1e6, dt.UTC),
                    capture_source=state.source,
                    model_version=decision["model_version"],
                    src_ip=flow.src_ip,
                    src_port=flow.src_port,
                    dst_ip=flow.dst_ip,
                    dst_port=flow.dst_port,
                    protocol=endpoint["protocol"],
                    stage1_confidence=decision["confidence"],
                    stage2_error=decision["stage2_error"],
                    would_alert=decision["kind"],
                )
                for flow, endpoint, decision in zip(flows, endpoints, decisions, strict=True)
            )
            # Live traffic feeds the drift monitor too: shadow mode is exactly
            # when the distance from the training distribution is worth seeing.
            sample_scored_flows(
                session,
                flows=features,
                decisions=decisions,
                scored_at=finished_at,
                source="live",
                start_index=state.scored,
            )
            state.shadow_rows += len(flows)
        else:
            decisions = bundle.score_batch(features, tau_anom=state.tau_anom)
            alerts = ingest_batch(
                session,
                flows=features,
                decisions=decisions,
                bundle=bundle,
                detected_at=finished_at,
                source="live",
                broker=broker,
                start_index=state.scored,
                endpoints=endpoints,
                provenance_note=observed_provenance(state.tau_anom, calibration),
                tau_anom=state.tau_anom,
                error_histogram=None if calibration is None else calibration.get("error_histogram"),
            )
            state.alerts += len(alerts)
    state.scored += len(flows)


async def _run(
    *,
    bundle: ModelBundle,
    broker: EventBroker,
    state: IngestState,
    target: tuple[str, Any],
    calibration: dict[str, Any] | None,
) -> None:
    kind, where = target
    meter = FlowMeter(settings.live_idle_flush_s)
    out: queue.Queue[MeteredFlow] = queue.Queue()
    stop = state.stop_event
    assert stop is not None
    worker = threading.Thread(
        target=_capture_interface if kind == "interface" else _capture_pcap,
        args=(where, meter, out, stop),
        name=f"capture-{kind}",
        daemon=True,
    )
    worker.start()
    try:
        while True:
            finished = not worker.is_alive()
            batch = _drain(out, MAX_BATCH_FLOWS)
            while batch:
                await asyncio.to_thread(
                    score_flows,
                    bundle=bundle,
                    broker=broker,
                    state=state,
                    flows=batch,
                    calibration=calibration,
                )
                batch = _drain(out, MAX_BATCH_FLOWS)
            counts = meter.counts
            state.packets, state.undecoded = counts.packets, counts.undecoded
            state.flows, state.unscoreable = counts.flows, counts.unscoreable
            if finished and out.empty():
                break
            await asyncio.sleep(settings.live_batch_interval_s)
    except Exception as exc:  # noqa: BLE001 - reported on the status, not swallowed
        logger.exception("live capture stopped on an error")
        state.error = f"{type(exc).__name__}: {exc}"
        stop.set()
    finally:
        state.running = False
        state.finished_at = dt.datetime.now(dt.UTC)
        state.task = None
        broker.source_stopped()
        logger.info(
            "capture %s finished: %d packets, %d flows, %d scored, %d alerts",
            state.source,
            state.packets,
            state.flows,
            state.scored,
            state.alerts,
        )


async def start_ingest(
    *,
    bundle: ModelBundle,
    broker: EventBroker,
    state: IngestState,
    replay_running: bool,
    source: str,
    interface: str | None,
    pcap: str | None,
    mode: str,
) -> dict[str, Any]:
    """Validate everything, then start; a refused request leaves nothing armed."""
    if state.running or replay_running:
        raise IngestAlreadyRunning(
            "a replay is running" if replay_running else f"a capture of {state.source} is running"
        )
    if mode not in MODES or source not in SOURCES:
        raise ValueError(f"mode must be one of {list(MODES)} and source one of {list(SOURCES)}")
    if not bundle.stage2_ready:
        raise ValueError("Stage 2 is not loaded, so there is nothing to calibrate or score with")

    if source == "interface":
        if not interface:
            raise ValueError("source 'interface' needs an interface name")
        check_interface(interface)
        target: tuple[str, Any] = ("interface", interface)
        label = f"interface:{interface}"
    else:
        if not pcap:
            raise ValueError("source 'pcap' needs a pcap file name")
        path = resolve_pcap(pcap)
        target, label = ("pcap", path), f"pcap:{path.name}"

    calibration = usable_local_threshold(bundle)
    if mode == "alert" and calibration is None:
        raise NotCalibrated(
            "alert mode needs a local tau_anom calibrated for the Stage 2 model that is "
            f"serving ({bundle.stage2_version}). Run a shadow capture first, then "
            "`python -m training.calibrate_live`: a 2017 threshold on this network would "
            "flood the queue with its ordinary traffic."
        )

    state.running = True
    state.mode, state.source = mode, label
    state.session_id = uuid.uuid4().hex
    state.started_at, state.finished_at = dt.datetime.now(dt.UTC), None
    state.packets = state.undecoded = state.flows = state.unscoreable = 0
    state.scored = state.shadow_rows = state.alerts = 0
    state.tau_anom = calibration["tau_anom_local"] if mode == "alert" else bundle.tau_anom
    state.error = None
    state.stop_event = threading.Event()
    broker.source_started(f"live:{label}")
    state.task = asyncio.create_task(
        _run(bundle=bundle, broker=broker, state=state, target=target, calibration=calibration)
    )
    return state.as_status()


async def stop_ingest(*, state: IngestState) -> dict[str, Any]:
    """Signal the capture to stop and wait for its last flows to be scored."""
    if not state.running or state.task is None or state.stop_event is None:
        raise IngestNotRunning("no capture is running; POST /ingest/start begins one")
    state.stop_event.set()
    task = state.task
    try:
        await asyncio.wait_for(task, timeout=30)
    except TimeoutError:
        task.cancel()
    return state.as_status()
