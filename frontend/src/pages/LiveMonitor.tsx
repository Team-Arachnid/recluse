/**
 * Screen 3 — the live traffic monitor.
 *
 * The threshold histogram leads, because it is the one element on any screen
 * here that teaches something rather than reporting it: drag the line and the
 * precision/recall tradeoff stops being a sentence in a README and becomes a
 * number that moves. Everything else on this screen is instrumentation around
 * it.
 */
import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

import { AlertDetailDrawer } from '@/components/alert/AlertDetailDrawer'
import { ScreenBody } from '@/components/AppShell'
import { CaptureControls } from '@/components/live/CaptureControls'
import { FlowTicker, RatePanels } from '@/components/live/FlowTicker'
import { ReplayControls } from '@/components/live/ReplayControls'
import { ThresholdHistogram } from '@/components/live/ThresholdHistogram'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

export function LiveMonitor() {
  const [params, setParams] = useSearchParams()
  const openId = params.get('alert')

  const open = useCallback(
    (id: number) => {
      const next = new URLSearchParams(params)
      next.set('alert', String(id))
      setParams(next, { replace: true })
    },
    [params, setParams],
  )

  const close = useCallback(() => {
    const next = new URLSearchParams(params)
    next.delete('alert')
    setParams(next, { replace: true })
  }, [params, setParams])

  return (
    <ScreenBody
      title="Live traffic"
      lede="A replay streams held-out flows, and a live capture streams your own network's, through the same features, the same models and the same alert pipeline. Nothing here is a second code path."
    >
      <div className="space-y-6">
        <ReplayControls />

        <ErrorBoundary label="Live capture">
          <CaptureControls />
        </ErrorBoundary>

        <ErrorBoundary label="Threshold histogram">
          <Card>
            <CardContent className="pt-5">
              <ThresholdHistogram />
            </CardContent>
          </Card>
        </ErrorBoundary>

        <ErrorBoundary label="Rate sparklines">
          <RatePanels />
        </ErrorBoundary>

        <ErrorBoundary label="Flow ticker">
          <Card>
            <CardHeader>
              <CardTitle>Alerts as they arrive</CardTitle>
            </CardHeader>
            <CardContent className="px-0 pb-0">
              <FlowTicker onOpen={open} />
            </CardContent>
          </Card>
        </ErrorBoundary>
      </div>

      <AlertDetailDrawer
        alertId={openId ? Number(openId) : null}
        onClose={close}
        onOpen={open}
      />
    </ScreenBody>
  )
}
