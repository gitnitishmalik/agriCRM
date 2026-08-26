/**
 * Design system reference.
 *
 * A real page, not a demo: Phase 1 builds the organisation registry against
 * these components, and this is where their behaviour is agreed before then.
 *
 * The values shown are illustrative and labelled as such. Nothing here reads
 * the API — inventing plausible farmer records to make a page look populated
 * is exactly the habit this system exists to break.
 */

import { PageHeader } from '../layout/AppShell'
import { CompletenessBar, QualityTierChip } from '../components/QualityTier'
import { FreshnessMeter } from '../components/FreshnessMeter'
import { MaskedValue } from '../components/MaskedValue'
import { TIERS, TIER_META, TARGET_DISTRIBUTION } from '../lib/quality'

export function DesignSystemPage() {
  return (
    <>
      <PageHeader
        eyebrow="Reference"
        title="Design system"
        description="Three colour families, each with exactly one job. Green is chrome and identity — the things you can do. The four tier inks are status — how much to trust what you are reading. Everything between them is warm earth neutral. No colour does two jobs."
      />

      <div className="max-w-5xl space-y-10 p-6">
        {/* -- Tiers ----------------------------------------------------- */}
        <Section
          title="Quality tiers"
          note="Doc 07 §2. Every farmer, organisation and person carries one."
        >
          <div className="overflow-hidden rounded-card border border-line bg-surface">
            <table className="w-full text-left">
              <thead className="bg-sunken">
                <tr className="border-b border-line">
                  <Th>Tier</Th>
                  <Th>What it means</Th>
                  <Th className="whitespace-nowrap">Can be messaged</Th>
                  <Th className="whitespace-nowrap">12-month target</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {TIERS.map((tier) => {
                  const [lo, hi] = TARGET_DISTRIBUTION[tier]
                  return (
                    <tr key={tier}>
                      <Td>
                        <QualityTierChip tier={tier} />
                      </Td>
                      <Td className="text-ink-2">{TIER_META[tier].meaning}</Td>
                      <Td className="text-ink-2">{TIER_META[tier].messageable ? 'Yes' : 'No'}</Td>
                      <Td className="font-mono text-ink-2">
                        {lo}–{hi}%
                      </Td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Section>

        {/* -- Freshness meter ------------------------------------------- */}
        <Section
          title="Freshness meter"
          note="Doc 07 §4. Draws decay_factor = 0.5 ^ (days ÷ half-life), marks where the record sits, and says when it drops a tier."
        >
          <div className="grid gap-3 sm:grid-cols-2">
            {[
              { tier: 'gold', days: 12, label: 'Verified recently' },
              { tier: 'gold', days: 171, label: 'About to drop' },
              { tier: 'silver', days: 402, label: 'Ageing' },
              { tier: 'bronze', days: 890, label: 'Long stale' },
              { tier: 'silver', days: null, label: 'Never verified by us' },
              { tier: 'quarantine', days: 220, label: 'Excluded' },
            ].map((example, i) => (
              <div key={i} className="card p-4">
                <div className="mb-2.5 flex items-center justify-between gap-3">
                  <span className="text-sm text-ink-2">{example.label}</span>
                  <QualityTierChip tier={example.tier as never} />
                </div>
                <FreshnessMeter
                  tier={example.tier as never}
                  daysSinceVerified={example.days}
                  fieldClass="contact"
                />
              </div>
            ))}
          </div>

          <div className="card mt-3 p-5">
            <div className="label mb-3">Panel scale — data health dashboard</div>
            <FreshnessMeter tier="gold" daysSinceVerified={140} fieldClass="contact" size="panel" />
            <p className="mt-3 max-w-xl text-sm leading-relaxed text-ink-2">
              The dotted line marks the half-life, where confidence has fallen by half. For a
              phone number that is 365 days; for a mill's crushing capacity, 1,095. The curve
              shape carries that difference without anyone reading a number.
            </p>
          </div>
        </Section>

        {/* -- Completeness ---------------------------------------------- */}
        <Section
          title="Completeness"
          note="Doc 07 §3. A weighted field checklist, 0–100. Segmented because it counts discrete fields, not a measurement."
        >
          <div className="card space-y-3 p-5">
            {[92, 70, 45, 18].map((score) => (
              <div key={score} className="flex items-center gap-4">
                <CompletenessBar score={score} />
                <span className="text-sm text-ink-2">
                  {score >= 70
                    ? 'Meets the Gold threshold'
                    : score >= 45
                      ? 'Meets the Silver threshold'
                      : 'Below Silver — Bronze at best'}
                </span>
              </div>
            ))}
          </div>
        </Section>

        {/* -- PII ------------------------------------------------------- */}
        <Section
          title="Contact details"
          note="🔴 R9 / Doc 12 §4. Masked by default. Reveal is one record at a time and writes the access log."
        >
          <div className="card space-y-4 p-5">
            <Row label="Without contact.view_full">
              <MaskedValue masked="+91 98XXX XX210" kind="phone" canReveal={false} />
            </Row>
            <Row label="With contact.view_full">
              <MaskedValue
                masked="+91 98XXX XX210"
                kind="phone"
                canReveal
                onReveal={async () => '+919876543210'}
              />
            </Row>
            <p className="border-t border-line pt-3 text-sm leading-relaxed text-ink-2">
              The component never receives the full value until a reveal is granted, so it
              cannot be read out of the page. There is no bulk variant, deliberately — bulk
              export is how contact databases leave companies.
            </p>
          </div>
        </Section>

        {/* -- Type ------------------------------------------------------ */}
        <Section
          title="Typography"
          note="IBM Plex Sans, Sans Devanagari and Mono. Doc 02 requires name_local alongside name_en, so Devanagari is a first-class script here."
        >
          <div className="card space-y-5 p-5">
            <div>
              <div className="label mb-1.5">Organisation name — as registered</div>
              <div className="text-xl text-ink">Bhainswal Kisan Producer Company Limited</div>
              <div lang="hi" className="mt-1 text-lg text-ink-2">
                भैंसवाल किसान प्रोड्यूसर कंपनी लिमिटेड
              </div>
            </div>
            <div className="border-t border-line pt-4">
              <div className="label mb-1.5">Codes and identifiers — tabular figures</div>
              <dl className="grid gap-x-8 gap-y-1.5 sm:grid-cols-2">
                {[
                  ['CIN', 'U01100UP2021PTC123456'],
                  ['Organisation', 'FPO-UP-000123'],
                  ['LGD village', '900111'],
                  ['Khasra', '247/2'],
                  ['Area', '3.5000 ha'],
                  ['Capacity', '16,000 TCD'],
                ].map(([term, value]) => (
                  <div key={term} className="flex gap-3">
                    <dt className="w-24 shrink-0 text-sm text-ink-3">{term}</dt>
                    <dd className="font-mono text-sm text-ink">{value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </div>
        </Section>

        {/* -- Actions --------------------------------------------------- */}
        <Section
          title="Actions"
          note="Green carries every affordance. Nothing green in this application is ever reporting a fact about data."
        >
          <div className="card flex flex-wrap items-center gap-4 p-5">
            <button type="button" className="btn-primary">
              Save organisation
            </button>
            <button type="button" className="btn-quiet">
              Cancel
            </button>
            <button type="button" className="btn-primary" disabled>
              Committing import
            </button>
            <a className="link text-base" href="#actions" onClick={(e) => e.preventDefault()}>
              An inline link
            </a>
          </div>
        </Section>

        {/* -- Palette --------------------------------------------------- */}
        <Section
          title="Palette"
          note="Three families, three jobs. If a colour you are adding does not belong to one of them, it does not belong."
        >
          <div className="grid gap-3 lg:grid-cols-3">
            <Swatches
              title="Green — chrome and identity"
              gloss="The rail, primary buttons, links, the mark. This is the crop: growing, cultivated, alive. It never encodes data — a green thing is a thing you can click, not a thing you can trust."
              swatches={[
                ['brand', 'bg-brand'],
                ['brand-hover', 'bg-brand-hover'],
                ['brand-soft', 'bg-brand-soft'],
                ['rail', 'bg-rail'],
                ['rail-raised', 'bg-rail-raised'],
                ['rail-ink', 'bg-rail-ink'],
              ]}
            />
            <Swatches
              title="Warm earth — every surface and letter"
              gloss="Soil, husk and unbleached paper rather than the blue-grey admin tooling defaults to. Green against grey looks like a tech product with a green logo; green against warm earth looks like farming."
              swatches={[
                ['canvas', 'bg-canvas'],
                ['surface', 'bg-surface'],
                ['sunken', 'bg-sunken'],
                ['line', 'bg-line'],
                ['ink-3', 'bg-ink-3'],
                ['ink', 'bg-ink'],
              ]}
            />
            <Swatches
              title="Tiers — status, and nothing else"
              gloss="Gold is gold and Bronze is terracotta because the chrome took the green. The tiers are now literally their metals, which is what a reader assumes on first sight anyway."
              swatches={[
                ['gold', 'bg-gold'],
                ['gold-soft', 'bg-gold-soft'],
                ['silver', 'bg-silver'],
                ['bronze', 'bg-bronze'],
                ['quarantine', 'bg-quarantine'],
                ['focus', 'bg-focus'],
              ]}
            />
          </div>

          <p className="mt-3 max-w-3xl text-sm leading-relaxed text-ink-2">
            Gold was a deep green (<code className="font-mono text-ink-3">#14654A</code>) while
            the chrome was neutral and green was free. It is not free now — a green primary
            button and a green Gold chip on one screen would teach staff that green means two
            things, and status would stop being legible. Focus stays blue, and it is the only
            blue left, so a focus ring can never be misread as brand or as a tier.
          </p>
        </Section>
      </div>
    </>
  )
}

/**
 * Palette column. The class strings are written out rather than built from
 * the token name — Tailwind reads source text, and `bg-${name}` compiles to
 * a swatch with no background at all.
 */
function Swatches({
  title,
  gloss,
  swatches,
}: {
  title: string
  gloss: string
  swatches: [string, string][]
}) {
  return (
    <div className="card p-5">
      <div className="label mb-2">{title}</div>
      <p className="mb-3.5 text-sm leading-relaxed text-ink-2">{gloss}</p>
      <div className="space-y-2">
        {swatches.map(([name, cls]) => (
          <div key={name} className="flex items-center gap-3">
            <span className={`h-6 w-6 shrink-0 rounded-chip border border-line ${cls}`} />
            <span className="font-mono text-sm text-ink-2">{name}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function Section({
  title,
  note,
  children,
}: {
  title: string
  note?: string
  children: React.ReactNode
}) {
  return (
    <section>
      <h2 className="text-lg font-semibold text-ink">{title}</h2>
      {note && <p className="mt-1 max-w-3xl text-sm leading-relaxed text-ink-2">{note}</p>}
      <div className="mt-4">{children}</div>
    </section>
  )
}

function Th({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <th className={`px-4 py-2.5 text-xs font-medium uppercase tracking-wide text-ink-3 ${className}`}>{children}</th>
}

function Td({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <td className={`px-4 py-3 align-top text-sm ${className}`}>{children}</td>
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
      <span className="w-52 shrink-0 text-sm text-ink-3">{label}</span>
      {children}
    </div>
  )
}
