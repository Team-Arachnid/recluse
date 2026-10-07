/**
 * Generate src/types/api.d.ts from the backend's OpenAPI schema.
 *
 * The frontend never hand-writes API types: hand-written ones drift from the
 * server and the drift is silent. The backend URL comes from the repo-root
 * .env, so nothing here hardcodes a port.
 *
 * Usage:
 *   npm run gen:types                                  from the running backend
 *   npm run gen:types -- --from <openapi.json>         from a schema file, such
 *                                                      as the committed contract
 *                                                      snapshot (`make openapi`)
 *
 * The committed snapshot is backend/tests/snapshots/openapi.json, and
 * src/types/contract.test.ts fails when this file is not exactly what that
 * snapshot generates.
 */
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import openapiTS, { astToString } from 'openapi-typescript'
import { loadEnv } from 'vite'

const here = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(here, '..', '..')
const outFile = resolve(here, '..', 'src', 'types', 'api.d.ts')

const banner = [
  '/**',
  ' * GENERATED FILE - do not edit.',
  ' *',
  ' * Regenerate with `make openapi` (from the committed contract snapshot) or',
  ' * `npm run gen:types` (from a running backend).',
  ' * Source: the FastAPI app OpenAPI schema.',
  ' */',
  '',
].join('\n')

const fromIndex = process.argv.indexOf('--from')
const fromFile = fromIndex === -1 ? null : resolve(process.cwd(), process.argv[fromIndex + 1])

let source
if (fromFile) {
  source = JSON.parse(await readFile(fromFile, 'utf8'))
} else {
  const env = loadEnv('development', repoRoot, '')
  const backend = env.VITE_DEV_PROXY_TARGET || 'http://127.0.0.1:8000'
  source = new URL('/openapi.json', backend)
}

try {
  const ast = await openapiTS(source)
  await mkdir(dirname(outFile), { recursive: true })
  await writeFile(outFile, banner + astToString(ast), 'utf8')
  console.log('wrote ' + outFile)
} catch (error) {
  console.error('failed to read ' + (fromFile ?? source.href))
  if (!fromFile) console.error('is the backend running? try: make dev at the repo root')
  console.error(error instanceof Error ? error.message : error)
  process.exitCode = 1
}
