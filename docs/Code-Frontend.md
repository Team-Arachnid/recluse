# Code Reference — Frontend

This page documents every file in `frontend/` as it exists at the end of Phase 0: the Vite + React + TypeScript dashboard, its typed API client, the single screen that ships today, the shadcn-style UI primitives, and the build and tooling configuration.

Phase 0 ships one screen. Of the seven dashboard screens — Triage Queue, Alert Detail, Live Traffic Monitor, Model Performance, Drift Monitor, Feedback Loop and Analytics — **none exist yet**. What exists is `SystemHealth`, a deliberately temporary landing page that proves the end-to-end path: React renders live JSON fetched from the running FastAPI process. Phase 6 replaces it with the triage queue.

| File | Lines | Role |
| --- | --- | --- |
| `frontend/index.html` | 17 | Vite HTML entry, dark theme default, `#root` mount point |
| `frontend/src/main.tsx` | 15 | React root creation and global stylesheet import |
| `frontend/src/App.tsx` | 20 | Provider composition: query client, error boundary, screen |
| `frontend/src/api/client.ts` | 70 | `fetch` wrapper, `ApiError`, base-URL resolution |
| `frontend/src/api/queries.ts` | 27 | TanStack Query hooks and the query-key registry |
| `frontend/src/api/queryClient.ts` | 24 | `QueryClient` factory with retry and staleness policy |
| `frontend/src/api/types.ts` | 13 | Hand-written aliases over the generated OpenAPI types |
| `frontend/src/pages/SystemHealth.tsx` | 80 | The Phase 0 landing page |
| `frontend/src/pages/SystemHealth.test.tsx` | 133 | Stubbed and live-backend tests for that page |
| `frontend/src/components/HealthPanel.tsx` | 130 | Live `/health` card: loading, error and data states |
| `frontend/src/components/ErrorBoundary.tsx` | 67 | Per-screen render-error boundary with reset |
| `frontend/src/components/ui/badge.tsx` | 50 | Severity badge, including the distinct `novel` variant |
| `frontend/src/components/ui/button.tsx` | 44 | Button primitive with three variants, two sizes |
| `frontend/src/components/ui/card.tsx` | 66 | Card and its five sub-components |
| `frontend/src/components/ui/skeleton.tsx` | 15 | Loading placeholder block |
| `frontend/src/lib/env.ts` | 26 | Typed, defaulted access to `import.meta.env` |
| `frontend/src/lib/utils.ts` | 7 | `cn` class-name merge helper |
| `frontend/src/index.css` | 106 | Tailwind import, palette, theme tokens, base layer |
| `frontend/src/types/api.d.ts` | 877 | **Generated** OpenAPI types — never hand-edited |
| `frontend/src/vite-env.d.ts` | 18 | `ImportMetaEnv` declarations for every `VITE_*` var |
| `frontend/src/test/setup.ts` | 6 | Vitest setup: jest-dom matchers and DOM cleanup |
| `frontend/scripts/generate-types.mjs` | 45 | Fetches the OpenAPI schema and writes `api.d.ts` |
| `frontend/vite.config.ts` | 52 | Dev server, proxy, alias, build and Vitest config |
| `frontend/tsconfig.json` | 29 | Strict app compiler options and the `@/*` path alias |
| `frontend/tsconfig.node.json` | 16 | Compiler options for Node-side config and scripts |
| `frontend/package.json` | 47 | Scripts and dependency manifest |
| `frontend/components.json` | 20 | shadcn/ui generator configuration |
| `frontend/Dockerfile` | 30 | Four-stage image: `deps`, `dev`, `build`, `serve` — documented in [Code-Infrastructure](Code-Infrastructure.md) |
| `frontend/nginx.conf` | 28 | Static hosting plus `/api` proxy for the `serve` stage — documented in [Code-Infrastructure](Code-Infrastructure.md) |
| `frontend/package-lock.json` | — | npm lockfile; `npm ci` in `frontend/Dockerfile` installs from it, so it must be committed and in step with `package.json` |

---

## Entry points

## index.html

`frontend/index.html` — the single HTML document Vite serves and builds from, carrying the mount point and the default theme class.

Vite treats `index.html` as the build entry rather than a template emitted by a bundler plugin, so the `<script type="module" src="/src/main.tsx">` tag is the real module graph root. Everything the browser loads is reached from that one import.

The `dark` class is set on `<html>` in the markup, not applied at runtime. A SOC console is read for hours in a dim room, so the dark theme is the default state of the document rather than something JavaScript toggles on after first paint — which would otherwise produce a white flash on every load. The companion `<meta name="color-scheme" content="dark light">` tells the browser to render form controls and scrollbars in the matching scheme before any CSS parses.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `<html lang="en" class="dark">` | element | `class="dark"` | Activates the `.dark` palette block in [index.css](#indexcss); dark is the default, light is the fallback |
| `<meta name="color-scheme">` | element | `content="dark light"` | Browser-level scheme hint so native UI matches before CSS loads |
| `<meta charset>` | element | `charset="UTF-8"` | Declared first so the parser never has to restart on an encoding switch |
| `<meta name="viewport">` | element | `content="width=device-width, initial-scale=1.0"` | Standard responsive viewport; the dashboard is laid out for a desktop console but does not lock zoom |
| `<title>` | element | `Recluse` | Document title |
| `<meta name="description">` | element | `Two-stage ML network intrusion detection with SOC triage.` | Page description |
| `#root` | element | `<div id="root"></div>` | The DOM node `main.tsx` mounts React into |
| module script | element | `<script type="module" src="/src/main.tsx">` | Build and runtime entry |

- The `#root` id is load-bearing: `main.tsx` throws if it is missing.
- No inline script, no theme-detection snippet — the theme is static for Phase 0.

## main.tsx

`frontend/src/main.tsx` — creates the React 18 root and renders `App` inside `StrictMode`.

This file does three things and nothing else: import the global stylesheet, find `#root`, and mount. Keeping it that small means the application shell lives in `App.tsx`, where it is renderable inside a test without touching the DOM bootstrap.

The `#root` lookup is a hard failure rather than a silent no-op. `createRoot(null)` would throw a less specific error further down the React internals, so the check converts a missing mount point into a message that names the file to fix.

`StrictMode` double-invokes renders and effects in development. That is deliberate: it surfaces accidental side effects in render, which is precisely the class of bug a polling dashboard would otherwise hide.

```tsx
const container = document.getElementById('root')
if (!container) throw new Error('missing #root element in index.html')

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
```

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `container` | const | `const container: HTMLElement \| null` | The resolved `#root` element; a `null` value throws immediately |
| module side effect | import | `import './index.css'` | Pulls the Tailwind entry stylesheet into the bundle |

- No default export and no exported symbols — this module is executed for its effect.
- The stylesheet is imported here rather than in `App.tsx` so tests that render `App` do not pay for CSS processing.

## App.tsx

`frontend/src/App.tsx` — composes the provider stack and renders the single Phase 0 screen.

`App` wires three things together in a fixed order: a `QueryClientProvider` holding the TanStack Query cache, an `ErrorBoundary` labelled `"Application"`, and the `SystemHealth` screen. Server state is provided once at the top so no component below has to construct its own fetching logic.

The query client is created through `useState(createQueryClient)` rather than at module scope. Module scope would share one cache across every test in a file, letting a stubbed response from one case leak into the next; a module-level `const` also cannot be reset between renders. Passing the factory (not `createQueryClient()`) means `useState` calls it lazily, exactly once per mounted `App`.

The outer boundary is the last line of defence. A render error inside `HealthPanel` is caught by the panel-level boundary that wraps it in `SystemHealth.tsx`; anything outside that wrapper — the screen's own header, the phase list, or a provider failure — is caught here so the page never goes blank.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `App` | React component | `export function App(): JSX.Element` | Root component: query provider, application-level error boundary, current screen |

- Per-app query client keeps Vitest cases isolated from one another.
- No router. With one screen there is nothing to route; Phase 6 introduces navigation alongside the queue and analytics screens.

---

## API layer

## api/client.ts

`frontend/src/api/client.ts` — the single `fetch` wrapper every request goes through, plus the typed error it raises.

There is exactly one function in the application that calls `fetch` against the backend: `request<T>`. Centralising it means base-URL resolution, the `Accept` header, JSON parsing, `204` handling and error shaping are decided once. A component that wants data calls a hook in [api/queries.ts](#apiqueriests), which calls `request`, which calls `fetch`.

Base URL resolution is a plain string concatenation: `` `${env.apiBaseUrl}${path}` ``, where `env.apiBaseUrl` defaults to `/api/v1` (see [lib/env.ts](#libenvts)). That default is a *relative* URL on purpose. In development the Vite dev server proxies `/api` to the backend, so the browser stays same-origin and never needs CORS; in production the same relative path is served through the reverse proxy in front of the container. Neither case requires rebuilding the bundle with a different origin baked in. Setting `VITE_API_BASE_URL` to an absolute URL is supported for the case where the API genuinely lives elsewhere.

Error handling is where the file earns its length. A non-2xx response is read for a body, a human-readable message is extracted from FastAPI's `detail` field when present, and an `ApiError` is thrown carrying the status, the URL, the message and the raw body. The `isNotImplemented` getter singles out `501`, because the backend answers `501` for every route a later phase fills in (see [API-Reference](API-Reference.md)). That distinction is what lets the UI say "not built yet" instead of showing a generic failure, and it is what [api/queryClient.ts](#apiqueryclientts) keys its retry policy off.

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
| `ApiError.isNotImplemented` | getter | `get isNotImplemented(): boolean` | `true` when `status === 501` — the endpoint exists but its phase has not been built |
| `readBody` | function (module-private) | `async function readBody(response: Response): Promise<unknown>` | Parses JSON when `content-type` includes `application/json`, otherwise text; returns `undefined` on a parse failure |
| `messageFrom` | function (module-private) | `function messageFrom(body: unknown, response: Response): string` | Uses the body's string `detail` when present, else `` `${status} ${statusText}` `` |
| `request` | function | `export async function request<T>(path: string, init?: RequestInit): Promise<T>` | Fetch JSON from the API; `path` is relative to `env.apiBaseUrl` |

- `Accept: application/json` is set first and spread-overridable, so a caller can override it but does not have to repeat it.
- A `204 No Content` resolves to `undefined` cast to `T` rather than attempting `response.json()`, which would throw on an empty body.
- `readBody` swallows parse errors deliberately: a malformed error body must not mask the status code the caller actually needs.
- The generic `T` is an unchecked cast. Type safety comes from the generated schema types in [api/types.ts](#apitypests), not from runtime validation.

## api/queries.ts

`frontend/src/api/queries.ts` — the TanStack Query hooks components use, and the one place query keys are defined.

Every piece of server state reaches a component through a hook in this file. `useEffect` fetch chains are ruled out, so there is no alternative path: a component calls a hook, the hook owns the key, the endpoint, the return type and the refresh policy.

`queryKeys` exists so invalidation cannot go stale. When Phase 5 and 6 add verdict submission, invalidating the alert list will reference `queryKeys.alerts` rather than a string literal retyped at the call site, and a rename becomes a compile error instead of a cache that silently never refreshes.

One hook exists today. `useHealth` polls `GET /api/v1/health` on the interval from `VITE_HEALTH_POLL_MS` (default 5000 ms) with `staleTime: 0`, overriding the client-level default of `2_000`. Zero staleness matters here because the point of the panel is that `uptime_s` advances between polls — a cached response would make a live process indistinguishable from a frozen one.

```ts
export const queryKeys = {
  health: ['health'] as const,
}

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => request<HealthResponse>('/health'),
    refetchInterval: env.healthPollMs,
    staleTime: 0,
  })
}
```

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `queryKeys` | const | `export const queryKeys = { health: ['health'] as const }` | Query-key registry; `as const` keeps the tuple literal so key types stay narrow |
| `useHealth` | React hook | `export function useHealth(): UseQueryResult<HealthResponse, Error>` | Polls `GET {apiBaseUrl}/health`; key `['health']`, `refetchInterval: env.healthPollMs`, `staleTime: 0` |

- Hook inventory by phase: `useHealth` is the only hook in Phase 0. Alerts, verdicts, metrics, drift, analytics and replay hooks arrive with Phases 5 and 6 alongside the endpoints that serve them — see [API-Reference](API-Reference.md) for the full route surface and [Roadmap](Roadmap.md) for the ordering.
- The endpoint path passed to `request` is `'/health'`, not `'/api/v1/health'`: the `/api/v1` prefix comes from `env.apiBaseUrl`.
- `refetchInterval` polls only while the query is mounted; TanStack Query pauses background polling for unmounted observers.

## api/queryClient.ts

`frontend/src/api/queryClient.ts` — factory producing the configured `QueryClient` for an application instance.

The retry policy is the substance of this file. TanStack Query retries failed queries three times by default with exponential backoff, which is right for a flaky network and wrong for two cases this project hits constantly.

A `501` means a later phase has not been built. Retrying it cannot change the answer and only delays the message the UI wants to show, so `isNotImplemented` short-circuits to no retries. Any other 4xx is likewise the caller's fault and not worth repeating. Everything else — 5xx and network failures — retries up to twice. The predicate is written against `ApiError` specifically: a thrown value that is not an `ApiError` — a network-level `TypeError` from `fetch`, for instance — matches neither guard and falls through to `failureCount < 2`, so genuine connectivity failures are retried like a 5xx. That fall-through, together with the retried 5xx, is why the error-state case in [SystemHealth.test.tsx](#pagessystemhealthtesttsx) allows ten seconds for the failure message to appear.

`refetchOnWindowFocus: true` is kept on. An analyst who tabs back to a monitoring console expects what they see to be current. `staleTime: 2_000` sets a two-second floor on refetching so a burst of remounts does not turn into a request storm; `useHealth` overrides it to `0`.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `createQueryClient` | function | `export function createQueryClient(): QueryClient` | Builds a `QueryClient` whose query defaults carry the retry predicate, focus refetching and a 2 s stale time |
| retry predicate | option | `retry: (failureCount: number, error: Error) => boolean` | `false` for `ApiError` with `isNotImplemented`; `false` for any `ApiError` with `status < 500`; otherwise `failureCount < 2` |

- Exported as a factory, not a singleton, so `App` can hold one per instance and tests stay isolated.
- No mutation defaults are configured yet; they arrive with verdict submission in Phase 6.

## api/types.ts

`frontend/src/api/types.ts` — concrete, named aliases over the generated OpenAPI type tree.

Components and hooks import `HealthResponse` from here, never `components['schemas']['HealthResponse']` from the generated file. The indirection keeps [types/api.d.ts](#typesapidts) an implementation detail: if the backend renames a schema, the break is one line in this file plus type errors at the call sites, rather than a scatter of bracket-indexed lookups across the tree.

It is also the only hand-written file that is permitted to import from `@/types/api`.

```ts
import type { components } from '@/types/api'

export type HealthResponse = components['schemas']['HealthResponse']
export type NotImplementedResponse =
  components['schemas']['NotImplementedResponse']

export type HealthStatus = HealthResponse['status']
```

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `HealthResponse` | type alias | `export type HealthResponse = components['schemas']['HealthResponse']` | `{ status: 'ok' \| 'degraded'; model_version: string; uptime_s: number }` |
| `NotImplementedResponse` | type alias | `export type NotImplementedResponse = components['schemas']['NotImplementedResponse']` | `{ detail: string; phase: string; endpoint: string }` — the body every phase-stub route returns |
| `HealthStatus` | type alias | `export type HealthStatus = HealthResponse['status']` | The `'ok' \| 'degraded'` union, extracted for reuse |

- Type-only file: it emits nothing at runtime, and `verbatimModuleSyntax` in [tsconfig.json](#tsconfigjson) enforces the `import type` form.
- `HealthStatus` is derived from `HealthResponse` rather than declared separately, so the union cannot drift from the schema.
- Only `HealthResponse` is consumed today — by [api/queries.ts](#apiqueriests) (the `useHealth` return type) and by [pages/SystemHealth.test.tsx](#pagessystemhealthtesttsx) (the stub payload). `NotImplementedResponse` and `HealthStatus` are declared ahead of the screens that need them and are imported nowhere in `src/`.
- `NotImplementedResponse` is the type a Phase 6 screen will read `phase` from when rendering an explicit "not built yet" state out of `ApiError.body`.

---

## Pages

## pages/SystemHealth.tsx

`frontend/src/pages/SystemHealth.tsx` — the Phase 0 landing page: a health panel beside a build-progress list.

This screen is temporary by design and says so in its own docblock. From Phase 6 the landing page is the triage queue — analysts live in the queue, so the queue is home — and this shell is replaced rather than promoted to an overview dashboard. Building an overview now would create exactly the "dashboard as home page" pattern [Anti-Patterns](Anti-Patterns.md) rules out.

The layout is a two-column grid at `md` and above: a fixed-ish left column (`minmax(0,24rem)`) holding `HealthPanel` wrapped in its own `ErrorBoundary`, and a fluid right column holding the phase list. The header states what the system is in three sentences, ending on the constraint that governs the whole project: it alerts, ranks and explains, and never blocks traffic.

`PHASES` is a local `as const` array of the ten build phases. It is static content, not fetched state — there is no endpoint that reports build progress, and inventing one would be a fake. Phase 0 carries `state: 'current'` and renders an `info` badge; everything else is `pending`. The `data-state` attribute is set on each list item so styling can key off it later without a class-name lookup.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `PHASES` | const (module-private) | `const PHASES: readonly { id: number; name: string; state: 'current' \| 'pending' }[]` | The ten phases, ids `0`–`9`, from `Scaffolding` to `Real traffic`; only id `0` is `'current'` |
| `SystemHealth` | React component | `export function SystemHealth(): JSX.Element` | Renders the header, the boundary-wrapped `HealthPanel`, and the build-progress list |

- The `ShieldAlert` icon is tinted with `var(--novel)` and the header badge uses `variant="novel"` — the same channel reserved for `UNCLASSIFIED_ANOMALY`, tying the landing page to the project's distinguishing claim.
- Phase ids are zero-padded to two digits with `String(phase.id).padStart(2, '0')` and rendered in the `tabular font-mono` combination so the column does not jitter.
- The `<section>` is labelled by `aria-labelledby="build-progress"`, and the phase list is an `<ol>` because the order is meaningful.
- Status: implemented — and explicitly scheduled for replacement in Phase 6. See [Frontend-Screens](Frontend-Screens.md).

## pages/SystemHealth.test.tsx

`frontend/src/pages/SystemHealth.test.tsx` — the Phase 0 checkpoint, asserted twice: once against a stub, once against the live backend.

The first suite, `SystemHealth (stubbed backend)`, pins the rendering contract. `globalThis.fetch` is replaced with a stub returning a fixed `HealthResponse` (`status: 'ok'`, `model_version: 'unloaded'`, `uptime_s: 12.5`) and five cases assert the panel's behaviour: the three field labels render, a skeleton appears before the first response, the request URL is exactly `/api/v1/health`, no accuracy figure appears anywhere, and a backend failure produces a visible error rather than a blank panel.

The accuracy case is a project constraint expressed as a test. On traffic that is 99% benign, a model that always answers "benign" scores 99%, so a hero accuracy tile is the exact failure mode the brief rules out. The test asserts `container.textContent` matches neither `/accura/i` nor `/\d{2}\.\d%/` — the word and the shape of the number. It is asserted, not merely avoided.

The second suite, `SystemHealth (live FastAPI)`, is the part that actually proves "live health data fetched from FastAPI". Before the suite is declared, the file performs a top-level `await fetch` against `${BACKEND}/api/v1/health` and uses `describe.skipIf(!backendIsUp)` so the suite is skipped cleanly when no backend is running. When one is, `fetch` is wrapped to rewrite root-relative URLs onto the backend origin — jsdom would otherwise resolve `/api/v1/health` against its own origin — and the test asserts the `model_version` string rendered on screen is the one the live process just reported.

```ts
const BACKEND = import.meta.env.VITE_DEV_PROXY_TARGET ?? 'http://127.0.0.1:8000'
```

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `BACKEND` | const | `const BACKEND: string` | Live backend origin, from `VITE_DEV_PROXY_TARGET`, falling back to `http://127.0.0.1:8000` — the same var the dev proxy uses, so the test target cannot drift |
| `SystemHealth (stubbed backend)` | test suite | `describe('SystemHealth (stubbed backend)', ...)` | Five cases against a stubbed `fetch` |
| `renders the three health fields returned by the API` | test | `it(...)` | Finds `model_version`, `status` and `uptime_s` labels |
| `shows a skeleton before the first response lands` | test | `it(...)` | Asserts `role="status"` with accessible name matching `/loading health/i` is visible |
| `requests the configured API path` | test | `it(...)` | Asserts the first `fetch` argument stringifies to `/api/v1/health` |
| `renders no accuracy figure anywhere` | test | `it(...)` | Asserts the rendered text matches neither `/accura/i` nor `/\d{2}\.\d%/` |
| `surfaces a backend failure instead of rendering a blank panel` | test | `it(..., 15_000)` | Stubs a `503`, waits up to `10_000` ms for `/backend unreachable/i` |
| `backendIsUp` | const | `const backendIsUp: boolean` | Top-level probe of `${BACKEND}/api/v1/health`; `false` on any rejection |
| `SystemHealth (live FastAPI)` | test suite | `describe.skipIf(!backendIsUp)('SystemHealth (live FastAPI)', ...)` | Real HTTP round trip; skipped when the backend is down |
| `renders the version and uptime reported by the live process` | test | `it(...)` | Fetches health directly, renders `App`, asserts the live `model_version` string is on screen |

- The `503` case needs its long timeouts because the retry predicate in [api/queryClient.ts](#apiqueryclientts) retries 5xx twice with backoff before the panel gives up.
- `afterEach` calls `vi.unstubAllGlobals()` and `vi.restoreAllMocks()`; DOM cleanup is handled globally by [test/setup.ts](#testsetupts).
- The live suite skips rather than fails when no backend is running, so `npm test` is usable without starting the stack — and `make test` still exercises the real path when it is.
- Status: implemented. See [Testing](Testing.md) for how this fits the wider suite.

---

## Components

## components/HealthPanel.tsx

`frontend/src/components/HealthPanel.tsx` — the card rendering live `/api/v1/health` data, with explicit loading, error and success states.

This component is the Phase 0 checkpoint made visible: real data from FastAPI rendered in React. It consumes `useHealth` and branches on three states in a fixed order — `isPending` renders three `Skeleton` bars inside a `role="status"` region labelled "Loading health"; `error` renders a `ServerCrash` icon, the heading "Backend unreachable" and the error message in monospace; otherwise a `<dl>` of the three health fields.

Note what is deliberately absent: there is no accuracy tile, and no hero percentage of any kind. On traffic that is 99% benign such a number would be meaningless, and a test asserts it never appears.

The footer carries the honest status line. When `model_version` is `'unloaded'` it reads "No model trained yet — Phase 2 writes the first bundle."; otherwise "Scoring with the loaded bundle." Today it is always the former, because there is no trained model. Beside it, a ghost `Button` calls `refetch()` and disables itself while `isFetching`, spinning the `RefreshCw` icon through `animate-spin`.

`formatUptime` keeps the number readable as the process runs: seconds with one decimal below a minute, `Xm Ys` below an hour, `Xh Ym` above.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `Field` | React component (module-private) | `function Field({ term, children }: { term: string; children: React.ReactNode }): JSX.Element` | One `<dt>`/`<dd>` row with a bottom border suppressed on the last child; value rendered `tabular font-mono` |
| `formatUptime` | function (module-private) | `function formatUptime(seconds: number): string` | `< 60` → `"12.5s"`; `< 3600` → `"5m 12s"`; otherwise `"2h 3m"` |
| `HealthPanel` | React component | `export function HealthPanel(): JSX.Element` | Live backend health card driven by `useHealth`, covering pending, error and data states |

- The card description reads `GET /api/v1/health, polled every {env.healthPollMs / 1000}s`, so the displayed interval always matches the configured one.
- The status `Badge` maps `'ok'` to the `ok` variant and anything else — that is, `'degraded'` — to `high`.
- `model_version === 'unloaded'` is rendered in muted text to read as an absence rather than a version string.
- `onClick={() => void refetch()}` discards the returned promise explicitly, satisfying strict lint rules without an unhandled rejection.

## components/ErrorBoundary.tsx

`frontend/src/components/ErrorBoundary.tsx` — a class error boundary rendering a recoverable fallback card for one screen or panel.

React error boundaries must be class components; this is the only class component in the codebase. Every screen gets one, because a monitoring dashboard that renders a blank page on a render error looks broken in exactly the way a monitoring tool must not. The `label` prop is named in the fallback so a failure points at the screen that caused it: `"Health panel failed to render"` rather than an anonymous apology.

The fallback is a `Card` with a `critical`-tinted border, an `AlertTriangle` icon, the label line, the error message in monospace, and a "Try again" button. The button calls `reset`, which clears the stored error and re-renders children — enough to recover from a transient failure without a page reload, and harmless if the error is persistent since the fallback simply returns.

`componentDidCatch` logs the error and `info.componentStack` through `console.error`, so the stack is available in the browser console even though the UI shows only the message.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `Props` | interface (module-private) | `interface Props { children: ReactNode; label?: string }` | `label` is named in the fallback so a failure points at the screen that caused it |
| `State` | interface (module-private) | `interface State { error: Error \| null }` | `null` means render children |
| `ErrorBoundary` | React class component | `export class ErrorBoundary extends Component<Props, State>` | Error boundary for a screen |
| `ErrorBoundary.getDerivedStateFromError` | static method | `static getDerivedStateFromError(error: Error): State` | Stores the thrown error so the next render shows the fallback |
| `ErrorBoundary.componentDidCatch` | method | `componentDidCatch(error: Error, info: ErrorInfo): void` | Logs `'render failed'`, the error and `info.componentStack` |
| `ErrorBoundary.reset` | private field | `private reset = () => this.setState({ error: null })` | Clears the error and retries rendering children |
| `ErrorBoundary.render` | method | `render(): ReactNode` | Children when no error; the fallback card otherwise |

- Boundaries catch render, lifecycle and constructor errors only. A rejected fetch is not one of them — that path is handled by TanStack Query and rendered by `HealthPanel`'s error branch.
- Used at two levels today: application-wide in [App.tsx](#apptsx) and around the panel in [SystemHealth.tsx](#pagessystemhealthtsx).
- The border colour uses `color-mix(in oklab, var(--critical) 40%, transparent)` so it reads as a tint rather than a solid red frame.

---

## UI primitives

Four shadcn/ui "new-york" components, copied into the repository rather than installed so they can be edited directly. Each is a plain function that spreads `ComponentProps<'element'>`, merges classes through `cn`, and carries a `data-slot` attribute for styling hooks.

| File | Exports | Notes |
| --- | --- | --- |
| `components/ui/badge.tsx` | `Badge`, `badgeVariants` | Seven variants named by meaning, not hue: `neutral`, `ok`, `info`, `medium`, `high`, `critical`, `novel` |
| `components/ui/button.tsx` | `Button`, `buttonVariants` | Three variants (`default`, `outline`, `ghost`), two sizes (`sm`, `default`) |
| `components/ui/card.tsx` | `Card`, `CardHeader`, `CardTitle`, `CardDescription`, `CardContent`, `CardFooter` | Thin wrappers with consistent padding, so any subset of parts spaces evenly |
| `components/ui/skeleton.tsx` | `Skeleton` | `bg-muted animate-pulse rounded-md`; the caller supplies the size |

Colours come from `color-mix(in oklab, var(--token) 18%, transparent)` against the raw token, so a single palette change in [index.css](#indexcss) moves every badge. Radius comes from `var(--radius-card)`. Both are tokens rather than repeated utility classes.

Three decisions in here are worth knowing:

- **`novel` is its own badge variant, not a reuse of `critical`.** `UNCLASSIFIED_ANOMALY` has to be distinguishable at a glance from a high-severity *known* attack, so it is the only variant with a visible coloured border as well as a tinted fill. That distinction is the visual claim Stage 2 exists to make.
- **There is no `destructive` button variant, and none is planned.** There is no destructive action anywhere in this UI — no block, drop or quarantine control. See [Anti-Patterns](Anti-Patterns.md).
- **`Skeleton` sets `aria-hidden="true"` on itself.** It is decoration; the accessible announcement belongs on the wrapping `role="status"` region. Marking both would make a screen reader read the placeholder as content. The pulse is suppressed by the `prefers-reduced-motion` block in [index.css](#indexcss).

`CardTitle` renders an `h3` by default; a screen needing a different level must adjust, since heading order is an accessibility concern rather than a styling one. A `low` colour token exists with no matching badge variant — it lands with the severity column in Phase 6.

---
## Library and types

## lib/env.ts

`frontend/src/lib/env.ts` — typed, defaulted access to the Vite environment, and the only file containing a literal port or path.

Every configurable value has a default here and nowhere else, so no component contains a literal port, path or interval. `apiBaseUrl` defaults to `/api/v1` — relative on purpose, so the dev proxy and the production reverse proxy both work without a rebuild. `healthPollMs` defaults to `5000`.

`positiveInt` guards the numeric parse. `import.meta.env` values are always strings or `undefined`, and `Number(undefined)` is `NaN` while `Number('')` is `0` — either would produce a broken or runaway poll interval. The helper falls back unless the parsed value is both finite and positive.

```ts
const DEFAULTS = {
  apiBaseUrl: '/api/v1',
  healthPollMs: 5000,
} as const

function positiveInt(raw: string | undefined, fallback: number): number {
  const parsed = Number(raw)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}
```

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `DEFAULTS` | const (module-private) | `const DEFAULTS = { apiBaseUrl: '/api/v1', healthPollMs: 5000 } as const` | The only place these literals appear |
| `positiveInt` | function (module-private) | `function positiveInt(raw: string \| undefined, fallback: number): number` | Returns the parsed number when finite and `> 0`, else the fallback |
| `env` | const | `export const env: { readonly apiBaseUrl: string; readonly healthPollMs: number }` | Frozen-by-`as const` view of the resolved configuration |

- Only `VITE_API_BASE_URL` and `VITE_HEALTH_POLL_MS` are read here. `VITE_DEV_SERVER_HOST` and `VITE_DEV_SERVER_PORT` are consumed only by [vite.config.ts](#viteconfigts). `VITE_DEV_PROXY_TARGET` is read by `vite.config.ts` and by [scripts/generate-types.mjs](#scriptsgenerate-typesmjs) at build time, and also by `SystemHealth.test.tsx` through `import.meta.env`, which is how the live-backend suite locates the running API. See [Configuration](Configuration.md).
- Vite inlines `import.meta.env.VITE_*` at build time, so changing one requires a restart of the dev server or a rebuild.
- Only `VITE_`-prefixed variables reach the browser bundle. Nothing secret belongs in one.

## lib/utils.ts

`frontend/src/lib/utils.ts` — the `cn` class-name merge helper used by every component.

`clsx` flattens conditional class inputs — strings, arrays, objects — into one string; `tailwind-merge` then resolves conflicts so a later utility in the same Tailwind group wins. Without the second step, `cn('px-5', 'px-2')` would emit both and let CSS source order decide, which is what makes a `className` override from a caller unreliable.

```ts
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
```

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `cn` | function | `export function cn(...inputs: ClassValue[]): string` | Merge conditional class names, with later Tailwind utilities winning |

- Referenced by the `utils` alias in [components.json](#componentsjson), so generated shadcn components import it automatically.

## index.css

`frontend/src/index.css` — Tailwind v4 entry point, colour palette, theme tokens and base layer.

Tailwind v4 is configured in CSS rather than in a JavaScript config file, which is why there is no `tailwind.config.js` in this repository and `components.json` sets `"tailwind": { "config": "" }`. The file opens with `@import 'tailwindcss'` and declares one custom variant, `dark`, defined as `(&:is(.dark *))` — the `.dark` class on `<html>` is what activates it.

The palette is authored in **oklch** and split into two blocks. `:root` carries the light theme; `.dark` overrides every token with a darker, slightly more saturated set. Dark is the default state of the document because a SOC console is read for hours in a dim room, so the light theme is the fallback rather than the base.

Severity colours are named by meaning rather than by hue — `--ok`, `--info`, `--low`, `--medium`, `--high`, `--critical`, `--novel` — so a palette change never requires renaming a class. `--novel` is deliberately its own channel: `UNCLASSIFIED_ANOMALY` has to be findable at a glance, and reusing the critical red would make it read as just another high-severity known attack.

`@theme inline` republishes each raw variable as a Tailwind colour token (`--color-background`, `--color-novel`, and so on), which is what makes `bg-card`, `text-muted-foreground` and `border-border` resolve. It also defines `--font-mono` and `--radius-card`. The `@layer base` block sets a default border colour on every element, declares `color-scheme: light dark` on `html`, paints the body background and foreground with antialiasing, and defines the `.tabular` utility. A final `prefers-reduced-motion` block collapses every animation and transition to `0.01ms`.

```css
.tabular {
  font-variant-numeric: tabular-nums;
}
```

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `@import 'tailwindcss'` | at-rule | `@import 'tailwindcss';` | Tailwind v4 entry; no JS config file exists |
| `@custom-variant dark` | at-rule | `@custom-variant dark (&:is(.dark *));` | Makes `dark:` utilities key off the `.dark` class rather than the OS setting |
| `:root` | rule | palette block | Light theme: `--background`, `--foreground`, `--muted`, `--muted-foreground`, `--card`, `--card-foreground`, `--border`, `--ring` |
| `:root` severity tokens | custom properties | `--ok: oklch(0.62 0.15 150)`, `--info: oklch(0.62 0.14 235)`, `--low: oklch(0.65 0.09 235)`, `--medium: oklch(0.75 0.14 85)`, `--high: oklch(0.67 0.17 45)`, `--critical: oklch(0.58 0.2 25)`, `--novel: oklch(0.62 0.19 305)` | Named by meaning; `--novel` is violet and shares no hue with `--critical` |
| `.dark` | rule | palette override | Dark theme values for every token above, e.g. `--background: oklch(0.18 0.012 260)`, `--critical: oklch(0.68 0.2 22)`, `--novel: oklch(0.72 0.18 305)` |
| `@theme inline` | at-rule | `--color-*: var(--*)` | Exposes each palette variable as a Tailwind colour utility |
| `--font-mono` | theme token | `ui-monospace, 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace` | Monospace stack used for every raw value on screen |
| `--radius-card` | theme token | `0.625rem` | Card corner radius, consumed by `Card` |
| `@layer base` — `*` | rule | `border-color: var(--color-border)` | Single default border colour, so `border` alone is enough |
| `@layer base` — `html` | rule | `color-scheme: light dark` | Native control rendering follows the active theme |
| `@layer base` — `body` | rule | background, foreground, `-webkit-font-smoothing: antialiased`, `text-rendering: optimizeLegibility` | Page surface |
| `.tabular` | utility | `font-variant-numeric: tabular-nums` | Fixed-width digits so a changing uptime counter does not make the row jitter |
| `@media (prefers-reduced-motion: reduce)` | at-rule | animation and transition durations forced to `0.01ms` | Applies to `*`, `*::before` and `*::after` |

- Colours are in oklch for perceptually even lightness across the severity ramp; mixing is done with `color-mix(in oklab, …)` in the component files for the same reason.
- `--card-foreground` is defined as `var(--foreground)` in both themes, so a foreground change propagates to cards automatically.
- `.tabular` is a plain class in the base layer rather than a Tailwind plugin — it is used as `tabular font-mono` wherever a number updates in place.

## types/api.d.ts

`frontend/src/types/api.d.ts` — **generated** TypeScript definitions for the entire backend API, produced from the FastAPI OpenAPI schema.

This file is 877 lines and is not written by hand. It is produced by [scripts/generate-types.mjs](#scriptsgenerate-typesmjs) via `npm run gen:types`, which reads `/openapi.json` from the running backend and emits the full type tree with a banner reading `GENERATED FILE - do not edit.` Hand-writing API types lets the client drift silently from the server; generating them turns a backend schema change into a TypeScript compile error.

It is not enumerated here, and it should not be read as documentation — [API-Reference](API-Reference.md) is the readable description of the same surface. Five top-level types are exported:

| Export | What it holds |
| --- | --- |
| `paths` | One entry per route, keyed by URL template, with the operations available on it |
| `webhooks` | `Record<string, never>` — the API declares no webhooks |
| `components` | `schemas`, containing `HealthResponse`, `NotImplementedResponse`, `HTTPValidationError` and `ValidationError` |
| `$defs` | `Record<string, never>` — no JSON Schema `$defs` are emitted |
| `operations` | One entry per operation id, e.g. `health_api_v1_health_get`, with its parameters and response bodies |

Application code never imports from this file directly. [api/types.ts](#apitypests) re-exports the handful of schemas the UI uses under readable names, and everything else imports from there.

**To regenerate:** start the backend, then run `npm run gen:types` from `frontend/`, or `make gen-types` / `./make.ps1 gen-types` from the repository root. The script reads `VITE_DEV_PROXY_TARGET` from the repo-root `.env` (falling back to `http://127.0.0.1:8000`) to locate the schema. If the backend is not running it prints `is the backend running? try: make dev at the repo root` and exits non-zero, leaving the existing file untouched.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `paths` | interface | `export interface paths` | Sixteen route entries, from `/api/v1/health` through `/api/v1/stream` |
| `webhooks` | type alias | `export type webhooks = Record<string, never>` | Empty |
| `components` | interface | `export interface components` | Carries `schemas`, including `HealthResponse` and `NotImplementedResponse` |
| `$defs` | type alias | `export type $defs = Record<string, never>` | Empty |
| `operations` | interface | `export interface operations` | Per-operation parameter and response types |

- Never hand-edit this file. Any edit is lost on the next `gen:types` run, and the banner says so.
- It is committed to the repository so a fresh clone type-checks without a running backend.
- It is the mechanical proof that the client cannot drift from the server: the sixteen route paths it contains are exactly the routes FastAPI registers, including the ones that currently answer `501`.
- Status: generated artifact, current as of Phase 0.

## vite-env.d.ts

`frontend/src/vite-env.d.ts` — ambient declarations giving `import.meta.env` a typed shape.

`/// <reference types="vite/client" />` pulls in Vite's own client types; the `ImportMetaEnv` interface below it then declares every `VITE_*` variable this project reads, each with a doc comment stating what it controls. Because the augmentation is typed, `import.meta.env.VITE_HEALTH_POL_MS` is a compile error rather than `undefined` at runtime.

Every entry is optional (`?`) and typed `string`, which is honest: Vite exposes environment values as strings, and none of them is guaranteed to be set. That is why [lib/env.ts](#libenvts) supplies defaults for the two the browser uses.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `ImportMetaEnv` | interface | `interface ImportMetaEnv` | Declares the five `VITE_*` variables below |
| `VITE_API_BASE_URL` | property | `readonly VITE_API_BASE_URL?: string` | Base path the typed API client prefixes onto every request |
| `VITE_HEALTH_POLL_MS` | property | `readonly VITE_HEALTH_POLL_MS?: string` | Health poll interval in milliseconds |
| `VITE_DEV_SERVER_PORT` | property | `readonly VITE_DEV_SERVER_PORT?: string` | Vite dev server port, also used by `vite preview` |
| `VITE_DEV_SERVER_HOST` | property | `readonly VITE_DEV_SERVER_HOST?: string` | Interface the dev server binds to; `::` is dual-stack |
| `VITE_DEV_PROXY_TARGET` | property | `readonly VITE_DEV_PROXY_TARGET?: string` | Backend origin the dev server proxies `/api` to |
| `ImportMeta` | interface | `interface ImportMeta { readonly env: ImportMetaEnv }` | Attaches the typed env to `import.meta` |

- Adding a new `VITE_*` variable means adding it here, to `.env.example`, and to [lib/env.ts](#libenvts) if the browser needs it at runtime.
- Declared values match the defaults in `.env.example`; see [Configuration](Configuration.md) for the full table across backend and frontend.

## test/setup.ts

`frontend/src/test/setup.ts` — global Vitest setup, loaded once per test environment.

Two lines of substance. Importing `@testing-library/jest-dom/vitest` registers the DOM matchers — `toBeInTheDocument`, `toBeVisible` and the rest — used throughout `SystemHealth.test.tsx`. Registering `cleanup` in a global `afterEach` unmounts every rendered tree between cases, so a component left mounted by one test cannot be found by `screen` queries in the next.

This file is wired in by `test.setupFiles` in [vite.config.ts](#viteconfigts), not by convention.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| jest-dom registration | import side effect | `import '@testing-library/jest-dom/vitest'` | Adds DOM matchers to `expect` |
| cleanup hook | statement | `afterEach(cleanup)` | Unmounts rendered components after each test |

- The matcher types are enabled separately through `"types": [..., "@testing-library/jest-dom"]` in [tsconfig.json](#tsconfigjson).
- `test.include` in [vite.config.ts](#viteconfigts) is `['src/**/*.{test,spec}.{ts,tsx}']`, so a test file must sit under `src/` with a `.test.` or `.spec.` infix to run at all. `pages/SystemHealth.test.tsx` is the only file matching it today.

---

## Build and tooling config

## scripts/generate-types.mjs

`frontend/scripts/generate-types.mjs` — reads the backend OpenAPI schema and writes `src/types/api.d.ts`.

The frontend never hand-writes API types, because hand-written types drift from the server. This script closes that gap: it resolves the backend origin, fetches `/openapi.json`, converts the schema to a TypeScript AST with `openapi-typescript`, stringifies it, prefixes a do-not-edit banner, and writes the result.

The backend URL is not hardcoded. `loadEnv('development', repoRoot, '')` reads the **repo-root** `.env` — the same file the backend and `vite.config.ts` read — and takes `VITE_DEV_PROXY_TARGET`, falling back to `http://127.0.0.1:8000`. The third argument `''` is an empty prefix, which makes `loadEnv` return every variable rather than only `VITE_*` ones.

Failure is loud and non-destructive. A fetch or conversion error prints the schema URL, the hint `is the backend running? try: make dev at the repo root`, and the underlying message, then sets `process.exitCode = 1`. Because the write happens only on success, a failed run leaves the committed `api.d.ts` intact rather than truncating it.

```js
const env = loadEnv('development', repoRoot, '')
const backend = env.VITE_DEV_PROXY_TARGET || 'http://127.0.0.1:8000'
const schemaUrl = new URL('/openapi.json', backend)
```

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `here` | const | `const here: string` | Directory of this script, from `fileURLToPath(import.meta.url)` |
| `repoRoot` | const | `const repoRoot: string` | `resolve(here, '..', '..')` — the repository root, where `.env` lives |
| `outFile` | const | `const outFile: string` | `resolve(here, '..', 'src', 'types', 'api.d.ts')` |
| `env` | const | `const env = loadEnv('development', repoRoot, '')` | Every variable in the repo-root `.env`, unprefixed |
| `backend` | const | `const backend: string` | `env.VITE_DEV_PROXY_TARGET \|\| 'http://127.0.0.1:8000'` |
| `schemaUrl` | const | `const schemaUrl = new URL('/openapi.json', backend)` | The FastAPI schema endpoint |
| `banner` | const | `const banner: string` | The `GENERATED FILE - do not edit.` header prepended to the output |
| top-level `try`/`catch` | statement | `await openapiTS(schemaUrl)` → `mkdir` → `writeFile` | Writes on success, prints diagnostics and sets `process.exitCode = 1` on failure |

- Run through `npm run gen:types` from `frontend/`, or `make gen-types` from the repository root. The backend must be running.
- `mkdir(dirname(outFile), { recursive: true })` means a missing `src/types/` directory is created rather than an error.
- An `.mjs` file rather than TypeScript, so it runs under `node` with no build step. It is **not** type-checked: [tsconfig.node.json](#tsconfignodejson) lists it in `include`, but without `allowJs` TypeScript skips `.mjs` files entirely.

## vite.config.ts

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
| `build` | option | `{ outDir: 'dist', sourcemap: mode !== 'production' }` | Source maps everywhere except production builds |
| `test` | option | ``{ environment: 'jsdom', globals: true, setupFiles: [`${here}src/test/setup.ts`], include: ['src/**/*.{test,spec}.{ts,tsx}'] }`` | Vitest configuration |

- The `/// <reference types="vitest/config" />` directive at the top is what makes the `test` key type-check inside `defineConfig`.
- `globals: true` is why test files can rely on ambient `describe`/`it`/`expect`, and why `"vitest/globals"` appears in the `types` array of `tsconfig.json` — though the test file imports them explicitly anyway.
- Changing the `@` alias requires the matching change in `tsconfig.json`; nothing enforces that automatically.
- `here` comes from `fileURLToPath(new URL('.', import.meta.url))`, which resolves to this directory **with a trailing separator** — which is why `` `${here}src/test/setup.ts` `` has no slash of its own. The path is absolute, so Vitest resolves the setup file regardless of the working directory the run starts from.
- `build.outDir` is `dist/`, which is gitignored. It is produced by `npm run build` and consumed by the `serve` stage of `frontend/Dockerfile`; nothing in `src/` reads it.

## tsconfig.json

`frontend/tsconfig.json` — compiler options for application code under `src/`.

Strict mode is on, plus four extra checks beyond it: `noUnusedLocals`, `noUnusedParameters`, `noFallthroughCasesInSwitch` and `noUncheckedSideEffectImports`. `verbatimModuleSyntax` forces `import type` to be written explicitly, which keeps type-only imports out of the emitted graph and makes the distinction visible when reading a file.

`noEmit` is true because Vite does the transpiling; TypeScript is used purely as a checker, run through `npm run typecheck`. The `types` array is pinned to `["vite/client", "vitest/globals", "@testing-library/jest-dom"]` rather than left to automatic `@types` discovery, so a transitively installed type package cannot quietly change what is in scope.

`paths` maps `@/*` to `src/*`, mirroring the Vite alias, and `include` is `["src"]` — the config files are covered by `tsconfig.node.json` instead. The target is `ES2022`.

- `exactOptionalPropertyTypes: false` is a deliberate concession to the generated schema types, which model optional fields as `field?: T` rather than `field?: T \| undefined`.
- `npm run typecheck` runs this config and the Node one in sequence, so both must pass before `npm run build` proceeds.

## tsconfig.node.json

`frontend/tsconfig.node.json` — compiler options for the Node-side files that configure and build the app.

`vite.config.ts` and `scripts/*.mjs` run in Node, not the browser: they use `node:fs/promises`, `node:path` and `node:url`, and they have no DOM. Checking them under the application config would require pulling Node types into browser code, so they get their own project with `"types": ["node"]`, an `ES2023` target and no DOM lib.

It keeps the same strictness — `strict`, `noUnusedLocals`, `noUnusedParameters`, `isolatedModules` — so a config file is not a place where type discipline quietly relaxes.

- The `scripts/**/*.mjs` entry in `include` is currently inert. `allowJs` is not set, so TypeScript drops `.mjs` files from the program rather than checking them — `tsc -p tsconfig.node.json --noEmit --listFiles` lists only `vite.config.ts`. `generate-types.mjs` is therefore unchecked; adding `"allowJs": true` (and `checkJs`) would be needed to check it.

## package.json

`frontend/package.json` — the npm manifest: scripts and the dependency set.

An ESM package (`"type": "module"`, which is why the config and script files use `import`), marked `"private": true` because it is never published. The scripts are the frontend's whole task surface; the repository-root `Makefile` and `make.ps1` call into them, so either entry point works.

| Script | Command | For |
| --- | --- | --- |
| `dev` | `vite` | Dev server with hot reload, on the port and host from the repo-root `.env` |
| `build` | `npm run typecheck && vite build` | Production bundle into `dist/`, type-checked first |
| `preview` | `vite preview` | Serves the built `dist/` on the same host and port |
| `typecheck` | `tsc -p tsconfig.json --noEmit && tsc -p tsconfig.node.json --noEmit` | App code and Node-side config as two separate projects |
| `test` | `vitest run` | Single run; what `make test` / `make test-frontend` invoke |
| `test:watch` | `vitest` | Watch mode |
| `gen:types` | `node scripts/generate-types.mjs` | Regenerates `src/types/api.d.ts` from the running backend |

Dependencies, by what they are for — versions live in `package.json` itself rather than here, where they would go stale:

- **Framework** — `react`, `react-dom`
- **Server state** — `@tanstack/react-query`, the only fetching mechanism permitted
- **Phase 6, declared but unused** — `@tanstack/react-table` and `@tanstack/react-virtual` for the triage queue (tens of thousands of rows during a fast replay), `recharts` for SHAP waterfalls and PR/ROC curves
- **Styling** — `tailwindcss` with the Vite plugin, `class-variance-authority`, `clsx` and `tailwind-merge` (the last two are what `cn` is built from), `lucide-react`, `@radix-ui/react-slot` for `asChild`
- **Build and test** — `vite`, `@vitejs/plugin-react`, `typescript`, `vitest`, `jsdom`, `@testing-library/react`, `@testing-library/jest-dom`
- **Codegen** — `openapi-typescript`

- The Phase 6 packages are declared now so the dependency set is fixed before the screens are built.
- `build` depends on `typecheck`, so it fails on a type error rather than emitting a bundle from unchecked code.
- `allowScripts` is an explicit allowlist naming the one dependency permitted to run an install script (`esbuild`).
- `package-lock.json` is committed and is what the container build uses: `frontend/Dockerfile` runs `npm ci`, which fails rather than resolving a new tree if the lockfile disagrees with the manifest. `npm install` locally updates it; commit the result.
## components.json

`frontend/components.json` — configuration for the shadcn/ui generator, so added components land in the right place with the right imports.

This file is read by the shadcn CLI, not by the application. It records the conventions the four existing UI primitives already follow, so a component added later is generated consistently rather than pasted in and hand-adjusted: the "new-york" style, TSX output, CSS variables for theming, and Lucide as the icon library.

`"tailwind": { "config": "" }` is the notable entry. Tailwind v4 has no JavaScript config file — the theme lives in [index.css](#indexcss), which is what the `css` key points at — so the field is intentionally empty rather than missing.

The `aliases` block mirrors the `@/*` path mapping shared by `tsconfig.json` and `vite.config.ts`, so generated files import `cn` from `@/lib/utils` and siblings from `@/components/ui` with no editing.

- `@/hooks` is declared but no `src/hooks/` directory exists yet; it is created the first time a shared hook is added.
- Generated components are checked into the repository and edited freely — `badge.tsx` already carries the project-specific `novel` variant.
- Status: implemented (configuration only; it has no runtime effect).

## Dockerfile and nginx.conf

**Paths:** `frontend/Dockerfile`, `frontend/nginx.conf` — the container build for the dashboard and the server block its static stage uses.

Both are documented in full on [Code-Infrastructure](Code-Infrastructure.md), alongside `docker-compose.yml` and the backend image, because the four stages only make sense next to the compose service that targets them. In short: `deps` runs `npm ci` from the lockfile; `dev` is what compose runs (Vite with HMR, `VITE_DEV_SERVER_HOST=0.0.0.0`, port 5173); `build` runs `npm run build`; `serve` copies `dist` into `nginx:1.29-alpine` with `nginx.conf` and is the Phase 8 packaging target, referenced by nothing in the default stack.

`nginx.conf` is a single `server` block: `try_files $uri $uri/ /index.html`, so an unknown path resolves to the SPA shell rather than a 404, and a `/api/` location proxying to `http://backend:8000` with buffering and caching disabled and a 24-hour read timeout — the Phase 5 alert feed is server-sent events, and a buffering proxy would hold the stream.

Status: implemented.
