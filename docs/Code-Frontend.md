# Code Reference — Frontend

This page documents `frontend/` as it stands at Phase 9: the Vite + React + TypeScript dashboard — the seven screens the brief specifies, plus a System view — its typed API client and its one server-sent-events connection, the components the screens are built from, and the build, test and container tooling. What each screen shows and why is on [Frontend Screens](Frontend-Screens.md); this page is about the code that does it.

Every number on every screen comes from the API. There is no mock data anywhere in `src/` outside `src/test/`, and the API's types are generated from the backend's OpenAPI schema rather than written by hand, so a contract change is a compile error here rather than an `undefined` at runtime.

| File | Lines | Role |
| --- | --- | --- |
| `frontend/index.html` | 21 | The HTML shell: `class="dark"` on `<html>`, the favicons, the `#root` mount |
| `frontend/src/main.tsx` | 21 | Applies the stored theme before the first render, then mounts `<App />` |
| `frontend/src/App.tsx` | 105 | Providers and routes: query client, router, the one SSE connection, the shell, one error boundary per screen |
| `frontend/src/index.css` | 339 | Tailwind v4 entry, the palette in both themes, the bundled fonts, base layer |
| `frontend/src/api/client.ts` | 70 | The single `fetch` wrapper, and `ApiError` |
| `frontend/src/api/queries.ts` | 435 | Every TanStack Query hook and the query-key registry |
| `frontend/src/api/queryClient.ts` | 24 | The `QueryClient` factory and its retry policy |
| `frontend/src/api/stream.tsx` | 239 | `StreamProvider` — the one `EventSource` — and `useAlertStream` |
| `frontend/src/api/types.ts` | 85 | Named aliases over the generated schema types |
| `frontend/src/pages/TriageQueue.tsx` | 174 | Screen 1, the landing page: the triage queue |
| `frontend/src/pages/LiveMonitor.tsx` | 84 | Screen 3: the threshold histogram, the traffic sources, the live feed |
| `frontend/src/pages/ModelPerformance.tsx` | 250 | Screen 4: LOAO first, then the per-class table, confusion matrix and PR/ROC |
| `frontend/src/pages/DriftMonitor.tsx` | 309 | Screen 5: PSI snapshots, the trend, the baseline overlay, the registry |
| `frontend/src/pages/FeedbackLoop.tsx` | 541 | Screen 6: labels, the disagreement rate, retrain requests and their history |
| `frontend/src/pages/Analytics.tsx` | 238 | Screen 7: trends, families, ranked hosts, throughput, MITRE coverage |
| `frontend/src/pages/SystemHealth.tsx` | 206 | System: what is running, the phases, and what the system never does |
| `frontend/src/components/AppShell.tsx` | 443 | The sidebar, the page header and the work surface |
| `frontend/src/components/ErrorBoundary.tsx` | 67 | A render error stays inside the screen that threw it |
| `frontend/src/components/States.tsx` | 77 | Loading, empty and error states |
| `frontend/src/components/StatTile.tsx` | 183 | A metric tile with an optional sparkline drawn only from real series |
| `frontend/src/components/HealthPanel.tsx` | 130 | Live `/health`, on the System screen |
| `frontend/src/components/RecluseLogo.tsx` | 52 | The emblem, as vectors |
| `frontend/src/components/queue/AlertTable.tsx` | 378 | The virtualised, risk-ordered queue table with bulk selection |
| `frontend/src/components/queue/QueueFilters.tsx` | 211 | The filter row and the one-click anomaly chip |
| `frontend/src/components/queue/QueueStatStrip.tsx` | 87 | The four figures above the queue |
| `frontend/src/components/alert/AlertDetailDrawer.tsx` | 651 | Why / what-it-is / how-to-fix, the two models' verdicts, host context, the raw flow |
| `frontend/src/components/alert/AlertGlyphs.tsx` | 93 | The marks an alert wears: class badge, severity mark, status pill |
| `frontend/src/components/alert/ExplanationChart.tsx` | 170 | Signed TreeSHAP bars, or unsigned reconstruction-error shares |
| `frontend/src/components/live/ThresholdHistogram.tsx` | 494 | The anomaly-score histogram with a draggable, keyboard-operable threshold |
| `frontend/src/components/live/FlowTicker.tsx` | 231 | The live alert ticker and the two rate series |
| `frontend/src/components/live/ReplayControls.tsx` | 138 | Dataset, speed, start and stop |
| `frontend/src/components/live/CaptureControls.tsx` | 192 | Phase 9: shadow burn-in and alerting, with both Stage 2 thresholds |
| `frontend/src/components/model/Panels.tsx` | 339 | Per-class table, confusion matrix, PR and ROC with their caption |
| `frontend/src/components/model/LoaoPanel.tsx` | 380 | The leave-one-attack-out table |
| `frontend/src/components/drift/Panels.tsx` | 520 | Retrain banner, snapshot summary, ranked PSI, trend, baseline overlay |
| `frontend/src/components/analytics/Panels.tsx` | 376 | Alerts over time, family mix, ranked tables, MITRE heatmap, throughput |
| `frontend/src/components/charts/Chart.tsx` | 213 | The shared chart furniture: frame, legend, tooltip, scale key |
| `frontend/src/components/ui/badge.tsx` | 51 | Bordered pills, including the `novel` variant |
| `frontend/src/components/ui/button.tsx` | 56 | Buttons; at most one primary per panel |
| `frontend/src/components/ui/card.tsx` | 79 | The matte panel everything sits in |
| `frontend/src/components/ui/checkbox.tsx` | 39 | A native checkbox with a real indeterminate state |
| `frontend/src/components/ui/drawer.tsx` | 103 | The right-hand drawer, with focus management |
| `frontend/src/components/ui/segmented.tsx` | 55 | A segmented control of pressed buttons |
| `frontend/src/components/ui/select.tsx` | 50 | A styled native `<select>` |
| `frontend/src/components/ui/skeleton.tsx` | 15 | Loading placeholder |
| `frontend/src/lib/env.ts` | 58 | Typed, defaulted access to the `VITE_*` environment |
| `frontend/src/lib/format.ts` | 130 | Timestamps (offset-less read as UTC), durations, counts, percentages |
| `frontend/src/lib/alerts.ts` | 138 | The display vocabulary of an alert, keyed by the generated types |
| `frontend/src/lib/hooks.ts` | 48 | `useDebounced` and `useElementWidth` |
| `frontend/src/lib/theme.ts` | 34 | Light/dark, persisted per viewer |
| `frontend/src/lib/utils.ts` | 7 | `cn`, the class-name merge |
| `frontend/src/types/api.d.ts` | 3,273 | **Generated** from the OpenAPI snapshot; never hand-edited |
| `frontend/src/vite-env.d.ts` | 18 | Types for the `VITE_*` variables |
| `frontend/src/test/setup.ts` | 22 | jest-dom matchers and per-test cleanup |
| `frontend/src/test/harness.tsx` | 182 | A routed app over a stubbed API surface |
| `frontend/src/test/fixtures.ts` | 674 | Typed fixture bodies for every endpoint the screens read |
| `frontend/src/pages/SystemHealth.test.tsx` | 148 | 6 tests: the health contract, stubbed and live |
| `frontend/src/pages/screens.test.tsx` | 578 | 48 tests: the seven screens' acceptance criteria |
| `frontend/src/types/contract.test.ts` | 28 | 1 test: `api.d.ts` is what the committed snapshot generates |
| `frontend/scripts/generate-types.mjs` | 62 | Writes `api.d.ts` from the running backend or a schema file |
| `frontend/vite.config.ts` | 75 | Dev server and proxy, alias, build chunks, Vitest |
| `frontend/tsconfig.json` | 29 | Compiler options for `src/` |
| `frontend/tsconfig.node.json` | 16 | Compiler options for the Node-side config |
| `frontend/package.json` | 50 | Scripts and dependencies |
| `frontend/nginx.conf` | 37 | The `serve` stage's server block: SPA fallback, asset caching, the SSE-safe `/api` proxy |

---

## Entry points

### index.html

The HTML shell. `<html lang="en" class="dark">` ships the dark theme in the markup itself, so the console never flashes white before the script runs; `<meta name="color-scheme" content="dark light">` and `theme-color` match it. The favicons are the Recluse emblem. `<div id="root">` is the mount point and `/src/main.tsx` the module entry.

### main.tsx

Calls `applyTheme(storedTheme())` *before* the first render — restoring a viewer who chose the light theme last time without a flash of the dark one — then mounts `<App />` inside `StrictMode`, failing loudly if `#root` is missing. The global stylesheet is imported here.

### App.tsx

The provider stack and the routes, in that nesting: `QueryClientProvider` (a client created in state, so each app instance — and each test — has its own) → `BrowserRouter` → `StreamProvider` (the one SSE connection) → `AppShell` → the routes.

| Path | Screen |
| --- | --- |
| `/` | `TriageQueue` — the landing page. Analysts live in the queue, so the queue is home, not an overview dashboard they click past a few hundred times a shift |
| `/live` | `LiveMonitor` |
| `/model` | `ModelPerformance` |
| `/drift` | `DriftMonitor` |
| `/feedback` | `FeedbackLoopScreen` |
| `/analytics` | `Analytics` |
| `/system` | `SystemHealth` |
| `*` | Redirects to `/` |

Each screen is wrapped in its own `ErrorBoundary`, *inside* the shell rather than around it: a render error on the drift screen must not take the navigation with it, because being able to click away from a broken panel is the difference between a bug and an outage. The alert detail is a drawer opened by `?alert=<id>` on the queue rather than a route, so the queue behind it — and the analyst's place in it — is never lost.

---

## API layer

Every byte of server state reaches a component through `src/api/`. Components never call `fetch`.

### api/client.ts

`frontend/src/api/client.ts` — the single `fetch` wrapper every request goes through, plus the typed error it raises.

There is exactly one function in the application that calls `fetch` against the backend: `request<T>`. Centralising it means base-URL resolution, the `Accept` header, JSON parsing, `204` handling and error shaping are decided once. A component that wants data calls a hook in [api/queries.ts](#apiqueriests), which calls `request`, which calls `fetch`.

Base URL resolution is a plain string concatenation: `` `${env.apiBaseUrl}${path}` ``, where `env.apiBaseUrl` defaults to `/api/v1` (see [lib/env.ts](#libenvts)). That default is a *relative* URL on purpose. In development the Vite dev server proxies `/api` to the backend, so the browser stays same-origin and never needs CORS; in production the same relative path is served through the reverse proxy in front of the container. Neither case requires rebuilding the bundle with a different origin baked in. Setting `VITE_API_BASE_URL` to an absolute URL is supported for the case where the API genuinely lives elsewhere.

Error handling is where the file earns its length. A non-2xx response is read for a body, a human-readable message is extracted from FastAPI's `detail` field when present, and an `ApiError` is thrown carrying the status, the URL, the message and the raw body. The `isNotImplemented` getter singles out `501`, which the backend answered for a route a later phase would fill in. No route answers it since Phase 9 (see [API-Reference](API-Reference.md#the-501-contract)); the getter stays because it is what lets the UI say "not built yet" rather than show a generic failure should a stub return, and it is what [api/queryClient.ts](#apiqueryclientts) keys its retry policy off.

```ts
export async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const url = `${env.apiBaseUrl}${path}`

  const response = await fetch(url, {
    ...init,
    headers: { Accept: 'application/json', ...init?.headers },
  })

  if (!response.ok) {
    const body = await readBody(response)
    throw new ApiError(response.status, url, messageFrom(body, response), body)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}
```

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `ApiError` | class | `class ApiError extends Error` | A non-2xx response, carrying enough context to render something useful |
| `ApiError.constructor` | constructor | `constructor(readonly status: number, readonly url: string, message: string, readonly body?: unknown)` | Captures status, request URL, message and parsed body; sets `name` to `'ApiError'` |
| `ApiError.isNotImplemented` | getter | `get isNotImplemented(): boolean` | `true` when `status === 501` — an endpoint registered ahead of its phase |
| `readBody` | function (module-private) | `async function readBody(response: Response): Promise<unknown>` | Parses JSON when `content-type` includes `application/json`, otherwise text; returns `undefined` on a parse failure |
| `messageFrom` | function (module-private) | `function messageFrom(body: unknown, response: Response): string` | Uses the body's string `detail` when present, else `` `${status} ${statusText}` `` |
| `request` | function | `export async function request<T>(path: string, init?: RequestInit): Promise<T>` | Fetch JSON from the API; `path` is relative to `env.apiBaseUrl` |

- `Accept: application/json` is set first and spread-overridable, so a caller can override it but does not have to repeat it.
- A `204 No Content` resolves to `undefined` cast to `T` rather than attempting `response.json()`, which would throw on an empty body.
- `readBody` swallows parse errors deliberately: a malformed error body must not mask the status code the caller actually needs.
- The generic `T` is an unchecked cast. Type safety comes from the generated schema types in [api/types.ts](#apitypests), not from runtime validation.

### api/queries.ts

`frontend/src/api/queries.ts` — every TanStack Query hook the screens use, and the one place query keys are defined.

`queryKeys` is structured by prefix, and the prefixes are the point: `['alerts', ...]` is shared by the queue, the detail, the related list and the stat strip, so a verdict invalidates all four with one `invalidateQueries({ queryKey: queryKeys.alerts })` instead of four calls that can drift apart. A rename is a compile error, never a cache that silently stops refreshing.

| Hook | Endpoint | Policy |
| --- | --- | --- |
| `useHealth` | `GET /health` | Polls every `VITE_HEALTH_POLL_MS`, `staleTime: 0` — `uptime_s` advancing between polls is the proof the process is live |
| `useAlerts(filters)` | `GET /alerts` | `useInfiniteQuery` over the server's keyset cursor, which is what keeps paging stable while a replay inserts higher-risk rows between requests |
| `useQueueStats` | `GET /alerts/stats` | Polls every `VITE_STATS_POLL_MS` |
| `useAlert(id)` / `useRelatedAlerts(id)` | `GET /alerts/{id}`, `GET /alerts/{id}/related` | Enabled only while the drawer has an id |
| `useSubmitVerdict(id)` | `POST /alerts/{id}/verdict` | Invalidates the `alerts` and `analytics` prefixes |
| `useUpdateAlertStatus` | `PATCH /alerts/status` | The bulk dismiss; same invalidations |
| `useModelMetrics`, `useAnomalyHistogram` | `GET /metrics/model`, `GET /metrics/anomaly-histogram` | `staleTime: Infinity` — the evaluation artifacts do not change while the process runs |
| `useThresholdProjection(t)` | `GET /metrics/threshold?t=` | Called with the *debounced* slider value; `placeholderData` keeps the previous projection on screen while the next loads |
| `useDrift`, `useModelRegistry` | `GET /metrics/drift`, `GET /models` | A minute's staleness: both change nightly at most |
| `useRetrainRuns` / `useRequestRetrain` | `GET` / `POST /retrain` | The history polls; a request invalidates it and the feedback counts |
| `useAnalytics(range)`, `useMitreCoverage`, `useFeedbackLoop` | `GET /analytics/*` | Poll every `VITE_STATS_POLL_MS` |
| `useReplayStatus`, `useReplayDatasets`, `useReplayControl` | `GET /replay/status`, `GET /replay/datasets`, `POST /replay/start\|stop` | Status polls every `VITE_REPLAY_POLL_MS`; start and stop invalidate it and the alerts |
| `useIngestStatus`, `useIngestControl` | `GET /ingest/status`, `POST /ingest/start\|stop` | The same shape as replay, for live capture (Phase 9) |

- The path passed to `request` never carries `/api/v1`; that prefix comes from `env.apiBaseUrl`.
- `refetchInterval` polls only while a query is mounted, so a screen nobody is looking at costs nothing.

### api/queryClient.ts

`frontend/src/api/queryClient.ts` — factory producing the configured `QueryClient` for an application instance.

The retry policy is the substance of this file. TanStack Query retries failed queries three times by default with exponential backoff, which is right for a flaky network and wrong for two cases this project hits constantly.

A `501` would mean a route's phase has not been built. Retrying it cannot change the answer and only delays the message the UI wants to show, so `isNotImplemented` short-circuits to no retries. Any other 4xx is likewise the caller's fault and not worth repeating. Everything else — 5xx and network failures — retries up to twice. The predicate is written against `ApiError` specifically: a thrown value that is not an `ApiError` — a network-level `TypeError` from `fetch`, for instance — matches neither guard and falls through to `failureCount < 2`, so genuine connectivity failures are retried like a 5xx. That fall-through, together with the retried 5xx, is why the error-state case in `SystemHealth.test.tsx` allows ten seconds for the failure message to appear.

`refetchOnWindowFocus: true` is kept on. An analyst who tabs back to a monitoring console expects what they see to be current. `staleTime: 2_000` sets a two-second floor on refetching so a burst of remounts does not turn into a request storm; `useHealth` overrides it to `0`.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `createQueryClient` | function | `export function createQueryClient(): QueryClient` | Builds a `QueryClient` whose query defaults carry the retry predicate, focus refetching and a 2 s stale time |
| retry predicate | option | `retry: (failureCount: number, error: Error) => boolean` | `false` for `ApiError` with `isNotImplemented`; `false` for any `ApiError` with `status < 500`; otherwise `failureCount < 2` |

- Exported as a factory, not a singleton, so `App` can hold one per instance and tests stay isolated.
- No mutation defaults are configured: each mutation hook in [api/queries.ts](#apiqueriests) names the queries it invalidates, which is where that decision belongs.

### api/stream.tsx

`frontend/src/api/stream.tsx` — the live alert feed, as one `EventSource` for the whole application.

A subscription rather than a query, which is why it does not live in `queries.ts`, and a provider rather than a hook, so there is exactly one connection however many components read from it — two components each calling `new EventSource` would open two streams, and the backend would fan every alert out twice.

The connection opens only while a traffic source is running. `GET /stream` answers 503 when none is, and `EventSource` reacts to a 503 by reconnecting forever on a short timer; so the provider first asks `/replay/status` and `/ingest/status` (plain fetches, so the effect does not re-run on every poll) and connects once either reports `running`. Frames update state bounded by `VITE_TICKER_ROWS`, a per-second `RateSample` series of alerts and flows (sixty seconds of it), and the last-frame time, where a heartbeat counts as proof of life. Arrivals refresh the queue at most every 1.5 seconds rather than per event: an `alert` frame is narrower than a queue row, so the honest update is to patch what the frame carries and let a coalesced refetch bring in the new rows. `following` lets the analyst pause the table without pausing the feed.

| Symbol | Kind | Description |
| --- | --- | --- |
| `StreamProvider` | component | Owns the `EventSource` and the state above |
| `useAlertStream` | hook | Reads it; throws outside the provider |
| `RateSample` | interface | `{ second, alerts, flows }` — one second of the rate series |

### api/types.ts

`frontend/src/api/types.ts` — concrete, named aliases over the generated OpenAPI type tree, and the only hand-written file that imports `@/types/api`.

Components import `AlertDetail` from here, never `components['schemas']['AlertDetail']`. The indirection keeps [types/api.d.ts](#typesapidts) an implementation detail: a renamed schema breaks one line here plus the call sites, not a scatter of bracket-indexed lookups. The aliases cover the alert domain (`AlertSummary`, `AlertDetail`, `AlertPage`, `AlertExplanation`, `Contributor`, `RecommendedActions`, `Technique`, `QueueStats`, the verdict and bulk-status bodies), the stream and the traffic sources (`AlertEvent`, `HeartbeatEvent`, `ReplayStatus`, `ReplayDataset`, `IngestStatus`, `IngestStartRequest`, `LocalCalibration`), the metrics (`ModelMetrics`, `ClassMetrics`, `CurvePair`, `ThresholdProjection`, `AnomalyHistogram`, `ErrorDistribution`), analytics and feedback, and drift, the registry and retraining. Vocabulary unions — `AlertKind`, `AlertFamily`, `Severity`, `AlertStatus`, `DetectionStage`, `Verdict`, `DriftBand`, `ModelStage`, `RetrainStatus` — are derived from the schemas rather than restated, so they cannot drift. `AnalyticsRange` is the one hand-written union, because the range is a query parameter rather than a schema.

---

## Screens — `src/pages/`

Each screen reads its own hooks and composes panels from `src/components/`; none fetches directly. Every one renders loading, empty and error states rather than a blank area.

- **`TriageQueue`** (`/`) — the queue is the landing page. A filter row with the one-click unclassified-anomaly chip, the four-figure stat strip, and the virtualised table sorted by risk on the server; selecting rows enables the bulk dismiss. Opening a row sets `?alert=<id>` and slides in the detail drawer over the queue.
- **`LiveMonitor`** (`/live`) — the draggable threshold histogram leads, because it is the one element that teaches rather than reports: drag the line and the projected alerts per analyst-hour move against the budget. Below it, the replay controls and, from Phase 9, the capture controls, then the live ticker and the two rate series.
- **`ModelPerformance`** (`/model`) — the leave-one-attack-out panel leads, a deliberate inversion: per-class tables describe traffic the model was trained for, and only LOAO speaks to the claim the project makes. Then the per-class table, the confusion matrix, and PR beside ROC with the caption explaining why they disagree. Accuracy appears only as a caveated footnote.
- **`DriftMonitor`** (`/drift`) — the retrain banner (any single feature past 0.25), the latest snapshot's summary, PSI ranked worst first with the band as a word, a trend for a readable handful of features, the observed score distribution over the training baseline, and the model registry as the audit trail.
- **`FeedbackLoopScreen`** (`/feedback`) — labels since the last retrain, the disagreement rate with `UNSURE` outside it, the guards on the benign refit pool, the retrain request — which queues a run, never performs one — and the run history including the challengers that were declined.
- **`Analytics`** (`/analytics`) — one range control scoping every panel: alerts over time split known from unclassified, the family mix, ranked hosts, ports and sources, SOC throughput, and the MITRE heatmap, whose never-fired techniques are visible zeros. It leads on the unclassified rate, not an accuracy percentage.
- **`SystemHealth`** (`/system`) — the live health panel, how the Stage 1 threshold was derived from the false-positive budget, the phase list with what each produced (Phase 9 is *partial*: built and run, its attack exercise not), and the things the system never does.

---

## Components — `src/components/`

### The shell

`AppShell` is the sidebar and the work surface. The sidebar carries, besides navigation, how much work waits on each screen (open alerts, labels pending a retrain, a drift warning) and what the two-stage pipeline is running — both read from the API, and a source pill that says whether a replay or a live capture is feeding the stream. `PageHeader` and `ScreenBody` give every screen the same frame. `ErrorBoundary` names the screen that failed in its fallback. `States` holds `LoadingRows`, `EmptyState` (which says what is absent and what would fill it) and `ErrorState`. `StatTile` is a metric tile whose sparkline is drawn only from a series the API returned — a decorative squiggle beside a number reads as a trend nobody measured. `HealthPanel` renders `/health` on the System screen, with no accuracy tile. `RecluseLogo` is the emblem, hidden from assistive technology wherever the word "Recluse" sits beside it.

### Queue — `queue/`

`AlertTable` is virtualised with TanStack Virtual, because a 100x replay puts tens of thousands of rows in it; rows arrive sorted by risk from the server and are never re-sorted by time. An `UNCLASSIFIED_ANOMALY` row carries its own channel, glyph and words, so it never reads as just another severe known attack. The header checkbox has a true indeterminate state for a partial selection. `QueueFilters` builds the filter object the queue sends up; the anomaly chip is a toggle apart from the class select, because it answers "what does this catch that a signature IDS doesn't" and must be one click. `QueueStatStrip` is exactly four figures: alerts in the last hour, projected alerts per analyst-hour, the threshold that produces them, and the hosts involved — no accuracy.

### Alert detail — `alert/`

`AlertDetailDrawer` answers why, what-it-is and how-to-fix, in that order: the English narrative above the explanation chart, the technique with its plain-English line (or, for an anomaly, an honest "no known technique" rather than an invented one), and the reviewed playbook as a checklist (or "no playbook yet — escalate"). Beside them, both models' verdicts against the thresholds they are held to — for a live alert, the local threshold from its own provenance and the error as a multiple of it — the host's related alerts, the raw flow with its provenance caveat, the demo-only ground-truth tally on replayed rows, and the verdict footer. There is no containment action, and a test says so. `ExplanationChart` draws one chart for both explainers: signed TreeSHAP contributions for Stage 1, unsigned reconstruction-error shares for Stage 2. `AlertGlyphs` keeps the class badge, severity mark and status pill in one place, so the queue, the drawer and the ticker cannot drift into dialects.

### Live traffic — `live/`

`ThresholdHistogram` draws the persisted Stage 2 error bins on their log axis with a threshold line that is a real slider (pointer and keyboard, `role="slider"`); the line follows the pointer from local state while only the debounced value goes to `/metrics/threshold`. `FlowTicker` is the live alert feed plus flows-per-second and alerts-per-second as two charts rather than one with two axes, which would flatten the alert series into the floor. `ReplayControls` lists every dataset `/replay/datasets` names and disables the ones that cannot run here, and restarts on a speed change because the engine fixes its pacing when a run begins. `CaptureControls` (Phase 9) offers only the interfaces and pcaps the server lists, shows both Stage 2 thresholds side by side, and enables *Start alerting* only once a local calibration exists.

### Model, drift and analytics panels

`model/Panels.tsx` holds the per-class table, the confusion matrix and the PR/ROC pair; `model/LoaoPanel.tsx` the leave-one-attack-out table with its Missed and named-correctly columns. `drift/Panels.tsx` holds the retrain banner, the snapshot summary, ranked PSI, the trend and the baseline overlay — and not "one line per feature", because no palette separates ninety-two series. `analytics/Panels.tsx` holds the analytics panels, kept off the Model screen on purpose: measured-offline and seen-in-deployment are different claims.

### Charts — `charts/Chart.tsx`

The shared chart furniture — `ChartFrame`, `ChartLegend`, `TooltipCard`, `TooltipRow`, `ScaleKey`, `heatFill`, and the grid and axis props — built once so every chart reads as one system. Series colours come from the `--series-*` tokens, which were chosen as pairs that stay apart under simulated colour-vision deficiency.

### UI primitives — `ui/`

`badge`, `button`, `card`, `checkbox`, `drawer`, `segmented`, `select` and `skeleton`, generated from shadcn conventions and restyled to the matte console. `Badge` has a `novel` variant of its own; `Button`'s primary is the crimson brand, at most one per panel, and no button anywhere changes traffic. `Select` is a styled *native* select, because the filter row is used dozens of times a shift and the platform widget already has keyboard navigation and type-ahead. `Drawer` moves focus in on open and restores it on close. `Segmented` is a group of `aria-pressed` buttons, each segment being an action as much as a state.

---

## Library — `src/lib/`

### lib/env.ts

Typed, defaulted access to the Vite environment — every value has a default here and nowhere else, so no component contains a literal port, path or interval. `positiveInt` guards each numeric parse, because `Number(undefined)` is `NaN` and `Number('')` is `0`, and either would produce a broken or runaway poll.

| Value | Variable | Default | For |
| --- | --- | --- | --- |
| `apiBaseUrl` | `VITE_API_BASE_URL` | `/api/v1` | The request prefix; relative so the dev proxy and the production proxy both work without a rebuild |
| `healthPollMs` | `VITE_HEALTH_POLL_MS` | 5000 | The health poll |
| `statsPollMs` | `VITE_STATS_POLL_MS` | 10000 | The aggregate panels' poll |
| `replayPollMs` | `VITE_REPLAY_POLL_MS` | 2000 | The replay and capture status polls — fast, because they back a control the analyst just clicked |
| `thresholdDebounceMs` | `VITE_THRESHOLD_DEBOUNCE_MS` | 150 | The slider's projection debounce: about six requests over a two-second drag instead of a hundred |
| `tickerRows` | `VITE_TICKER_ROWS` | 60 | The live feed's bound |
| `queuePageSize` | `VITE_QUEUE_PAGE_SIZE` | 100 | The queue's page size |

`VITE_DEV_SERVER_HOST`, `VITE_DEV_SERVER_PORT` and `VITE_DEV_PROXY_TARGET` are read only by `vite.config.ts` (and the last by the type generator and the live test). Vite inlines `import.meta.env.VITE_*` at build time, so changing one needs a restart or a rebuild, and only `VITE_`-prefixed variables reach the bundle — nothing secret belongs in one.

### lib/format.ts, lib/alerts.ts, lib/hooks.ts, lib/theme.ts, lib/utils.ts

- `format.ts` renders every timestamp, duration, count and percentage, once. `parseTimestamp` reads an offset-less API timestamp as UTC: SQLite hands timezone-aware values back naive, and `new Date()` would read them as local time and shift every alert by the viewer's offset.
- `alerts.ts` is the display vocabulary of an alert — labels, badge variants, colours and orderings for severity, family, status, verdict and stage — with every map keyed by the generated types, so an eighth attack family in `app/models.py` is a compile error here rather than an unlabelled badge.
- `hooks.ts` has `useDebounced` (the slider's settled value) and `useElementWidth` (for the charts that compute their own geometry, which CSS cannot lay out).
- `theme.ts` persists light or dark per viewer under `recluse.theme`; dark is the default, and a private window that blocks storage falls back to dark rather than failing to render.
- `utils.ts` is `cn`: `clsx` flattens conditional class inputs and `tailwind-merge` lets a later utility win, which is what makes a caller's `className` override reliable.

---

## Styling — `src/index.css`

Tailwind v4 is configured in CSS, so there is no `tailwind.config.js`. The file imports Tailwind and the two bundled variable fonts (Inter, and JetBrains Mono for every raw value), declares the `dark` variant against the `.dark` class, and defines the palette twice — `:root` for light and `.dark` for the default dark — as hex tokens published to Tailwind through `@theme inline`.

The palette is a matte SOC console in which each accent means one thing: **crimson** for the brand and for attack (known families, critical severity, the primary action — never decoration); **ochre** for Stage 2, `UNCLASSIFIED_ANOMALY` and anything nobody named, its own channel because it is what this project catches that a signature IDS does not; **green** for resolved, benign and inside-budget; **slate** for the baseline that should recede. Severity steps crimson → orange → sand → slate, so `medium` is never mistaken for an anomaly's ochre — they differ in lightness and chroma as well as hue, and the anomaly always carries its glyph and its word too. Surfaces come in three steps (page, card, inset well), with hairline borders one shade off the surface. The `--series-*` tokens are stepped separately from the badge channels and validated as pairs under simulated colour-vision deficiency: known against unclassified separates on lightness as well as hue, and benign against attack is slate-blue against crimson, which stays apart under deuteranopia where red against green would not. The base layer adds the `.tabular` utility (fixed-width digits for numbers that update in place) and collapses animation under `prefers-reduced-motion`.

---

## types/api.d.ts

**Generated** — 3,273 lines, never hand-edited. `make openapi` writes the backend's contract snapshot (`backend/tests/snapshots/openapi.json`) and regenerates this file from it; `npm run gen:types` regenerates it from a running backend instead. `src/types/contract.test.ts` fails if the committed file is not exactly what the snapshot generates, and the backend's `test_api_contract.py` fails if the snapshot is not what the app serves — so a wire change cannot land without both moving. It is not documentation: [API-Reference](API-Reference.md) is the readable description of the same surface.

---

## Tests

- `src/test/setup.ts` registers the jest-dom matchers and runs `cleanup` after every test.
- `src/test/harness.tsx` renders the real `App` at a route over a stubbed `fetch` that answers the whole API surface from `src/test/fixtures.ts` — one stub for every endpoint rather than a mock per test, because a screen reads several endpoints and a test that stubs only the one it cares about would pass while the screen errors everywhere else. Tests override single routes and read back every request made, with its body.
- `src/test/fixtures.ts` holds typed fixture bodies for every endpoint, shaped like the real responses.
- The three test files and what they pin are on [Testing](Testing.md#frontend).

---

## Build and tooling

### scripts/generate-types.mjs

Writes `src/types/api.d.ts` with `openapi-typescript`, prefixed by a do-not-edit banner. By default it fetches `/openapi.json` from the backend named by `VITE_DEV_PROXY_TARGET` in the repo-root `.env` (falling back to `http://127.0.0.1:8000`); with `--from <file>` it reads a schema file instead, which is how `make openapi` generates from the committed snapshot without a running server. A failure prints the source, a hint and the error and sets a non-zero exit code; the file is written only on success, so a failed run leaves the committed types intact. An `.mjs` file, so it runs under `node` with no build step — and, without `allowJs`, is not type-checked.

### vite.config.ts

`frontend/vite.config.ts` — dev server, proxy, path alias, build output and Vitest configuration in one exported factory.

The config is a function of `{ mode }` so it can call `loadEnv(mode, repoRoot, '')` before returning. `envDir` is then pointed at the repository root, which is the important decision in this file: there is one `.env` for the whole stack rather than two that drift apart. The frontend port, bind host and proxy target come from the same file the backend reads.

Three server settings carry reasoning. `strictPort: true` makes a busy port a hard failure instead of silently moving to another one, which would leave the documented `http://localhost:5173` pointing at nothing. The default host is `::` (dual-stack) because on Windows Node otherwise resolves the default host to `::1` only, so `127.0.0.1` refuses connections and produces a confusing "works in the browser, refused by curl"; containers override this with `0.0.0.0`. The `/api` proxy with `changeOrigin: true` keeps the browser same-origin in development, so CORS is never needed.

The `@` alias resolves to `./src`, matching the `paths` entry in [tsconfig.json](#tsconfigjson) — both must be kept in step, since Vite resolves at runtime and TypeScript only type-checks. Vitest configuration lives in the same file under `test`: jsdom environment, global test APIs, the setup file, and an include glob restricted to `src/**/*.{test,spec}.{ts,tsx}`.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| default export | config factory | `export default defineConfig(({ mode }) => ({ ... }))` | Mode-aware configuration |
| `here` | const | `const here: string` | This directory, used to build an absolute setup-file path |
| `repoRoot` | const | `const repoRoot: string` | Repository root, used for `loadEnv` and `envDir` |
| `devPort` | const | `const devPort: number` | `Number(env.VITE_DEV_SERVER_PORT \|\| 5173)` |
| `proxyTarget` | const | `const proxyTarget: string` | `env.VITE_DEV_PROXY_TARGET \|\| 'http://127.0.0.1:8000'` |
| `devHost` | const | `const devHost: string` | `env.VITE_DEV_SERVER_HOST \|\| '::'` — dual-stack by default |
| `envDir` | option | `envDir: repoRoot` | One `.env` for the whole stack |
| `plugins` | option | `[react(), tailwindcss()]` | React Fast Refresh and the Tailwind v4 Vite plugin |
| `resolve.alias` | option | `{ '@': <frontend>/src }` | Mirrors the `@/*` TypeScript path alias |
| `server` | option | `{ host, port, strictPort: true, proxy: { '/api': { target, changeOrigin: true } } }` | Dev server and API proxy |
| `preview` | option | `{ host: devHost, port: devPort, strictPort: true }` | `vite preview` matches the dev server |
| `build` | option | `{ outDir: 'dist', sourcemap: mode !== 'production', rollupOptions: { output: { manualChunks } } }` | Source maps everywhere except production builds; dependencies split into a `charts` chunk (recharts and the d3 modules) and one `vendor` chunk |
| `test` | option | ``{ environment: 'jsdom', globals: true, setupFiles: [`${here}src/test/setup.ts`], include: ['src/**/*.{test,spec}.{ts,tsx}'] }`` | Vitest configuration |

- The `/// <reference types="vitest/config" />` directive at the top is what makes the `test` key type-check inside `defineConfig`.
- `globals: true` is why test files can rely on ambient `describe`/`it`/`expect`, and why `"vitest/globals"` appears in the `types` array of `tsconfig.json` — though the test file imports them explicitly anyway.
- Changing the `@` alias requires the matching change in `tsconfig.json`; nothing enforces that automatically.
- `here` comes from `fileURLToPath(new URL('.', import.meta.url))`, which resolves to this directory **with a trailing separator** — which is why `` `${here}src/test/setup.ts` `` has no slash of its own. The path is absolute, so Vitest resolves the setup file regardless of the working directory the run starts from.
- `build.outDir` is `dist/`, which is gitignored. It is produced by `npm run build` and consumed by the `serve` stage of `frontend/Dockerfile`; nothing in `src/` reads it.
- Two vendor chunks, not three: the charting stack is the largest dependency and the likeliest to stay cached across a deploy, so it gets its own; splitting further produced circular chunks, because recharts and the TanStack packages both import React.

### tsconfig.json

`frontend/tsconfig.json` — compiler options for application code under `src/`.

Strict mode is on, plus four extra checks beyond it: `noUnusedLocals`, `noUnusedParameters`, `noFallthroughCasesInSwitch` and `noUncheckedSideEffectImports`. `verbatimModuleSyntax` forces `import type` to be written explicitly, which keeps type-only imports out of the emitted graph and makes the distinction visible when reading a file.

`noEmit` is true because Vite does the transpiling; TypeScript is used purely as a checker, run through `npm run typecheck`. The `types` array is pinned to `["vite/client", "vitest/globals", "@testing-library/jest-dom"]` rather than left to automatic `@types` discovery, so a transitively installed type package cannot quietly change what is in scope.

`paths` maps `@/*` to `src/*`, mirroring the Vite alias, and `include` is `["src"]` — the config files are covered by `tsconfig.node.json` instead. The target is `ES2022`.

- `exactOptionalPropertyTypes: false` is a deliberate concession to the generated schema types, which model optional fields as `field?: T` rather than `field?: T \| undefined`.
- `npm run typecheck` runs this config and the Node one in sequence, so both must pass before `npm run build` proceeds.

### tsconfig.node.json

`frontend/tsconfig.node.json` — compiler options for the Node-side files that configure and build the app.

`vite.config.ts` and `scripts/*.mjs` run in Node, not the browser: they use `node:fs/promises`, `node:path` and `node:url`, and they have no DOM. Checking them under the application config would require pulling Node types into browser code, so they get their own project with `"types": ["node"]`, an `ES2023` target and no DOM lib.

It keeps the same strictness — `strict`, `noUnusedLocals`, `noUnusedParameters`, `isolatedModules` — so a config file is not a place where type discipline quietly relaxes.

- The `scripts/**/*.mjs` entry in `include` is currently inert. `allowJs` is not set, so TypeScript drops `.mjs` files from the program rather than checking them — `tsc -p tsconfig.node.json --noEmit --listFiles` lists only `vite.config.ts`. `generate-types.mjs` is therefore unchecked; adding `"allowJs": true` (and `checkJs`) would be needed to check it.

### package.json

An ESM package (`"type": "module"`), private. The scripts are the frontend's whole task surface, called by the repo-root `Makefile` and `make.ps1`.

| Script | Command | For |
| --- | --- | --- |
| `dev` | `vite` | Dev server with hot reload, on the port and host from the repo-root `.env` |
| `build` | `npm run typecheck && vite build` | Production bundle into `dist/`, type-checked first |
| `preview` | `vite preview` | Serves the built `dist/` |
| `typecheck` | `tsc -p tsconfig.json --noEmit && tsc -p tsconfig.node.json --noEmit` | App code and Node-side config as two projects |
| `test` | `vitest run` | Single run; what `make test-frontend` invokes |
| `test:watch` | `vitest` | Watch mode |
| `gen:types` | `node scripts/generate-types.mjs` | Regenerates `src/types/api.d.ts` |

Dependencies, by purpose (versions live in `package.json`, where they cannot go stale here):

- **Framework and routing** — `react`, `react-dom`, `react-router-dom`
- **Server state** — `@tanstack/react-query`, the only fetching mechanism permitted
- **The queue and the charts** — `@tanstack/react-table` and `@tanstack/react-virtual` for the queue, `recharts` for the charts
- **Styling** — `tailwindcss` with its Vite plugin, `class-variance-authority`, `clsx`, `tailwind-merge`, `lucide-react`, `@radix-ui/react-slot`, and the bundled fonts `@fontsource-variable/inter` and `@fontsource-variable/jetbrains-mono`
- **Build and test** — `vite`, `@vitejs/plugin-react`, `typescript`, `vitest`, `jsdom`, `@testing-library/react`, `@testing-library/jest-dom`
- **Codegen** — `openapi-typescript`

`package-lock.json` is committed and is what the container build installs from: `npm ci` fails rather than resolving a new tree if the lockfile disagrees with the manifest.

### components.json

`frontend/components.json` — configuration for the shadcn/ui generator, so added components land in the right place with the right imports.

This file is read by the shadcn CLI, not by the application. It records the conventions the UI primitives under `src/components/ui/` follow, so a component added later is generated consistently rather than pasted in and hand-adjusted: the "new-york" style, TSX output, CSS variables for theming, and Lucide as the icon library.

`"tailwind": { "config": "" }` is the notable entry. Tailwind v4 has no JavaScript config file — the theme lives in [index.css](#styling--srcindexcss), which is what the `css` key points at — so the field is intentionally empty rather than missing.

The `aliases` block mirrors the `@/*` path mapping shared by `tsconfig.json` and `vite.config.ts`, so generated files import `cn` from `@/lib/utils` and siblings from `@/components/ui` with no editing.

- `@/hooks` is declared but no `src/hooks/` directory exists: the two shared hooks live in `src/lib/hooks.ts`.
- Generated components are checked into the repository and edited freely — `badge.tsx` carries the project-specific `novel` variant, and every primitive was restyled to the console's matte palette.
- Status: implemented (configuration only; it has no runtime effect).

### Dockerfile and nginx.conf

`frontend/Dockerfile` has four stages: `deps` (`npm ci` from the lockfile), `dev` (the Vite dev server, for anyone who wants hot reload inside a container), `build` (`npm run build`), and `serve` — `dist/` in `nginx:1.29-alpine` with `nginx.conf`. **`docker compose up` builds `serve`** (Phase 8), published on `FRONTEND_PORT` (default 5173) with a health check, so the containerised dashboard is the production bundle rather than a dev server.

`nginx.conf` is one `server` block. Unknown paths fall back to `index.html`, which is served `no-cache` so a rebuilt image is picked up on the next load; everything under `/assets/` is fingerprinted by Vite and cached for a year as `immutable`. `/api/` proxies to `http://backend:8000` over HTTP/1.1 with the upstream connection kept open, buffering and caching off, and a 24-hour read timeout — the alert feed is server-sent events, and a buffering proxy would hold the stream. The full container story is on [Code-Infrastructure](Code-Infrastructure.md).
