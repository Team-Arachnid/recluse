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
  subtitle,
  footer,
  children,
}: {
  open: boolean
  onClose: () => void
  title: ReactNode
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
    <div className="fixed inset-0 z-40 flex justify-end">
      <div
        className="bg-background/70 absolute inset-0 backdrop-blur-[1px]"
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
          'bg-card border-border relative flex h-full w-full max-w-2xl flex-col border-l shadow-2xl',
          'motion-safe:animate-[drawer-in_160ms_ease-out] outline-none',
        )}
      >
        <header className="border-border flex items-start gap-3 border-b px-5 py-4">
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold tracking-tight">{title}</h2>
            {subtitle ? <div className="mt-1.5">{subtitle}</div> : null}
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close alert detail">
            <X aria-hidden="true" />
          </Button>
        </header>

        <div className="scrollbar-thin flex-1 overflow-y-auto">{children}</div>

        {footer ? (
          <footer className="border-border bg-card border-t px-5 py-4">{footer}</footer>
        ) : null}
      </aside>
    </div>
  )
}
