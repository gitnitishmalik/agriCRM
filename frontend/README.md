# AgriCRM — frontend

React 19 + TypeScript + Vite + TanStack Query + Tailwind.

```bash
npm install
npm run dev      # http://localhost:5173, proxies /api to :8000
```

Run the backend first (`make run` from the repo root), or point the proxy
elsewhere with `VITE_DEV_API`.

## Scripts

| | |
|---|---|
| `npm run dev` | Dev server with API proxy |
| `npm run build` | Typecheck, then production build |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run api:types` | Regenerate `src/api/schema.d.ts` from `../openapi.yaml` |

🔴 `src/api/schema.d.ts` is generated, not written. When the backend's API
changes, run `make schema-doc` at the repo root, then `npm run api:types`, and
commit both. CI fails if they drift — a stale client means the frontend is
compiling against an API that no longer exists.

## The design rule

**Four colour families, four jobs.** A colour that does not belong to one of
them does not belong.

| Family | Job | Where |
|---|---|---|
| Brand green | Affordance — things you can *do* | Rail, primary buttons, links, active states |
| Warm earth | Every surface and every letter | Canvas, cards, borders, all text |
| Quality tiers | Status — things you can *trust* | Tier chips, meters, tier-keyed strokes |
| Copper | The lockup, and nothing else | The theta mark and the AgriCRM wordmark |

The separation is the point: green is never a data signal and a tier ink is
never a control, so staff never have to ask which of the two a colour means.
Focus stays blue and is the only blue left, so a focus ring cannot be misread
as either. Copper sits next to bronze in hue, which is safe only because the
two never share a surface — keep it off chips, meters, strokes and buttons.

Light theme only, deliberately. Data-ops staff read grids for whole shifts;
one well-tuned surface beats two adequate ones.

See `/design` in the running app for the full reference, and the long-form
reasoning in `tailwind.config.js`.

## The signed-out screens

`/login` and `/mfa` share `layout/AuthLayout.tsx`: a photograph on the left
with the product's claim over it, the form on paper to the right.

- The photograph is a **CSS background on a `hidden lg:flex` element**, not an
  `<img>`. A hidden element never fetches its background, so an agent signing
  in on a phone does not pay ~120KB for a picture they will not see. An `<img>`
  inside a hidden container downloads anyway.
- Both scrims are load-bearing, not decoration. A photograph is not a contrast
  guarantee; the gradients are what turn it into one. Numbers are in the
  component.
- `components/botanical.tsx` is the **only decoration in the product**, and it
  stops at the sign-in boundary. Past here every surface is a grid over a
  hundred thousand rows and there is no such thing as a decorative pixel.

## Layout

```
src/
├── api/         client (auth, refresh), generated types, queries
├── components/  Brand, botanical, QualityTier, FreshnessMeter, MaskedValue
├── layout/      AppShell + PageHeader, AuthLayout (the signed-out halves)
├── lib/         quality.ts — tiers, completeness, the decay function
└── pages/       Login, Mfa, Overview, Account, DesignSystem
```

`lib/quality.ts` mirrors Doc 07. Those numbers are the specification's, and the
backend computes the same values from the same rules — if the two disagree, the
backend is right and the frontend is the bug.
