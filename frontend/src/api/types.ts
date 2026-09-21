/**
 * Concrete aliases over the generated OpenAPI types.
 *
 * Components import from here, so the generated file stays an implementation
 * detail and a schema change surfaces as a type error at the call sites.
 */
import type { components } from '@/types/api'

export type HealthResponse = components['schemas']['HealthResponse']
export type NotImplementedResponse =
  components['schemas']['NotImplementedResponse']

export type HealthStatus = HealthResponse['status']
