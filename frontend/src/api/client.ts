import { env } from '@/lib/env'

/** A non-2xx response, carrying enough context to render something useful. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly url: string,
    message: string,
    readonly body?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }

  /**
   * True when the endpoint exists but its phase has not been built yet.
   *
   * The backend answers 501 for the routes later phases fill in, so the UI can
   * say "not built yet" instead of showing a generic failure.
   */
  get isNotImplemented(): boolean {
    return this.status === 501
  }
}

async function readBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get('content-type') ?? ''
  try {
    return contentType.includes('application/json')
      ? await response.json()
      : await response.text()
  } catch {
    return undefined
  }
}

function messageFrom(body: unknown, response: Response): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
  }
  return `${response.status} ${response.statusText}`
}

/**
 * Fetch JSON from the API.
 *
 * `path` is relative to `VITE_API_BASE_URL` (default `/api/v1`), which is a
 * relative URL by default so the dev proxy and any production reverse proxy
 * both work without rebuilding the bundle.
 */
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
