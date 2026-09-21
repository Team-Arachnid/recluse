/**
 * Generate src/types/api.d.ts from the running backend OpenAPI schema.
 *
 * The frontend never hand-writes API types: BUILD_PROMPT.md Part 9 is explicit
 * that hand-written types drift from the server. The backend URL comes from the
 * repo-root .env, so nothing here hardcodes a port.
 *
 * Usage: npm run gen:types   (backend must be running)
 */
import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import openapiTS, { astToString } from 'openapi-typescript'
import { loadEnv } from 'vite'

const here = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(here, '..', '..')
const outFile = resolve(here, '..', 'src', 'types', 'api.d.ts')

const env = loadEnv('development', repoRoot, '')
const backend = env.VITE_DEV_PROXY_TARGET || 'http://127.0.0.1:8000'
const schemaUrl = new URL('/openapi.json', backend)

const banner = [
  '/**',
  ' * GENERATED FILE - do not edit.',
  ' *',
  ' * Regenerate with `npm run gen:types` while the backend is running.',
  ' * Source: the FastAPI app OpenAPI schema.',
  ' */',
  '',
].join('\n')

try {
  const ast = await openapiTS(schemaUrl)
  await mkdir(dirname(outFile), { recursive: true })
  await writeFile(outFile, banner + astToString(ast), 'utf8')
  console.log('wrote ' + outFile)
} catch (error) {
  console.error('failed to read ' + schemaUrl.href)
  console.error('is the backend running? try: make dev at the repo root')
  console.error(error instanceof Error ? error.message : error)
  process.exitCode = 1
}
