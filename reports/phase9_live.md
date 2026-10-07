# Phase 9 -- the shadow-mode burn-in

Measured 2026-10-07T03:31:50+00:00 over 7,972 live flows (2026-10-07T02:56:58+00:00 to 2026-10-07T03:11:20+00:00), scored in shadow mode by `stage2-autoencoder-202610070106`: every flow scored, no alert raised.

## Two thresholds

```
tau_anom, CICIDS2017   0.103158   99.5th percentile of a 2017 lab's benign Thursday
tau_anom, local        0.639301   99.5th percentile of this burn-in
ratio                  6.20x
```

| Flagged on this network's own traffic | Share of the burn-in |
| --- | --- |
| Stage 2 at the CICIDS2017 threshold | **11.80%** |
| Stage 2 at the local threshold | 0.50% |
| Stage 1 at `tau_sup` | 0.00% |

The first row is the domain shift, priced: the share of this network's ordinary traffic the shipped Stage 2 threshold would have put in front of an analyst. The local threshold flags its percentile's complement by construction, which is the point -- it is a statement about this network's normal, made from this network.

## Where the errors sit

| Percentile | CICIDS2017 benign (validation day) | This network |
| --- | --- | --- |
| p50 | 0.005237 | 0.02634 |
| p90 | 0.03448 | 0.1168 |
| p99 | 0.06943 | 0.5684 |
| p99.5 | 0.1032 | 0.6393 |
| p99.9 | 0.2755 | 0.8612 |

## What the window was made of

A burn-in teaches the threshold that whatever it saw is normal, so the traffic it was cut from is listed here to be checked rather than assumed.

| Service (protocol/destination port) | Flows |
| --- | --- |
| `tcp/8000` | 3,506 |
| `tcp/5173` | 2,461 |
| `tcp/443` | 198 |
| `udp/53` | 159 |
| `tcp/46663` | 36 |
| `tcp/2025` | 4 |
| `tcp/47186` | 3 |
| `tcp/56574` | 3 |
| `tcp/56578` | 3 |
| `tcp/56588` | 3 |

| Source host | Flows |
| --- | --- |
| `127.0.0.1` | 7,596 |
| `192.0.2.2` | 361 |
| `160.79.104.10` | 6 |
| `192.0.2.1` | 4 |
| `104.16.9.34` | 2 |
| `151.101.64.223` | 1 |
| `104.16.1.34` | 1 |
| `104.16.2.34` | 1 |

Capture sources: `interface:eth0`, `interface:lo`. Burn-in runs: 2.
