import { X } from 'lucide-react'
import { useEffect, useRef, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

/**
 * A right-hand side drawer.
 *
 * A drawer and not a route, because the queue behind it must never be lost: an
 * analyst working a shift opens a row, judges it, and goes straight to the next
 * one. A route change would scroll them back to the top of the queue every
 * time and make them re-find their place after every alert.
 *
 * Focus moves into the panel on open and back to whatever opened it on close,
 * so the keyboard path through the queue survives a judgement. Escape and the
 * backdrop both close.
 */
export function Drawer({
  open,
  onClose,
  title,
  eyebrow,
  subtitle,
  footer,
  children,
}: {
  open: boolean
  onClose: () => void
  title: ReactNode
  /** A small monospaced line above the title -- an id, a timestamp. */
  eyebrow?: ReactNode
  subtitle?: ReactNode
  footer?: ReactNode
  children: ReactNode
}) {
  const panel = useRef<HTMLElement>(null)
  const returnFocusTo = useRef<Element | null>(null)

  useEffect(() => {
    if (!open) return

    returnFocusTo.current = document.activeElement
    panel.current?.focus()

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)

    return () => {
      document.removeEventListener('keydown', onKeyDown)
      const previous = returnFocusTo.current
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus()
    }
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-[1px]"
        onClick={onClose}
        aria-hidden="true"
      />

      <aside
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === 'string' ? title : 'Alert detail'}
        tabIndex={-1}
        className={cn(
          'bg-card border-border relative flex h-full w-full max-w-3xl flex-col border-l shadow-2xl shadow-black/60',
          'motion-safe:animate-[drawer-in_180ms_ease-out] outline-none',
        )}
      >
        <header className="border-border bg-muted flex items-start gap-3 border-b px-5 py-4 sm:px-6">
          <span aria-hidden="true" className="bg-brand mt-1 h-5 w-1.5 shrink-0 rounded-full" />
          <div className="min-w-0 flex-1">
            {eyebrow ? (
              <div className="text-subtle-foreground mb-0.5 font-mono text-[11px]">{eyebrow}</div>
            ) : null}
            <h2 className="text-foreground-strong text-base font-semibold tracking-tight sm:text-lg">
              {title}
            </h2>
            {subtitle ? <div className="mt-2">{subtitle}</div> : null}
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close alert detail">
            <X className="size-4" aria-hidden="true" />
          </Button>
        </header>

        <div className="scrollbar-thin flex-1 overflow-y-auto">{children}</div>

        {footer ? (
          <footer className="border-border bg-muted border-t px-5 py-4 sm:px-6">{footer}</footer>
        ) : null}
      </aside>
    </div>
  )
}
