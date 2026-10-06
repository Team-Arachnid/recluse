import { useEffect, useState } from 'react'

/**
 * A value that settles after `delay` of quiet.
 *
 * Used by the threshold slider: the line follows the pointer from the immediate
 * value while only the settled one is sent to the server, so a two-second drag
 * costs about six requests instead of a hundred that arrive out of order.
 */
export function useDebounced<T>(value: T, delay: number): T {
  const [settled, setSettled] = useState(value)

  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])

  return settled
}

/**
 * The rendered width of an element.
 *
 * Needed by the charts that compute their own geometry: a histogram whose bars
 * are positioned in pixels cannot be laid out by CSS alone, and scaling an SVG
 * to fit instead would stretch the text and the stroke widths with it.
 */
export function useElementWidth<T extends HTMLElement>(): [(node: T | null) => void, number] {
  const [element, setElement] = useState<T | null>(null)
  const [width, setWidth] = useState(0)

  useEffect(() => {
    if (!element) return

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (entry) setWidth(entry.contentRect.width)
    })
    observer.observe(element)
    setWidth(element.getBoundingClientRect().width)

    return () => observer.disconnect()
  }, [element])

  // A callback ref rather than a RefObject: the effect has to re-run when the
  // node arrives, and a ref object's mutation does not trigger that.
  return [setElement, width]
}
