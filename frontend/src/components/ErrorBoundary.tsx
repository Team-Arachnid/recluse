import { AlertTriangle } from 'lucide-react'
import { Component, type ErrorInfo, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'

interface Props {
  children: ReactNode
  /** Named in the fallback so a failure points at the screen that caused it. */
  label?: string
}

interface State {
  error: Error | null
}

/**
 * Error boundary for a screen.
 *
 * Every screen gets one: a dashboard that renders a blank page on a render
 * error looks broken in exactly the way a monitoring tool must not.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('render failed', error, info.componentStack)
  }

  private reset = () => this.setState({ error: null })

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <Card className="border-[color-mix(in_oklab,var(--critical)_40%,transparent)]">
        <CardContent className="flex items-start gap-3 pt-5">
          <AlertTriangle
            className="mt-0.5 size-4 shrink-0 text-[var(--critical)]"
            aria-hidden="true"
          />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold">
              {this.props.label ?? 'This panel'} failed to render
            </p>
            <p className="text-muted-foreground mt-1 font-mono text-xs break-words">
              {error.message}
            </p>
            <Button
              variant="outline"
              size="sm"
              className="mt-3"
              onClick={this.reset}
            >
              Try again
            </Button>
          </div>
        </CardContent>
      </Card>
    )
  }
}
