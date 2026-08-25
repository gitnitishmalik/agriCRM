/**
 * AgriCRM design tokens.
 *
 * The governing rule: colour means data quality and nothing else.
 *
 * All chrome — surfaces, borders, text, buttons — is neutral. The only
 * saturated colours in this application are the four quality tiers from
 * Doc 07 §2. Most CRMs use colour as decoration and as status at the same
 * time, so status stops being legible. Here, if something is coloured, it is
 * telling you how much to trust the data.
 *
 * Light theme only, committed to deliberately rather than hedged with a dark
 * variant. Data-ops staff read grids for whole shifts; one well-tuned surface
 * beats two adequate ones.
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // -- Neutral chrome ------------------------------------------------
        canvas: '#FAFAFB', // the page itself
        surface: '#FFFFFF', // cards, rows, raised things
        sunken: '#F2F3F6', // table headers, wells, inset areas
        line: '#E3E6EC', // default border
        'line-strong': '#C7CCD6', // emphasis border, dividers that matter

        ink: '#14181F', // primary text, primary buttons
        'ink-2': '#545C6B', // secondary text, labels
        'ink-3': '#868E9E', // captions, placeholders, disabled

        // -- Quality tiers: the ONLY saturated colour in the app -----------
        // Note silver is near-neutral graphite on purpose. Silver means "no
        // verification signal from us", and a desaturated colour says that
        // more honestly than a blue would.
        gold: { DEFAULT: '#14654A', soft: '#E4F1EB', line: '#B4D8CA' },
        silver: { DEFAULT: '#4A5464', soft: '#EDEFF3', line: '#CDD3DD' },
        bronze: { DEFAULT: '#8E5A14', soft: '#F7EDDB', line: '#E3CB9C' },
        quarantine: { DEFAULT: '#85292E', soft: '#F8E8E8', line: '#E4BCBD' },

        // -- System affordance, not data ----------------------------------
        // Used for focus rings and text selection only. Deliberately a blue
        // no tier uses, so a focus ring can never be read as a quality signal.
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
        card: '0 1px 2px rgba(20, 24, 31, 0.04)',
        pop: '0 4px 16px rgba(20, 24, 31, 0.10), 0 0 0 1px rgba(20, 24, 31, 0.06)',
      },

      keyframes: {
        'meter-draw': { from: { strokeDashoffset: '120' }, to: { strokeDashoffset: '0' } },
      },
      animation: { 'meter-draw': 'meter-draw 700ms ease-out both' },
    },
  },
  plugins: [],
}
