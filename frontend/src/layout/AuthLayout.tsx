/**
 * The two halves of every signed-out screen.
 *
 * `AuthAside` is the photograph and the claim written over it. `AuthDecor` is
 * the line art on the paper half. Both are shared by sign-in and the second
 * factor so the two screens are one place rather than two that drifted.
 *
 * The aside is `hidden lg:flex` and the photograph is a CSS background rather
 * than an `<img>`, which is the point of the choice: a `display:none` element
 * never fetches its background, so an agent signing in on a phone over a
 * village connection does not pay 120KB for a picture they will not see. An
 * `<img>` inside a hidden container downloads regardless.
 */

import { BrandLockup } from '../components/Brand'
import { SeedCluster, WheatEar, WheatStalk } from '../components/botanical'

/** The three things this system claims to know about every field it holds. */
const CLAIMS: [string, string][] = [
  ['Provenance', 'Where it came from'],
  ['Verification', 'Who confirmed it'],
  ['Freshness', 'Whether it still holds'],
]

export function AuthAside() {
  return (
    <aside className="relative hidden flex-col justify-between overflow-hidden bg-rail lg:flex">
      {/* Anchored below centre: on a short, wide viewport the crop has to
          keep the soil and the root system, not the sky. */}
      <div className="hero-field absolute inset-0 bg-cover bg-[position:50%_62%] bg-no-repeat" />

      {/* Two scrims, top and bottom, because a photograph is not a contrast
          guarantee — a graduated darkening is what turns it into one.

          Bottom: the soil under the headline already looks dark, but "looks
          dark" and "measures 14:1" are different claims. Against this scrim's
          darkest stop the headline reads 14.8:1 and the body 10.2:1.

          Top: the sky is a mid blue (#77ABE4), and mid tones are hostile to
          everything — no ink in this palette clears AA against it, light or
          dark. So the lockup gets its own ground: a soft ellipse rather than
          a full-width band, because a band deepens the whole sky and turns a
          midday field into dusk. The ellipse fades out in every direction, so
          the sky stays bright everywhere the lockup is not.

          Under it the caption measures 5.5:1 and the wordmark about 2.9:1.
          The wordmark is a logotype, which WCAG 1.4.3 exempts from contrast;
          the number matters here only because it is roughly where a copper
          mark stops looking washed out, which is the reason that counts. */}
      <div
        aria-hidden
        className="absolute inset-x-0 top-0 h-1/2"
        style={{
          background:
            'radial-gradient(ellipse 58% 62% at 50% 10%, rgba(16,26,38,0.60) 0%, rgba(16,26,38,0.42) 38%, rgba(16,26,38,0) 100%)',
        }}
      />
      <div
        aria-hidden
        className="absolute inset-x-0 bottom-0 top-1/3"
        style={{
          background:
            'linear-gradient(to top, rgba(20,13,8,0.94) 0%, rgba(20,13,8,0.88) 34%, rgba(20,13,8,0.55) 62%, rgba(20,13,8,0) 100%)',
        }}
      />

      <div className="relative z-10 flex justify-center px-12 pt-12">
        <BrandLockup on="dark" layout="stacked" size="lg" />
      </div>

      <div className="relative z-10 max-w-xl px-12 pb-12">
        <h2 className="text-3xl font-semibold leading-tight text-hero-ink">
          Every record can answer one question: how do you know?
        </h2>
        <p className="mt-4 max-w-md text-lg leading-relaxed text-hero-ink-2">
          Farmers, FPOs, cooperative societies and sugar mills — with the source, the
          verification date and the confidence behind every field.
        </p>

        <dl className="mt-8 grid grid-cols-3 gap-6 border-t border-hero-ink/25 pt-6">
          {CLAIMS.map(([term, gloss]) => (
            <div key={term}>
              <dt className="label text-hero-ink">{term}</dt>
              <dd className="mt-1 text-sm text-hero-ink-2">{gloss}</dd>
            </div>
          ))}
        </dl>
      </div>
    </aside>
  )
}

/**
 * Line art on the paper half: an ear at the top right, a stalk bottom left, a
 * few loose seeds bottom right. Faint enough to sit behind the form without
 * competing with it, and gone below `sm` where there is no room for anything
 * that is not the form.
 */
export function AuthDecor() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 hidden select-none text-ink sm:block">
      {/* Each drawing is rotated and offset so its cut ends leave the panel
          rather than stopping in mid-air. A stem that ends inside the frame
          reads as a rendering fault; the same stem running off the edge reads
          as a plant that continues past it. That is the whole placement rule
          here, and it is why the ear is turned 30° — enough for its stalk to
          exit through the right edge instead of dangling. */}
      <WheatEar className="absolute -right-10 -top-16 h-[58vh] max-h-[480px] w-auto -rotate-[26deg] opacity-[0.16]" />
      <WheatStalk className="absolute -left-8 -bottom-10 h-[50vh] max-h-[430px] w-auto -rotate-6 opacity-[0.14]" />
      <SeedCluster className="absolute bottom-14 right-8 h-24 w-auto -rotate-6 opacity-[0.18] lg:bottom-20 lg:right-14 lg:h-32" />
    </div>
  )
}

/**
 * The lockup above the form on small screens, where the aside is not there to
 * carry it. Signing in to something unbranded on a phone is disorienting in a
 * way it is not on a desktop, where the tab and the window still say what
 * this is.
 */
export function AuthMobileBrand() {
  return (
    <div className="mb-8 lg:hidden">
      <BrandLockup on="light" layout="inline" size="sm" />
    </div>
  )
}
