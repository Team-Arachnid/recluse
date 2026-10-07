/**
 * The Recluse emblem: a brown recluse, drawn as vectors so it is crisp at any
 * size and takes the brand colour from the theme.
 *
 * Decorative wherever it sits beside the word "Recluse", which is every place
 * it is used -- so it is hidden from assistive technology rather than given a
 * second, redundant name.
 */
export function RecluseLogo({ size = 32, className }: { size?: number; className?: string }) {
  const leg = {
    stroke: 'currentColor',
    strokeWidth: 2.2,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    fill: 'none',
  }

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      aria-hidden="true"
      className={className ?? 'text-[#c53a3a] shrink-0'}
    >
      {/* fangs */}
      <path
        d="M22 13C22 11.9 22.9 11 24 11C25.1 11 26 11.9 26 13V15H22V13Z"
        fill="currentColor"
      />
      <path d="M21 10L19 7M27 10L29 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      {/* cephalothorax and abdomen */}
      <circle cx="24" cy="18" r="4.2" fill="currentColor" />
      <path
        d="M24 22C21.2 22 19 25 19 29.5C19 34 22.5 38 24 39C25.5 38 29 34 29 29.5C29 25 26.8 22 24 22Z"
        fill="currentColor"
      />
      {/* the violin mark */}
      <path d="M23 26L25 26L24 28L25 30L23 30L24 28Z" fill="#000" />
      {/* eight legs */}
      <path d="M21 16L13 13L8 18" {...leg} />
      <path d="M27 16L35 13L40 18" {...leg} />
      <path d="M20 18L10 20L6 27" {...leg} />
      <path d="M28 18L38 20L42 27" {...leg} />
      <path d="M20 22L11 28L9 37" {...leg} />
      <path d="M28 22L37 28L39 37" {...leg} />
      <path d="M21 25L14 34L15 43" {...leg} />
      <path d="M27 25L34 34L33 43" {...leg} />
    </svg>
  )
}
