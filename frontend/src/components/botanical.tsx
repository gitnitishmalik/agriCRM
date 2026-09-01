/**
 * Botanical line art.
 *
 * Three drawings — an ear of wheat, a young stalk, a cluster of seeds — in a
 * single hairline weight, sized large and set very faint. They are the only
 * decoration in the application and they are confined to the pages you are
 * not signed in on. Once you are inside, the surface is a grid over a hundred
 * thousand rows and there is no such thing as a decorative pixel.
 *
 * Drawn rather than illustrated: every grain is generated from one shape and
 * one angle, so the set is internally consistent at any scale and a change to
 * the leaf curve propagates through all of it. `strokeWidth` is set on the
 * root and inherited, which keeps the hairline a hairline no matter what the
 * caller scales the SVG to — `vectorEffect` would do the opposite.
 *
 * All three are `aria-hidden`. They carry no meaning; a screen reader
 * announcing "wheat" on a sign-in form would be noise.
 */

interface DrawingProps {
  className?: string
}

const line = {
  fill: 'none' as const,
  stroke: 'currentColor',
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
}

/**
 * A single grain: a pointed almond on the stem, with the awn continuing past
 * its tip. `side` mirrors it; `y` places it up the ear.
 */
function Grain({ y, side, length = 26 }: { y: number; side: 1 | -1; length?: number }) {
  const tipX = side * length
  const tipY = y - length * 1.15

  return (
    <g transform={`translate(60 0)`}>
      <path
        d={`M0 ${y}
            C ${side * length * 0.85} ${y - length * 0.15},
              ${tipX + side * 2} ${tipY + length * 0.45},
              ${tipX} ${tipY}
            C ${tipX - side * 4} ${tipY + length * 0.55},
              ${side * length * 0.22} ${y - length * 0.55},
              0 ${y} Z`}
        {...line}
      />
      {/* The awn: the bristle every wheat grain carries. Without them the ear
          reads as a fir cone. */}
      <path
        d={`M${tipX} ${tipY} C ${tipX + side * 6} ${tipY - length * 0.5},
            ${tipX + side * 8} ${tipY - length * 0.9},
            ${tipX + side * 7} ${tipY - length * 1.35}`}
        {...line}
      />
    </g>
  )
}

/** An ear of wheat, upright. Nine ranks of paired grains and a terminal one. */
export function WheatEar({ className }: DrawingProps) {
  const ranks = Array.from({ length: 9 }, (_, i) => 70 + i * 26)

  return (
    <svg
      viewBox="0 0 120 420"
      aria-hidden
      className={className}
      strokeWidth={1.3}
      preserveAspectRatio="xMidYMid meet"
    >
      <path d="M60 420C60 380 60 340 60 300" {...line} />
      {ranks.map((y) => (
        <g key={y}>
          <Grain y={y} side={1} />
          <Grain y={y} side={-1} />
        </g>
      ))}
      {/* Terminal grain, narrower than the ranks below it — an ear tapers. */}
      <path
        d="M60 76C50 62 50 40 60 22C70 40 70 62 60 76Z"
        {...line}
      />
      <path d="M60 22C60 14 60 8 60 2" {...line} />
    </svg>
  )
}

/**
 * A young stalk: two leaves off a stem, the plant before it heads. Paired with
 * the ear it makes the same point the freshness meter makes — the same thing
 * at two moments in its life.
 */
export function WheatStalk({ className }: DrawingProps) {
  return (
    <svg
      viewBox="0 0 200 420"
      aria-hidden
      className={className}
      strokeWidth={1.3}
      preserveAspectRatio="xMidYMid meet"
    >
      <path d="M104 420C96 350 92 270 100 186" {...line} />

      {/* Leaves. Each is a closed blade with a midrib, because an unribbed
          outline at this scale reads as a bag rather than as a leaf. */}
      <path d="M100 232C58 226 34 196 32 150C76 158 98 186 100 232Z" {...line} />
      <path d="M40 158C62 176 82 200 98 230" {...line} strokeWidth={0.9} />

      <path d="M102 200C144 190 166 156 164 108C122 120 102 152 102 200Z" {...line} />
      <path d="M156 118C134 138 116 164 103 197" {...line} strokeWidth={0.9} />

      <path d="M100 300C66 296 46 272 44 234C80 242 98 264 100 300Z" {...line} />
      <path d="M52 242C70 258 86 278 99 298" {...line} strokeWidth={0.9} />

      {/* The shoot the leaves come off, tapering to nothing. */}
      <path d="M100 186C100 150 102 118 108 92" {...line} />
    </svg>
  )
}

/** Four seeds, loose, as they fall. */
export function SeedCluster({ className }: DrawingProps) {
  const seeds = [
    { x: 24, y: 66, r: -32 },
    { x: 74, y: 40, r: -8 },
    { x: 116, y: 78, r: 24 },
    { x: 70, y: 108, r: 6 },
  ]

  return (
    <svg
      viewBox="0 0 160 150"
      aria-hidden
      className={className}
      strokeWidth={1.3}
      preserveAspectRatio="xMidYMid meet"
    >
      {seeds.map(({ x, y, r }) => (
        <g key={`${x}-${y}`} transform={`translate(${x} ${y}) rotate(${r})`}>
          <path d="M0 -26C13 -18 13 18 0 26C-13 18 -13 -18 0 -26Z" {...line} />
          <path d="M0 -20C0 -8 0 10 0 20" {...line} strokeWidth={0.8} />
        </g>
      ))}
    </svg>
  )
}
