import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Building2, RefreshCw, Search } from 'lucide-react'

import { api } from '../api/client'

interface Place {
  id: number
  name: string
}

interface OrganisationRow {
  id: string
  org_code: string | null
  name: string
  name_local: string | null
  type: string
  status: string
  quality_tier: string
  completeness_score?: number
  member_count: number | null
  state: Place | null
  district: Place | null
  is_deleted: boolean
}

interface OrganisationPage {
  count: number
  results: OrganisationRow[]
}

const PAGE_SIZE = 50

export function OrganisationsPage() {
  const [search, setSearch] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [offset, setOffset] = useState(0)
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) })
  if (submitted) params.set('q', submitted)

  const organisations = useQuery({
    queryKey: ['organisations', submitted, offset],
    queryFn: () => api<OrganisationPage>(`/api/v1/organisations/?${params}`),
  })

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="label">Registry</div>
          <h1 className="mt-1 text-2xl font-semibold text-ink">Organisations</h1>
          <p className="mt-1 text-sm text-ink-2">
            FPOs, cooperatives and mills imported from approved sources.
          </p>
        </div>
        <button
          type="button"
          className="btn-secondary inline-flex items-center gap-2"
          onClick={() => organisations.refetch()}
          disabled={organisations.isFetching}
        >
          <RefreshCw className={`h-4 w-4 ${organisations.isFetching ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </header>

      <section className="rounded-card border border-line bg-surface p-4">
        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            setOffset(0)
            setSubmitted(search.trim())
          }}
        >
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
            <input
              className="input w-full pl-9"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search name, code or CIN"
              aria-label="Search organisations"
            />
          </div>
          <button className="btn-primary" type="submit">Search</button>
        </form>
      </section>

      {organisations.isError ? (
        <div className="rounded-card border border-quarantine/30 bg-quarantine/5 p-5 text-sm text-quarantine">
          The organisation register could not be loaded. Confirm FastAPI is running and this
          frontend is routed to it.
        </div>
      ) : organisations.isLoading ? (
        <div className="rounded-card border border-line bg-surface p-8 text-center text-ink-2">
          Loading organisations…
        </div>
      ) : organisations.data?.results.length ? (
        <section className="overflow-hidden rounded-card border border-line bg-surface">
          <div className="flex items-center gap-3 border-b border-line px-5 py-4">
            <Building2 className="h-5 w-5 text-accent" />
            <strong className="text-ink">{organisations.data.count.toLocaleString('en-IN')}</strong>
            <span className="text-sm text-ink-2">organisations</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-sunken text-xs uppercase tracking-wide text-ink-3">
                <tr>
                  <th className="px-5 py-3">Organisation</th>
                  <th className="px-5 py-3">Type</th>
                  <th className="px-5 py-3">Location</th>
                  <th className="px-5 py-3">Quality</th>
                  <th className="px-5 py-3">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {organisations.data.results.map((row) => (
                  <tr key={row.id} className="hover:bg-sunken/60">
                    <td className="px-5 py-3">
                      <div className="font-medium text-ink">{row.name}</div>
                      <div className="mt-0.5 font-mono text-xs text-ink-3">
                        {row.org_code ?? row.id.slice(0, 8)}
                      </div>
                    </td>
                    <td className="px-5 py-3 capitalize text-ink-2">{row.type.replaceAll('_', ' ')}</td>
                    <td className="px-5 py-3 text-ink-2">
                      {[row.district?.name, row.state?.name].filter(Boolean).join(', ') || '—'}
                    </td>
                    <td className="px-5 py-3 capitalize text-ink-2">{row.quality_tier}</td>
                    <td className="px-5 py-3 capitalize text-ink-2">{row.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between border-t border-line px-5 py-3">
            <button
              className="btn-secondary"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              Previous
            </button>
            <span className="text-xs text-ink-3">
              {offset + 1}–{Math.min(offset + PAGE_SIZE, organisations.data.count)}
            </span>
            <button
              className="btn-secondary"
              disabled={offset + PAGE_SIZE >= organisations.data.count}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
            </button>
          </div>
        </section>
      ) : (
        <div className="rounded-card border border-line bg-surface p-8 text-center text-ink-2">
          No organisations match this search.
        </div>
      )}
    </div>
  )
}
