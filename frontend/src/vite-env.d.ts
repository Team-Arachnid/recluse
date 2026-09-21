/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base path the typed API client prefixes onto every request. */
  readonly VITE_API_BASE_URL?: string
  /** Health poll interval in milliseconds. */
  readonly VITE_HEALTH_POLL_MS?: string
  /** Vite dev server port, also used by `vite preview`. */
  readonly VITE_DEV_SERVER_PORT?: string
  /** Interface the dev server binds to. `::` is dual-stack. */
  readonly VITE_DEV_SERVER_HOST?: string
  /** Backend origin the dev server proxies /api to. */
  readonly VITE_DEV_PROXY_TARGET?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
