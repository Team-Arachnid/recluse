# Dashboard Screens

The seven screens of the Recluse SOC dashboard: what each one is for, who reads it, which components and endpoints it uses, and which acceptance criteria it has to satisfy. Written as the specification in Phase 0 and checked against `frontend/src/` as it stands at Phase 9; [Code: Frontend](Code-Frontend.md) documents the code itself.

**Status: all seven screens are built, plus a System view.** Phase 6 built screens 1–4 and 7 on the Phase 5 API, Phase 7 added the drift monitor and the feedback loop, and Phase 9 added live capture to the Live Traffic Monitor. Every acceptance criterion below is asserted by a named test in `frontend/src/pages/screens.test.tsx`, and the [Roadmap](Roadmap.md)'s checklist quotes each one.

---

## Screen status

| # | Screen | Purpose | Status |
| --- | --- | --- | --- |
| 1 | Triage Queue | The landing page. Open alerts, highest risk first. | Built — Phase 6 |
| 2 | Alert Detail | Side drawer answering why / what-it-is / how-to-fix. | Built — Phase 6 |
| 3 | Live Traffic Monitor | SSE ticker, rate sparklines, draggable threshold, traffic sources. | Built — Phase 6; live capture Phase 9 |
| 4 | Model Performance | Per-class metrics, confusion matrix, PR vs ROC, LOAO. | Built — Phase 6 |
| 5 | Drift Monitor | PSI per feature, baseline overlay, model registry. | Built — Phase 7 |
| 6 | Feedback Loop | New labels since retrain, disagreement rate, retrain trigger. | Built — Phase 7 |
| 7 | Analytics | Trends, family mix, top hosts, SOC throughput, MITRE coverage. | Built — Phase 6 |
| — | System | Live backend health, the false-positive budget, build progress. | Built — Phase 0, a status view since Phase 6 |

The System view was the landing page only until Phase 6. From then the triage queue is home — analysts live in the queue — and System is a status view reached from the navigation, rather than promoted into an overview dashboard.

### Where the code lives

Each screen is a file under `frontend/src/pages/`, composed from panels under `frontend/src/components/`; [Code: Frontend](Code-Frontend.md) maps every file. The routes are `/` (the queue), `/live`, `/model`, `/drift`, `/feedback`, `/analytics` and `/system`. Alert Detail is a drawer opened with `?alert=<id>` over the queue, not a route, so the queue behind it is never lost.

The colours are tokens in `frontend/src/index.css`, each meaning one thing: crimson for attack (known families, critical severity, the primary action), ochre for Stage 2 and the unclassified-anomaly channel, green for resolved and benign, slate for the baseline. `Badge` has a `novel` variant of its own rather than reusing `critical`, so an `UNCLASSIFIED_ANOMALY` is distinguishable at a glance from a high-severity known attack — and it always carries its glyph and its words too, because colour alone fails a colourblind analyst.

---

## 1. Triage Queue

**For:** the analyst working a shift. This is where they live, so this is the landing page.

**Status:** built, Phase 6.

### Why the queue and not an overview dashboard

The ordering of these screens is a design argument, not a list. Most security tools open on a dashboard of counters and gauges, and an analyst's first action is to click past it into the list of things that need judging. Every one of those clicks is pure overhead repeated hundreds of times a shift.

Recluse opens on the work. The aggregate view exists — it is screen 7 — but it is reached from a nav link, because it answers a different question ("what happened this week") for a different reader (a team lead, a reviewer). Building the queue as the landing page and analytics as a secondary screen states plainly which of the two readers the tool is for.

There is also a subtractive argument. An overview landing page invites a hero tile, and the most tempting hero tile is an accuracy percentage — the exact failure mode this project is built to avoid. On traffic that is 99% benign, a model that always answers "benign" scores 99%. Opening on a work queue removes the slot that number would have filled.

### Layout

```
┌────────────────────────────────────────────────────────────────────────┐
│  Recluse        Queue | Live | Model | Drift | Feedback | Analytics    │
├────────────────────────────────────────────────────────────────────────┤
│  alerts last hour │ alerts/analyst/hour │ current threshold │ hosts    │  ← thin stat strip
├────────────────────────────────────────────────────────────────────────┤
│ [severity ▾] [class ▾] [24h ▾] [verdict ▾]  (UNCLASSIFIED ANOMALY)    │  ← filter chips
├────────────────────────────────────────────────────────────────────────┤
│ ☐ │ time  │ source → destination │ sev │ verdict │ conf │ crit │ st │ n│
│ ☐ │ 14:22 │ 10.0.0.5 → 10.0.2.1  │ HIGH│   —     │ 0.94 │ high │open│ 7│
│ ☐ │ 14:21 │ 10.0.0.9 → 10.0.2.4  │NOVEL│   —     │  —   │ med  │open│ 1│  ← distinct badge
│ ☐ │ ...                                                               │
├────────────────────────────────────────────────────────────────────────┤
│ 2 selected      [ Dismiss selected ]                                   │
└────────────────────────────────────────────────────────────────────────┘
```

### Columns

| Column | Source field | Note |
| --- | --- | --- |
| time | `detected_at` | Displayed, never the sort key |
| source → destination | `src_ip`, `src_port`, `dst_ip`, `dst_port` | Monospace, tabular figures |
| severity | `severity` | `Badge` variant per level |
| verdict | latest `analyst_verdicts.verdict` | Empty until judged |
| confidence | `confidence` | Null for pure anomaly alerts |
| asset criticality | `asset_criticality` | From enrichment |
| status | `status` | `open` / `in_review` / `closed` / `dismissed` |
| count | `occurrence_count` | The dedupe count; 5,000 flows is one row |

**Sorted by risk score, not timestamp.** `risk_score` is a dedicated column on `alerts` precisely so the queue never has to sort by time, and the composite index `ix_alerts_status_risk_score` exists to serve exactly this query. Sorting a triage queue chronologically ranks alerts by when a packet happened rather than by what needs attention first.

### Filters

Severity, class, time window, verdict status, and a one-click `UNCLASSIFIED_ANOMALY` chip. The chip is not a nicety: it is the answer to "what does this catch that a signature IDS doesn't", and it has to be reachable in a single click during a demo. `ix_alerts_kind_detected_at` was created for it.

### Components and libraries

| Concern | Choice |
| --- | --- |
| Table | TanStack Table |
| Row virtualisation | TanStack Virtual |
| Chips, badges, buttons, cards | shadcn/ui primitives in `components/ui/` |
| Icons | lucide-react |
| Server state | TanStack Query |

### Endpoints

| Call | Purpose |
| --- | --- |
| `GET /api/v1/alerts` | The rows, with filter, sort and cursor pagination |
| `GET /api/v1/alerts/stats` | The counts in the stat strip |
| `GET /api/v1/metrics/threshold` | The current-threshold figure in the stat strip |
| `PATCH /api/v1/alerts/status` | Bulk dismiss, in one request |
| `GET /api/v1/stream` | New alerts arriving during a replay or a live capture |

### Interactions

- Click a row → opens [Alert Detail](#2-alert-detail) as a side drawer; queue position is preserved.
- Bulk select → dismiss.
- Filter chip → refetch with new query parameters.
- New alerts arrive over SSE and are merged into the queue without a full refetch.

### Acceptance criteria (Part 13, frontend)

- Triage queue is the landing page, sorted by risk.
- `UNCLASSIFIED_ANOMALY` visually distinct and filterable in one click.
- Verdict submission invalidates and refreshes the queue.
- No accuracy hero tile anywhere.

### Deliberately absent here

A large accuracy percentage. If the stat strip needs a headline number it is alerts per analyst hour, or Stage 2 novel-attack recall — a number that tells the SOC lead something they can act on. The first is measured, and the figure to show is the *fused* one rather than Stage 1's: Stage 1 alone costs **20.3 alerts per analyst per hour** on the test day, but the shipped two-stage pipeline costs **7,449** there, because `tau_anom` is a benign percentile rather than a staffing decision (`reports/loao.md`). Showing 20.3 on a dashboard driven by both stages would understate the queue by two orders of magnitude. The honest strip shows the fused number, and the threshold slider is what moves it. The second is measured both ways as of Phase 4: Stage 2 alone in `reports/phase3_anomaly.md`, and fused per held-out family in `reports/loao.md` — 75.5% of a DoS family the classifier was refitted without, which is the figure this screen should show.

---

## 2. Alert Detail

**For:** the analyst who just clicked a row and has about four seconds of attention before deciding what to do.

**Status:** built, Phase 6.

A side drawer, not a route change, so the queue behind it is never lost and the analyst does not have to re-find their place after every alert.

### The why / what-it-is / how-to-fix ordering

The drawer is structured around three questions in a fixed order, because the order is the point.

```
┌─ Alert #1284 ────────────────────────────────── [ × ] ─┐
│  UNCLASSIFIED ANOMALY   risk 0.94   ×7 occurrences     │
├────────────────────────────────────────────────────────┤
│ 1. WHY WAS THIS FLAGGED                                │
│    "2,400 distinct destination ports contacted in 8    │
│     seconds from a single source."          ← sentence │
│                                               on top   │
│    ┌──────────────────────────────────────┐            │
│    │ dst_port_nunique   ████████████████  │            │
│    │ flow_duration      ██████            │  ← signed  │
│    │ fwd_pkt_len_mean   ███               │    bars    │
│    └──────────────────────────────────────┘            │
│    Raw flow record, monospace                          │
├────────────────────────────────────────────────────────┤
│ 2. WHAT THIS LIKELY IS                                 │
│    No known technique. This is exactly what Stage 2    │
│    exists to catch.                                    │
│    Host context: 3 other alerts from 10.0.0.9 in 24h   │
├────────────────────────────────────────────────────────┤
│ 3. HOW TO FIX IT                                       │
│    No playbook yet — escalate for manual               │
│    investigation.                                      │
├────────────────────────────────────────────────────────┤
│  [demo only] ground truth: Infiltration                │
│  [ True positive ] [ False positive ] [ Need more info]│
└────────────────────────────────────────────────────────┘
```

**Why first.** An alert with a score and no reason is an alert an analyst ignores. The generated English sentence sits *above* the chart, not buried under it — a chart requires interpretation, a sentence does not. Underneath it: a signed horizontal bar chart (Recharts) showing the top-5 TreeSHAP contributors for a Stage 1 alert, or the top-5 per-feature reconstruction errors for a Stage 2 alert. Then the raw flow record in monospace, for the analyst who wants to check the model's work.

The two explainers are different on purpose. `backend/app/explain.py` uses TreeSHAP for the tree model and, for the autoencoder, `per_feature_error = (x - x_hat) ** 2` with `argsort(...)[-5:]` — the features the model failed hardest to reconstruct are precisely why the row looks anomalous. That is faster than KernelSHAP on a neural net, and more faithful to the model than an approximation of it.

**What it is, second.** The MITRE technique ID *plus* its one-line plain-English description, from the static table in `backend/app/mitre.py`. Not a bare code linking out to attack.mitre.org — a sentence the analyst can read without opening a second tab. Underneath, host context: other alerts from this source in the last 24 hours, so a scan-then-exploit sequence reads as one story instead of three disconnected rows. That list comes from `GET /api/v1/alerts/{id}/related`, backed by `ix_alerts_src_ip_detected_at`.

**How to fix it, third.** The recommended-response playbook for that family, rendered as a short checklist an analyst can scan in three seconds mid-shift, not a paragraph. It comes from `backend/app/remediation.py`, which is a static, reviewed lookup and explicitly not a generative one. A fixed playbook is something a SOC can trust; advice improvised per alert has to be re-verified every time, which defeats the purpose of having it.

### The honest no-playbook case

For `UNCLASSIFIED_ANOMALY`, panels 2 and 3 do **not** guess.

| Panel | Known family | Unclassified anomaly |
| --- | --- | --- |
| What this likely is | Technique ID + plain-English description | "Doesn't match a known technique — this is exactly what Stage 2 exists to catch." |
| How to fix it | The family's playbook checklist | "No playbook yet — escalate for manual investigation." |

This is not a gap in the UI; it is the system reporting its own state accurately. Stage 2 fired *because* Stage 1 could not name the traffic. Inventing a technique ID or a remediation checklist for it would be fabrication dressed as helpfulness, and a wrong playbook does more damage than an honest shrug — the analyst follows it, wastes the shift, and stops trusting the playbooks that are correct.

The database enforces the same rule: `ck_alerts_family_matches_kind` makes `family IS NULL` mandatory for `UNCLASSIFIED_ANOMALY`, so no code path can produce an anomaly carrying a fabricated family. See [Database Schema](Database-Schema.md).

### Ground truth and verdicts

Ground-truth labels exist only for replayed dataset rows (`alerts.ground_truth_label`, always null for live capture). They are shown only in replay mode and clearly badged as demo-only, because a label the model did not produce must never read as a model output.

The footer holds three buttons — True positive / False positive / Need more info — writing to `analyst_verdicts` through `POST /api/v1/alerts/{id}/verdict`. On success the mutation invalidates the alerts query, so the queue behind the drawer updates itself rather than going stale.

### Components, libraries, endpoints

| Concern | Choice |
| --- | --- |
| Drawer, cards, badges, buttons | shadcn/ui |
| Explanation chart | Recharts horizontal bar, signed |
| Verdict submission | TanStack Query mutation with query invalidation |

| Call | Purpose |
| --- | --- |
| `GET /api/v1/alerts/{id}` | Explanation, narrative, MITRE, remediation, raw flow |
| `GET /api/v1/alerts/{id}/related` | Host context, 24h window |
| `POST /api/v1/alerts/{id}/verdict` | The footer buttons |

### Acceptance criteria (Part 13, frontend)

- Alert Detail answers why / what-it-is / how-to-fix for every alert, including the honest "no playbook" case for unclassified anomalies.
- Verdict submission invalidates and refreshes the queue.
- `UNCLASSIFIED_ANOMALY` visually distinct.

### No block button

There is none, and there is no endpoint one could call — see [API Reference](API-Reference.md#explicitly-absent). If containment were ever added it would be manual, confirmed by a dialog naming the exact host and action, and audited.

---

## 3. Live Traffic Monitor

**For:** whoever is watching traffic in real time, and for the demo.

**Status:** built, Phase 6.

### Layout

- A histogram of anomaly scores with the threshold drawn as a draggable vertical line — it leads the screen.
- The replay control: the dataset (the held-out days where present, and the committed demo sample everywhere) and the speed, 1x, 10x or 100x.
- The live-capture control (Phase 9): a shadow burn-in or alerting, on an interface listed in `IDS_LIVE_INTERFACES` or a pcap in `IDS_LIVE_PCAP_DIR`, with the CICIDS2017 Stage 2 threshold and this network's calibrated one side by side. *Start alerting* is disabled until a burn-in has been calibrated, and the panel says why.
- An SSE ticker of alerts scrolling past, and flows/sec and alerts/sec as two charts.

### The draggable threshold

This is the strongest single element in the application. The analyst drags the threshold line across the anomaly-score histogram, and the projected alerts/hour figure updates live from `GET /api/v1/metrics/threshold?t=<value>`.

| Property | Value |
| --- | --- |
| Request debounce | **150 ms** |
| Endpoint | `GET /api/v1/metrics/threshold` |
| Parameter | `t`, float, already validated server-side as `>= 0.0` and `<= 1.0` |
| Feedback while in flight | The line follows the pointer immediately; the projected figure updates when the response lands |

The debounce matters. A drag fires pointer events at display refresh rate; without it, a two-second drag issues a hundred-odd requests, the responses arrive out of order, and the number flickers between stale values. At 150 ms the line stays smooth because it is driven by local state, while the network sees roughly six requests over that same drag — fast enough to feel live, slow enough to stay ordered.

One interaction communicates the precision/recall tradeoff better than any table could: at this setting, 40 alerts an hour; at that one, 400. It also makes the point that the threshold is a business decision owned by the SOC lead, not a constant baked into a model. The budget behind it is configuration — `IDS_ANALYST_CAPACITY_PER_HOUR`, `IDS_ANALYST_SHIFT_HOURS` and `IDS_EXPECTED_DAILY_FLOW_VOLUME` — and the derived `target_fpr` is logged at API startup.

The histogram bins come from the persisted benign reconstruction-error distribution (`ModelBundle.benign_error_histogram`), which Phase 3 writes as bins rather than raw rows — sixty log-spaced bins with their counts and reference percentiles, on the model card. **Those bins exist**, and `ErrorHistogram.above(tau)` is the arithmetic this slider does: it projects an alert count from the bins rather than rescoring a day of traffic on every drag.

### Endpoints

| Call | Purpose |
| --- | --- |
| `GET /api/v1/metrics/anomaly-histogram` | The bins the threshold line is drawn across |
| `GET /api/v1/metrics/threshold` | Projected volume at the dragged threshold |
| `GET /api/v1/replay/status`, `GET /api/v1/replay/datasets` | What is running, and what can |
| `POST /api/v1/replay/start` / `stop` | The replay control |
| `GET /api/v1/ingest/status`, `POST /api/v1/ingest/start` / `stop` | The live-capture control |
| `GET /api/v1/stream` | The ticker and the rate series |

### Acceptance criterion (Part 13, frontend)

- Draggable threshold updating projected alert volume live.

---

## 4. Model Performance

**For:** anyone assessing whether the models work — a reviewer, a team lead, the person presenting.

**Status:** built, Phase 6.

### Contents

| Panel | Contents |
| --- | --- |
| Per-class table | Precision, recall, F1, support per family |
| Confusion matrix | Heatmap |
| PR and ROC curves | **Side by side**, with the caption below |
| LOAO panel | The headline evidence, given real visual weight |

### The PR-vs-ROC caption requirement

The two curves are rendered side by side deliberately, and the pairing carries a required caption explaining that the gap between them is why ROC-AUC misleads on imbalanced data.

The reason, stated once so it can be written into the caption: ROC plots true-positive rate against false-positive rate, and the false-positive rate has the count of benign rows in its denominator. On traffic that is 99% benign, that denominator is enormous, so thousands of false alerts barely move the curve and the classifier looks excellent. Precision has the count of *predicted positives* in its denominator, so the same thousands of false alerts collapse it immediately. The PR curve is the one that reflects what the analyst's queue actually looks like. PR-AUC is therefore the headline metric, and accuracy may appear in the per-class table but never as a headline.

Showing the two next to each other turns that argument into something a reviewer can see rather than something the README asserts.

### The LOAO panel

Leave-one-attack-out: an entire attack family is removed from supervised training, the model is retrained, the autoencoder is left untouched because it never saw attacks anyway, and the full fusion pipeline is run on a test set containing that family. The table records, per family, what fraction was caught by Stage 1, what fraction by Stage 2, the total, and what was missed.

The Stage 2 column is the project's headline claim made measurable. The Missed column is not an embarrassment to be trimmed — a table with honest misses reads as engineering; a table of 99s reads as a bug.

**The LOAO numbers exist and are now served.** Phase 4 measured them into `reports/loao.md` and `backend/artifacts/metrics_loao.json`; Phase 5's `GET /api/v1/metrics/model` returns the whole table under its `loao` key, alongside the per-class metrics, both curves, and the budget the thresholds were cut against. The screen renders, per family: rows, Stage 1 recall, Stage 2 recall, total and **Missed**, plus the share of Stage 1's catches that carried the right family name, which under hold-out is zero by construction and is what stops a recall figure reading as classification.

### Endpoint

`GET /api/v1/metrics/model` — per-class metrics, both curve series, and the LOAO results.

### Acceptance criteria (Part 13, frontend)

- PR and ROC rendered side by side with explanatory caption.
- LOAO panel present and prominent.
- No accuracy hero tile anywhere.

---

## 5. Drift Monitor

**For:** whoever owns the model in production.

**Status:** built, Phase 7.

### Contents

| Panel | Contents |
| --- | --- |
| PSI per feature, worst first | The latest snapshot, every feature with its band as a word, against warning bands at 0.1 (moderate) and 0.25 (significant) |
| PSI over time | A trend for a readable handful of features — not one line per feature, because no palette separates ninety-two series |
| Baseline overlay | The observed score distribution over the training benign one; separation means the baseline has moved |
| Retrain banner | Raised when any single feature crosses 0.25 |
| Model registry | Versions, thresholds, champion/challenger/archived, and the alerts each one scored |

PSI is `sum over bins of (actual_pct - expected_pct) * ln(actual_pct / expected_pct)`, computed by `python -m training.drift_job` over a systematic sample of scored traffic — not over stored alerts — and served, never computed per request. Before the first snapshot the screen says so plainly instead of drawing a flat line at zero.

### Endpoints

| Call | Purpose |
| --- | --- |
| `GET /api/v1/metrics/drift` | PSI per feature per snapshot |
| `GET /api/v1/models` | The registry table |

Both are served from tables the drift job and the registry write; see [Database Schema](Database-Schema.md).

---

## 6. Feedback Loop

**For:** whoever decides when to retrain.

**Status:** built, Phase 7.

A small screen with a large narrative payoff: it is the clearest signal that the project is a system rather than a script.

| Element | Source |
| --- | --- |
| New analyst labels since last retrain | `analyst_verdicts` rows where `consumed_at IS NULL` |
| TP / FP breakdown | `analyst_verdicts.verdict`, served by `ix_analyst_verdicts_created_at_verdict` |
| Disagreement rate between model and analyst | Verdicts joined against the alert that produced them |
| "Retrain with N new labels" | Queues a run with `POST /api/v1/retrain` — the API never fits a model; `python -m training.retrain` does |
| Run history | `GET /api/v1/retrain`, including the challengers that were declined, with both scores |

The `consumed_at` column exists specifically so "since the last retrain" is answerable without a second table. Promotion is guarded: a challenger is evaluated against the champion on the same held-out set and promoted only on improvement, with the comparison logged.

One safety note that belongs on this screen rather than buried in a job: the benign refit pool for the autoencoder requires analyst FP confirmation before a row enters it, and caps the fraction contributed by any single source host. Without both guards, an attacker who can generate enough traffic can teach the baseline that their traffic is normal.

---

## 7. Analytics

**For:** whoever is not triaging alerts — a team lead, a reviewer, the presenter answering "so what happened this week".

**Status:** built, Phase 6.

Reached from a nav link, not a rebuild of the queue as a dashboard. Screen 1 stays the queue on purpose.

| Panel | Contents |
| --- | --- |
| Alerts over time | Stacked by known family vs `UNCLASSIFIED_ANOMALY`, with a 24h / 7d / 30d / all range picker |
| Attack-family breakdown | Bar or donut of what has actually been seen for the selected range |
| Top targeted hosts and ports | Small ranked table |
| Top source addresses and subnets | Small ranked table |
| SOC throughput | Alerts opened vs resolved, average time-to-verdict, TP/FP rate over time |
| MITRE coverage heatmap | Grid of which techniques have fired and how often |

The unclassified line on the first chart is the novel-detection headline, and it is the line a reviewer will ask about if it spikes. SOC throughput is the metric that argues for the project's existence — it should trend down as the feedback loop does its job.

### Endpoints

| Call | Purpose |
| --- | --- |
| `GET /api/v1/analytics/summary?range=` | Everything except the heatmap. `range` is already validated server-side against `^(24h\|7d\|30d\|all)$`, default `24h`. |
| `GET /api/v1/analytics/mitre-coverage` | The heatmap counts |

### Acceptance criterion (Part 13, frontend)

- Analytics screen shows trends by family, top hosts/sources, SOC throughput, and MITRE coverage.

Same rule as screen 1: no accuracy hero tile here either. If this screen needs one big number, it is alerts per analyst hour or the unclassified-anomaly rate.

---

## System

A status view at `/system`, reached from the navigation: what is running, what it was built from, and what it will never do.

| Element | Detail |
| --- | --- |
| Header | What the two stages do, ending on "it alerts, ranks and explains — it never blocks traffic" |
| Health panel | `GET /api/v1/health` through TanStack Query, polled every `VITE_HEALTH_POLL_MS` ms (default 5000): `status`, `model_version` and `uptime_s`, with a skeleton while pending, a labelled error card when the backend is unreachable, and a manual refresh |
| The false-positive budget | The arithmetic rather than its answer: daily volume, analyst capacity and shift length in, the target false-positive rate and the Stage 1 threshold cut to it out |
| Containment | Why there is no block button |
| Build progress | The ten phases with what each produced; Phase 9 shows as partial — built and run on its own host, its attack exercise not run |
| Principles | Alert-only, temporal splits, benign-only Stage 2, one feature module, leave-one-attack-out |

`model_version` renders whatever is loaded — the committed release's two stages on a fresh deployment, `unloaded` when the artifacts directory holds no model — and the panel shows whichever holds rather than hiding it behind a default. There is no accuracy tile: on traffic that is 99% benign it would be meaningless, and a hero percentage is the exact failure mode this project is built to avoid.

`frontend/src/pages/SystemHealth.test.tsx` asserts the health contract twice — once against a stubbed response to pin the rendering, and once against the real FastAPI process over HTTP, which is what proves "live health data fetched from FastAPI" rather than asserting it.

---

## Frontend rules

These apply to every screen and are checked in review.

| Rule | Why | Where it is already enforced |
| --- | --- | --- |
| **TanStack Query for all server state** | One cache, one retry policy, one invalidation story. Server state is not component state. | `createQueryClient()` in `frontend/src/api/queryClient.ts`; every hook in `api/queries.ts` |
| **No `useEffect` fetch chains** | They produce race conditions, double fetches under StrictMode, and cache-less refetching on every mount. | No screen fetches in an effect. The one `fetch` inside an effect is the stream provider asking whether a traffic source is running, which decides whether to open the `EventSource` |
| **Invalidate alerts on verdict submission** | The queue updates itself instead of showing a row the analyst just judged. | `useSubmitVerdict` invalidates the `alerts` prefix — queue, drawer and stat strip at once; "posts the verdict and refetches the alert queries" |
| **Generated types, never hand-written** | Hand-written types drift from the server silently; a schema change should be a type error, not a runtime surprise. | `make openapi` writes `src/types/api.d.ts` from the committed contract snapshot, and `src/types/contract.test.ts` fails if it differs; `src/api/types.ts` re-exports narrow aliases so components never import the generated file directly |
| **Virtualise the alert table** | It holds tens of thousands of rows during a 100x replay. Rendering them all freezes the tab. | `AlertTable` renders through TanStack Virtual |
| **Skeletons, empty states, error boundaries on every screen** | An empty queue must say "No open alerts", not render a blank page that looks broken. A monitoring tool that fails silently is worse than one that fails loudly. | `components/States.tsx` on every panel, and one `ErrorBoundary` per screen in `App.tsx`, inside the shell so the navigation survives |

A note on the 501 case, from the phased build: `ApiError.isNotImplemented` is true for status 501, and `createQueryClient()` does not retry those, because retrying a route that does not exist yet only delays the message the UI wants to show. No route answers 501 since Phase 9; the handling stays for any route registered ahead of its phase.

---

## Deliberately absent

### No accuracy hero tile, on any screen

Not on the queue, not on analytics, not on model performance. On traffic that is 99% benign, a model that always answers "benign" scores 99% accuracy — the number is uninformative by construction and actively misleading to anyone who reads it quickly. Accuracy may appear as one cell in a per-class table on screen 4. It may never be a headline.

If a screen needs one big number, the candidates are alerts per analyst hour, Stage 2 recall on held-out families, or the unclassified-anomaly rate. Each of those tells the reader something they can act on, and each is what a screen here leads with: the queue's stat strip, the LOAO table, the analytics headline.

### No block button

Nowhere in the UI. There is no endpoint behind it — `backend/tests/test_api_surface.py::test_no_route_mentions_blocking` asserts that no path in the OpenAPI schema contains `block`, `drop` or `quarantine`, and `IDS_ALLOW_AUTO_BLOCK` raises at startup if set to true.

The reason is arithmetic the analyst can check: at the configured expected volume of one million flows a day, a 0.1% false-positive rate is a thousand false alerts. Wiring automatic containment to that rate takes production down. The system alerts, ranks and explains; a human decides what to do with the result.

If containment were ever added it would be manual, confirmed by a dialog naming the exact host and action, scoped to a stated duration, audited with who acted and under which model version, and reversible by a path as easy to reach as the action itself.
