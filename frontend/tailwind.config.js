/**
 * AgriCRM design tokens — agricultural farming theme.
 *
 * Four colour families, each with exactly one job. If you are adding a
 * colour and it does not belong to one of these four, it does not belong.
 *
 *   1. BRAND GREEN — chrome and identity. The rail, primary actions, links,
 *      active states, the mark. This is the crop: growing, cultivated, alive.
 *      It never encodes data. A green thing on screen is a thing you can
 *      click, not a thing you can trust.
 *
 *   2. WARM EARTH NEUTRALS — every surface, border and letter of text. Soil,
 *      husk and unbleached paper rather than the blue-grey admin tooling
 *      defaults to. Green against grey looks like a tech product with a green
 *      logo; green against warm earth looks like farming.
 *
 *   3. QUALITY TIERS — status, and nothing else. Four inks from Doc 07 §2,
 *      used only on tier chips, meters and tier-keyed strokes.
 *
 *   4. COPPER — the brand lockup, and nothing else. The theta mark and the
 *      AgriCRM wordmark. It is the one colour on screen that is allowed to
 *      mean "this product" rather than "this control" or "this status", and
 *      it earns that by never appearing anywhere a user can click or read a
 *      value. See the note beside the token.
 *
 * On the tier re-key: Gold used to be a deep green (#14654A) back when the
 * chrome was neutral and green was free. It is not free any more — a green
 * primary button and a green Gold chip on the same screen would teach staff
 * that green means two different things, and status would stop being legible.
 * So Gold moved to actual gold and Bronze to terracotta. The tiers are now
 * literally their metals, which is what a reader assumes on first sight
 * anyway, and green belongs unambiguously to the interface.
 *
 * Silver stays near-neutral graphite on purpose. Silver means "no
 * verification signal from us", and a desaturated colour says that more
 * honestly than a blue would. Quarantine stays red for the obvious reason.
 *
 * Light theme only, committed to deliberately rather than hedged with a dark
 * variant. Data-ops staff read grids for whole shifts; one well-tuned surface
 * beats two adequate ones. The rail is the single dark region, which is what
 * gives the theme its weight without darkening the reading surface.
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // -- 1. Brand green: chrome and identity, never data ---------------
        brand: {
          DEFAULT: '#2F6B3C', // primary buttons, links, active accents
          hover: '#25562F', // pressed / hover on primary
          soft: '#E9F1E6', // subtle wash, selected rows, quiet green fills
          line: '#C2D6BC', // borders on green-tinted surfaces
        },

        // The rail is the one dark region in the application. Its own scale,
        // because text on deep green needs values that have nothing to do
        // with the ink scale used on paper.
        rail: {
          DEFAULT: '#1B3A24', // rail background — turned field
          raised: '#24482E', // hover
          line: '#2F5638', // dividers inside the rail
          ink: '#EDF2EA', // primary text on rail (11:1)
          'ink-2': '#A7BCA6', // secondary text on rail (6.2:1)
        },

        // -- 2. Warm earth neutrals: every surface and every letter --------
        canvas: '#FAF8F4', // the page itself — unbleached paper
        surface: '#FFFFFF', // cards, rows, raised things
        sunken: '#F2EFE8', // table headers, wells, inset areas — husk
        line: '#E4DFD4', // default border
        'line-strong': '#C9C2B2', // emphasis border, dividers that matter

        ink: '#1C1A15', // primary text — soil black
        'ink-2': '#5C574B', // secondary text, labels
        'ink-3': '#837C6B', // captions, placeholders, disabled

        // Text over the sign-in photograph. Warmer than the rail inks, which
        // are tuned for a green ground and go slightly cold over soil. Both
        // are checked against the scrim's darkest stop rather than against
        // the photograph, because a photograph is not a contrast guarantee:
        // 14.8:1 and 10.2:1 on rgba(20,13,8,0.94).
        hero: { ink: '#F5EEE4', 'ink-2': '#D3C7B5' },

        // -- 3. Quality tiers: status only --------------------------------
        // Contrast of each ink on its own soft fill, checked: gold 5.4:1,
        // silver 7.1:1, bronze 5.8:1, quarantine 6.9:1. All clear AA at the
        // small uppercase size the chips actually render at.
        gold: { DEFAULT: '#7E5E00', soft: '#FBF1D8', line: '#E8D49A' },
        silver: { DEFAULT: '#4A5464', soft: '#EDEFF3', line: '#CDD3DD' },
        bronze: { DEFAULT: '#8E4420', soft: '#F7E7DC', line: '#E5C3AB' },
        quarantine: { DEFAULT: '#85292E', soft: '#F8E8E8', line: '#E4BCBD' },

        // -- 4. Copper: the brand lockup, and nothing else -----------------
        // The mark and the wordmark. Never a chip, a meter, a stroke or a
        // button. It sits next to the bronze tier ink in hue, which is only
        // safe because the two never appear on the same surface and a bronze
        // chip always carries the word "Bronze" beside its colour.
        //
        // Two values because one cannot clear AA on both grounds: `DEFAULT`
        // is 5.1:1 on canvas, `light` is 4.9:1 on the rail and on the
        // photograph's soil.
        copper: { DEFAULT: '#9C5A29', light: '#C89A6B' },

        // -- System affordance, not data ----------------------------------
        // Focus stays blue. It is now the only blue in the application, so a
        // focus ring cannot be read as brand green or as any tier.
        focus: '#1D4ED8',
      },

      fontFamily: {
        // One superfamily, three jobs. IBM Plex Sans Devanagari is not a
        // convenience pick — Doc 02 requires name_local alongside name_en for
        // every person and organisation, so Devanagari is a first-class script
        // here, not a fallback. Plex Mono carries CIN, LGD, khasra and phone
        // numbers, which need real tabular figures.
        sans: ['"IBM Plex Sans"', 'system-ui', 'sans-serif'],
        deva: ['"IBM Plex Sans Devanagari"', '"IBM Plex Sans"', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'monospace'],
        // A fourth role, still the same superfamily. It sets the wordmark and
        // nothing else — the one piece of type that is the product naming
        // itself rather than the interface labelling something. One weight,
        // never used for UI text.
        serif: ['"IBM Plex Serif"', 'Georgia', 'serif'],
      },

      // Tighter than a marketing scale. A CRM's base is 14px, not 16 —
      // these screens carry 30-column grids.
      fontSize: {
        '2xs': ['0.625rem', { lineHeight: '0.875rem', letterSpacing: '0.06em' }],
        xs: ['0.6875rem', { lineHeight: '1rem', letterSpacing: '0.04em' }],
        sm: ['0.78125rem', { lineHeight: '1.125rem' }],
        base: ['0.875rem', { lineHeight: '1.375rem' }],
        lg: ['0.9375rem', { lineHeight: '1.5rem' }],
        xl: ['1.125rem', { lineHeight: '1.625rem', letterSpacing: '-0.01em' }],
        '2xl': ['1.5rem', { lineHeight: '1.875rem', letterSpacing: '-0.018em' }],
        '3xl': ['2rem', { lineHeight: '2.25rem', letterSpacing: '-0.024em' }],
      },

      borderRadius: { card: '0.375rem', chip: '0.1875rem' },

      boxShadow: {
        // Restrained. Elevation is a border in this system, not a glow.
        card: '0 1px 2px rgba(28, 26, 21, 0.04)',
        pop: '0 4px 16px rgba(28, 26, 21, 0.10), 0 0 0 1px rgba(28, 26, 21, 0.06)',
      },

      keyframes: {
        'meter-draw': { from: { strokeDashoffset: '120' }, to: { strokeDashoffset: '0' } },
      },
      animation: { 'meter-draw': 'meter-draw 700ms ease-out both' },
    },
  },
  plugins: [],
}
