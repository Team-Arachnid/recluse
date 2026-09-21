/**
 * The Phase 0 checkpoint, asserted twice.
 *
 * The first suite pins the rendering contract against a stubbed response. The
 * second talks to the real FastAPI process over HTTP, which is what actually
 * proves "live health data fetched from FastAPI" rather than asserting it.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from '@/App'
import type { HealthResponse } from '@/api/types'

// Same env var the dev proxy uses, so the test target cannot drift from
// the one the browser talks to.
const BACKEND = import.meta.env.VITE_DEV_PROXY_TARGET ?? 'http://127.0.0.1:8000'

describe('SystemHealth (stubbed backend)', () => {
  const payload: HealthResponse = {
    status: 'ok',
    model_version: 'unloaded',
    uptime_s: 12.5,
  }

  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify(payload), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('renders the three health fields returned by the API', async () => {
    render(<App />)

    expect(await screen.findByText('model_version')).toBeInTheDocument()
    expect(screen.getByText('status')).toBeInTheDocument()
    expect(screen.getByText('uptime_s')).toBeInTheDocument()
  })

  it('shows a skeleton before the first response lands', () => {
    render(<App />)

    expect(screen.getByRole('status', { name: /loading health/i })).toBeVisible()
  })

  it('requests the configured API path', async () => {
    render(<App />)
    await screen.findByText('model_version')

    const mock = vi.mocked(globalThis.fetch)
    expect(mock).toHaveBeenCalled()
    expect(String(mock.mock.calls[0]?.[0])).toBe('/api/v1/health')
  })

  it('renders no accuracy figure anywhere', () => {
    // A hero accuracy number is the exact failure mode the brief rules out:
    // on 99% benign traffic it is uninformative. Asserted, not just avoided.
    const { container } = render(<App />)

    expect(container.textContent).not.toMatch(/accura/i)
    expect(container.textContent).not.toMatch(/\d{2}\.\d%/)
  })

  it('surfaces a backend failure instead of rendering a blank panel', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('nope', { status: 503 })),
    )

    render(<App />)

    // A 5xx is retried twice with backoff before the panel gives up, so the
    // error state is expected to take a few seconds rather than appear at once.
    expect(
      await screen.findByText(/backend unreachable/i, undefined, {
        timeout: 10_000,
      }),
    ).toBeInTheDocument()
  }, 15_000)
})

// ---------------------------------------------------------------------------
// Live integration: requires `uvicorn app.main:app` to be running.
// ---------------------------------------------------------------------------
const backendIsUp = await fetch(`${BACKEND}/api/v1/health`)
  .then((response) => response.ok)
  .catch(() => false)

describe.skipIf(!backendIsUp)('SystemHealth (live FastAPI)', () => {
  beforeEach(() => {
    // jsdom resolves relative URLs against its own origin, so point them at
    // the real backend. The request itself is a genuine HTTP round trip.
    const realFetch = globalThis.fetch
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
      const url =
        typeof input === 'string' && input.startsWith('/')
          ? `${BACKEND}${input}`
          : input
      return realFetch(url, init)
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders the version and uptime reported by the live process', async () => {
    const live = (await fetch(`${BACKEND}/api/v1/health`).then((r) =>
      r.json(),
    )) as HealthResponse

    render(<App />)

    await waitFor(() =>
      expect(screen.getByText('model_version')).toBeInTheDocument(),
    )

    // The value on screen is the one the live process just reported.
    const rendered = await screen.findByText(live.model_version)
    expect(rendered).toBeInTheDocument()
    expect(screen.getByText('uptime_s')).toBeInTheDocument()
  })
})
