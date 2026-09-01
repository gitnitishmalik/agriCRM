/**
 * The brand lockup: mark, wordmark, and the two together.
 *
 * The mark is a theta — Theta Analytics — with a sprig growing out of its
 * left side. Theta is the parent company and the sprig is what this product
 * is about, so the mark says both without a metaphor in between. It replaces
 * the decay curve, which said something true about freshness but nothing
 * about who made this or what it is for; the curve stays where it belongs, on
 * the freshness meter, where it is instrumentation rather than identity.
 *
 * Copper is a fourth colour family and it has exactly one job: the lockup.
 * It appears on the mark and the wordmark and nowhere else — not on a chip,
 * a meter, a row stroke or a button. That restriction matters because copper
 * is adjacent to the bronze tier ink. They never meet on a surface, and a
 * bronze chip always carries the word "Bronze", so the label disambiguates
 * even if the hue does not.
 *
 * Two inks, not one: `copper` (5.1:1 on canvas) for paper, `copper-light`
 * (4.9:1 on the rail) for the dark rail and the photograph. A single value
 * cannot clear AA against both grounds.
 */

interface MarkProps {
  className?: string
  /** Ground the mark sits on. Decides which copper carries enough contrast. */
  on?: 'light' | 'dark'
}

/**
 * The mark alone. Square, so it drops into a favicon, an avatar slot or a
 * rail header without a wrapper doing the centring.
 */
export function BrandMark({ className = 'h-8 w-8', on = 'light' }: MarkProps) {
  const ink = on === 'dark' ? 'text-copper-light' : 'text-copper'

  return (
    <svg
      viewBox="0 0 48 48"
      aria-hidden
      className={`${ink} ${className}`}
      fill="none"
      stroke="currentColor"
      strokeWidth={2.2}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {/* Theta: the ring and its bar. The bar runs nearly the full width of
          the ring, which is what makes it a letter — inset any further and it
          reads as a prohibition sign rather than a theta. */}
      <circle cx="31" cy="29" r="11.6" />
      <path d="M20.6 29h20.8" />

      {/* The sprig: a stem up the left with two leaves off it. The stem stops
          short of the ring instead of crossing it — running the two together
          left a tail that read as a stray stroke at small sizes.

          Two leaves, not three. Three fit at 220px and turned to mush at 16,
          and 16px is the size this mark spends most of its life at. */}
      <path d="M19.9 36.4C15.2 29.4 12.7 20.2 13.4 8.5" />
      <path d="M15.1 17.2C9.3 15.8 6.5 11.2 7.7 5.2c5.8 1.4 8.4 5.8 7.4 12Z" />
      <path d="M17 28.4C11.2 27.8 8.2 23.6 8.8 17.6c5.8.8 8.6 4.8 8.2 10.8Z" />
    </svg>
  )
}

/**
 * The wordmark. IBM Plex Serif against the Plex Sans the rest of the app is
 * set in — the same superfamily, so the lockup is a different voice rather
 * than a different family. A serif is doing real work here: it is the one
 * place the product is allowed to look like it has a history, and it makes
 * the lockup unmistakably not a UI label.
 */
export function BrandWordmark({
  on = 'light',
  size = 'md',
}: {
  on?: 'light' | 'dark'
  size?: 'sm' | 'md' | 'lg'
}) {
  // `lg` reaches past the type scale on purpose. That scale tops out at 2rem
  // because it is sized for a CRM's 30-column grids, and a wordmark is not a
  // heading — it is the one piece of type on the sign-in screen whose job is
  // to be seen from across a desk.
  const scale = {
    sm: ['text-lg', 'text-2xs'],
    md: ['text-2xl', 'text-xs'],
    lg: ['text-[2.75rem]', 'text-xs'],
  }[size]

  return (
    <div className="min-w-0">
      <div
        className={`font-serif font-semibold leading-none tracking-tight ${scale[0]} ${
          on === 'dark' ? 'text-copper-light' : 'text-copper'
        }`}
      >
        AgriCRM
      </div>
      <div
        className={`mt-1 uppercase leading-none tracking-[0.22em] ${scale[1]} ${
          // The line under the wordmark is a caption, not a logotype, so it
          // has to clear AA on its own. Copper does not at this size, and on
          // dark grounds the warm cream beats the rail's green-grey — that
          // ink is tuned for a green background and turns sickly over the
          // blue of the photograph's sky.
          on === 'dark' ? 'text-hero-ink' : 'text-ink-2'
        }`}
      >
        Theta Analytics
      </div>
    </div>
  )
}

/**
 * Mark and wordmark together. `stacked` is the sign-in treatment — centred
 * over the photograph, where there is room for the lockup to be the first
 * thing anyone sees. `inline` is everywhere else.
 */
export function BrandLockup({
  on = 'light',
  layout = 'inline',
  size = 'md',
}: {
  on?: 'light' | 'dark'
  layout?: 'inline' | 'stacked'
  size?: 'sm' | 'md' | 'lg'
}) {
  const markSize = { sm: 'h-7 w-7', md: 'h-9 w-9', lg: 'h-[4.5rem] w-[4.5rem]' }[size]

  if (layout === 'stacked') {
    return (
      <div className="flex flex-col items-center text-center">
        <BrandMark on={on} className={markSize} />
        <div className="mt-2">
          <BrandWordmark on={on} size={size} />
        </div>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-2.5">
      <BrandMark on={on} className={`shrink-0 ${markSize}`} />
      <BrandWordmark on={on} size={size} />
    </div>
  )
}
