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

**Colour means data quality and nothing else.** Chrome — surfaces, borders,
text, buttons — is neutral throughout. The only saturated colours in the
application are the four quality tiers from Doc 07 §2: gold, silver, bronze,
quarantine. If something on screen is coloured, it is telling you how much to
trust what you are reading.

This is why the primary button is ink rather than a brand colour, and why the
focus ring is a blue no tier uses.

See `/design` in the running app for the full reference.

## Layout

```
src/
├── api/         client (auth, refresh), generated types, queries
├── components/  QualityTier, FreshnessMeter, MaskedValue
├── layout/      AppShell, PageHeader
├── lib/         quality.ts — tiers, completeness, the decay function
└── pages/       Login, Mfa, Overview, Account, DesignSystem
```

`lib/quality.ts` mirrors Doc 07. Those numbers are the specification's, and the
backend computes the same values from the same rules — if the two disagree, the
backend is right and the frontend is the bug.
