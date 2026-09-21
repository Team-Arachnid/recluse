"""Phase 9 -- live traffic ingestion.

A second traffic source alongside replay, never a replacement for it. Feeds
the exact same features module, the exact same inference path, and the exact
same alert pipeline. Live traffic needing its own scoring code would break the
train/serve-skew defence the feature contract exists to provide.

Authorisation is a hard precondition. Capture runs only against a network,
device or lab that the operator owns or is explicitly authorised to monitor.
Packet capture on a network you do not control is illegal in most places
regardless of intent.

Expected behaviour on first contact with real traffic: a false-positive rate
well above anything the CICIDS2017 validation numbers promised, because the
2017 lab baseline is not todays encrypted household or enterprise traffic.
That is domain shift, not a bug. The handling is a shadow-mode burn-in --
score everything, alert no one -- then recompute tau_anom from the locally
observed benign percentile and document both thresholds and the gap.

Ingest paths: Zeek or Suricata flow logs, or tcpdump plus CICFlowMeter over
the resulting pcap. Same output shape either way.
"""

from __future__ import annotations

from typing import Any


async def start_ingest(*_: Any, **__: Any) -> None:
    raise NotImplementedError("Live capture is implemented in Phase 9 (real traffic).")
