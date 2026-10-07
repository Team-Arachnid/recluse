// @vitest-environment node
/// <reference types="node" />
/**
 * The API contract, from the dashboard's side.
 *
 * `api.d.ts` must be exactly what the backend's committed OpenAPI snapshot
 * generates. The backend's own contract test pins the snapshot to the schema it
 * serves, so between the two a change to the wire format cannot land without
 * these types moving with it -- and a hand edit to the generated file, which
 * would let the dashboard believe in a field the server never sends, fails here.
 *
 *   make openapi        rewrites the snapshot and regenerates this file
 */
import { readFileSync } from 'node:fs'

import openapiTS, { astToString } from 'openapi-typescript'

const snapshot = new URL('../../../backend/tests/snapshots/openapi.json', import.meta.url)
const committedTypes = new URL('./api.d.ts', import.meta.url)

test('the generated API types are exactly what the committed schema produces', async () => {
  const schema = JSON.parse(readFileSync(snapshot, 'utf8'))
  const generated = astToString(await openapiTS(schema))
  const committed = readFileSync(committedTypes, 'utf8')

  // The committed file is a banner comment followed by the generated source.
  expect(committed.endsWith(generated)).toBe(true)
})
