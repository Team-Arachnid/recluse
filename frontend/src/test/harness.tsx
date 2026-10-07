/**
 * The screen-test harness: a routed app over a stubbed API.
 *
 * One stub for the whole surface rather than a mock per test, because the
 * screens read several endpoints each and a test that stubs only the one it
 * cares about passes while the screen renders an error for everything else.
 */
import { render } from '@testing-library/react'
import { vi } from 'vitest'

import { App } from '@/App'

import * as fixtures from './fixtures'

/** path (without the `/api/v1` prefix) -> body, or a status to answer with. */
export type Routes = Record<string, unknown | { status: number; body: unknown }>

/** The whole surface, answered with the fixtures. Individual tests override one
 *  entry rather than redeclaring the set. */
export function defaultRoutes(): Routes {
  return {
    '/health': fixtures.health,
    '/alerts': fixtures.alertPage,
    '/alerts/stats': fixtures.queueStats,
    '/alerts/1': fixtures.novelDetail,
    '/alerts/2': fixtures.midAlert,
    '/alerts/3': fixtures.knownDetail,
    '/alerts/1/related': fixtures.relatedAlerts,
    '/alerts/3/related': fixtures.relatedAlerts,
    '/metrics/model': fixtures.modelMetrics,
    '/metrics/threshold': fixtures.thresholdProjection,
    '/metrics/anomaly-histogram': fixtures.anomalyHistogram,
    '/metrics/drift': fixtures.driftResponse,
    '/models': fixtures.modelRegistry,
    '/retrain': fixtures.retrainRuns,
    '/analytics/summary': fixtures.analyticsSummary,
    '/analytics/mitre-coverage': fixtures.mitreCoverage,
    '/analytics/feedback': fixtures.feedbackLoop,
    '/replay/status': fixtures.replayStopped,
  }
}

export interface Harness {
  /** Every request the app made, in order, as a path with its query string. */
  calls: string[]
  /** Request bodies, keyed by `METHOD path`, for asserting a mutation's payload. */
  bodies: { method: string; path: string; body: unknown }[]
}

/**
 * Stub `fetch`, point the router at `route`, and render the app.
 *
 * `window.history.pushState` rather than a MemoryRouter: the app owns its
 * `BrowserRouter`, and swapping it out for the test would mean testing a
 * different component tree than the one that ships.
 */
export function renderApp(route = '/', routes: Routes = defaultRoutes()): Harness {
  const harness: Harness = { calls: [], bodies: [] }

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const [rawPath, query] = url.replace('/api/v1', '').split('?')
      harness.calls.push(query ? rawPath + '?' + query : rawPath)

      if (init?.body) {
        harness.bodies.push({
          method: init.method ?? 'GET',
          path: rawPath,
          body: JSON.parse(String(init.body)),
        })
      }

      // A verdict POST and a bulk PATCH are answered generically: what the
      // tests assert about them is the invalidation they trigger and the body
      // they send, not the echo.
      if (rawPath.endsWith('/verdict')) {
        return json(
          {
            id: 1,
            alert_id: Number(rawPath.split('/')[2]),
            verdict: (JSON.parse(String(init?.body)) as { verdict: string }).verdict,
            note: null,
            analyst: null,
            model_version: fixtures.health.model_version,
            created_at: '2026-10-06T10:31:00.000000',
          },
          201,
        )
      }
      if (rawPath === '/retrain' && (init?.method ?? 'GET') === 'POST') {
        return json(fixtures.retrainRequested, 202)
      }
      if (rawPath === '/alerts/status') {
        const body = JSON.parse(String(init?.body)) as { alert_ids: number[]; status: string }
        return json({ status: body.status, updated: body.alert_ids, missing: [] })
      }

      const match = routes[rawPath]
      if (match === undefined) return json({ detail: 'no stub for ' + rawPath }, 404)

      if (isStatused(match)) return json(match.body, match.status)
      return json(match)
    }),
  )

  // jsdom has no EventSource. The provider only constructs one once the replay
  // probe reports a running source, which the default stub does not -- this is
  // here so a test that *does* report one fails on an assertion rather than on
  // a missing global.
  if (!('EventSource' in globalThis)) {
    vi.stubGlobal(
      'EventSource',
      class {
        addEventListener() {}
        close() {}
      },
    )
  }

  // ResizeObserver is used by the threshold histogram to size its SVG.
  if (!('ResizeObserver' in globalThis)) {
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    )
  }

  /*
   * jsdom performs no layout, so every element measures 0x0. That is not a
   * cosmetic problem here: TanStack Virtual sizes its window from the scroll
   * container's `offsetHeight` and renders as many rows as fit, so a
   * zero-height container renders an empty queue -- and every row assertion
   * then fails against a table that works correctly in a browser.
   *
   * Both stubs are needed and they are read by different things: the
   * virtualiser reads `offsetWidth`/`offsetHeight`, while Recharts' responsive
   * container and the threshold histogram's own measurement read
   * `getBoundingClientRect`.
   */
  for (const [property, value] of [
    ['offsetWidth', 1280],
    ['offsetHeight', 720],
  ] as const) {
    Object.defineProperty(HTMLElement.prototype, property, {
      configurable: true,
      get: () => value,
    })
  }

  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
    width: 1280,
    height: 720,
    top: 0,
    left: 0,
    right: 1280,
    bottom: 720,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  })

  window.history.pushState({}, '', route)
  render(<App />)
  return harness
}

function isStatused(value: unknown): value is { status: number; body: unknown } {
  return typeof value === 'object' && value !== null && 'status' in value && 'body' in value
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}
