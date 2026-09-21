/**
 * Typed access to the Vite environment.
 *
 * Every value has a default here and nowhere else, so no component contains a
 * literal port or path.
 */

const DEFAULTS = {
  apiBaseUrl: '/api/v1',
  healthPollMs: 5000,
} as const

function positiveInt(raw: string | undefined, fallback: number): number {
  const parsed = Number(raw)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

export const env = {
  /** Prefix for every API request. Relative by default so the dev proxy and
   *  the production reverse proxy both work without a rebuild. */
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL ?? DEFAULTS.apiBaseUrl,
  healthPollMs: positiveInt(
    import.meta.env.VITE_HEALTH_POLL_MS,
    DEFAULTS.healthPollMs,
  ),
} as const
