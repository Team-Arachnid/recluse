import '@testing-library/jest-dom/vitest'

import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(cleanup)

/*
 * jsdom has no EventSource. The stream provider only opens one once a traffic
 * source reports itself running -- which a *live* backend can do mid-test, if
 * a replay happens to be running while the suite runs against it. A no-op
 * stand-in keeps that from surfacing as an unhandled ReferenceError; the
 * screen tests that need a stream behaviour stub their own.
 */
if (!('EventSource' in globalThis)) {
  class NoopEventSource {
    addEventListener() {}
    removeEventListener() {}
    close() {}
  }
  Object.defineProperty(globalThis, 'EventSource', { value: NoopEventSource, writable: true })
}
