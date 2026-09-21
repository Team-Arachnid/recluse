/// <reference types="vitest/config" />
import { fileURLToPath } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

const here = fileURLToPath(new URL('.', import.meta.url))
const repoRoot = fileURLToPath(new URL('..', import.meta.url))

/**
 * Ports and the API target come from the repo-root `.env` (see `.env.example`),
 * the same file the backend reads. `envDir` is pointed at the repo root so
 * there is one env file for the whole stack rather than two that drift.
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, repoRoot, '')

  const devPort = Number(env.VITE_DEV_SERVER_PORT || 5173)
  const proxyTarget = env.VITE_DEV_PROXY_TARGET || 'http://127.0.0.1:8000'
  // On Windows, Node resolves the default host to ::1 only, so 127.0.0.1
  // refuses connections. Binding both stacks avoids a confusing "works in the
  // browser, refused by curl". Containers override this with 0.0.0.0.
  const devHost = env.VITE_DEV_SERVER_HOST || '::'

  return {
    envDir: repoRoot,
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    server: {
      host: devHost,
      port: devPort,
      // Fail loudly rather than silently moving to another port, which would
      // leave the documented URL pointing at nothing.
      strictPort: true,
      proxy: {
        // Same-origin in dev, so the browser never needs CORS.
        '/api': { target: proxyTarget, changeOrigin: true },
      },
    },
    preview: { host: devHost, port: devPort, strictPort: true },
    build: { outDir: 'dist', sourcemap: mode !== 'production' },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: [`${here}src/test/setup.ts`],
      include: ['src/**/*.{test,spec}.{ts,tsx}'],
    },
  }
})
