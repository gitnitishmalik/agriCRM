import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, Search, Sprout, Upload } from 'lucide-react'

import { api } from '../api/client'

interface StateRow { id: number; name: string }
interface Place { id: number; name: string }
interface FarmerRow {
  id: string; state_id: number; farmer_code: string | null; first_name: string;
  last_name: string | null; farmer_class: string; total_area_ha: string | null;
  quality_tier: string; district: Place | null; state: Place;
}
interface FarmerPage { count: number; results: FarmerRow[] }
interface ImportResult { created: number; skipped: number; errors: string[] }

const PAGE_SIZE = 50

export function FarmersPage() {
  const queryClient = useQueryClient()
  const [stateId, setStateId] = useState('')
  const [search, setSearch] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [offset, setOffset] = useState(0)
  const [sourceCode, setSourceCode] = useState('theta_analytics')
  const [file, setFile] = useState<File | null>(null)

  const states = useQuery({ queryKey: ['states'], queryFn: () => api<StateRow[]>('/api/v1/states/') })
  const params = new URLSearchParams({ state: stateId, limit: String(PAGE_SIZE), offset: String(offset) })
  if (submitted) params.set('q', submitted)
  const farmers = useQuery({
    queryKey: ['farmers', stateId, submitted, offset],
    queryFn: () => api<FarmerPage>(`/api/v1/farmers/?${params}`),
    enabled: Boolean(stateId),
  })
  const importer = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error('Choose a CSV file.')
      const form = new FormData()
      form.set('source_code', sourceCode)
      form.set('file', file)
      return api<ImportResult>('/api/v1/farmers/import-csv/', { method: 'POST', body: form })
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['farmers'] }),
  })

  return (
    <div className="space-y-6 p-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="label">Personal-data registry</div>
          <h1 className="mt-1 text-2xl font-semibold text-ink">Farmers</h1>
          <p className="mt-1 text-sm text-ink-2">State-scoped farmer master. Website scrapers cannot populate this table.</p>
        </div>
        <button className="btn-secondary inline-flex items-center gap-2" onClick={() => farmers.refetch()} disabled={!stateId || farmers.isFetching}>
          <RefreshCw className={`h-4 w-4 ${farmers.isFetching ? 'animate-spin' : ''}`} /> Refresh
        </button>
      </header>

      <section className="rounded-card border border-line bg-surface p-4">
        <form className="flex flex-wrap gap-2" onSubmit={(event) => { event.preventDefault(); setOffset(0); setSubmitted(search.trim()) }}>
          <select className="input min-w-52" value={stateId} onChange={(event) => { setStateId(event.target.value); setOffset(0) }} aria-label="State">
            <option value="">Select a state</option>
            {states.data?.map((state) => <option key={state.id} value={state.id}>{state.name}</option>)}
          </select>
          <div className="relative min-w-64 flex-1">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
            <input className="input w-full pl-9" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search farmer name or code" />
          </div>
          <button className="btn-primary" type="submit" disabled={!stateId}>Search</button>
        </form>
      </section>

      <section className="rounded-card border border-line bg-surface p-4">
        <div className="mb-3 flex items-center gap-2"><Upload className="h-4 w-4 text-accent" /><strong>Import approved CSV</strong></div>
        <p className="mb-3 text-xs text-ink-3">Required columns: state_id, first_name. The source must be approved, contain PII, and have a lawful import route.</p>
        <form className="flex flex-wrap gap-2" onSubmit={(event) => { event.preventDefault(); importer.mutate() }}>
          <input className="input" value={sourceCode} onChange={(event) => setSourceCode(event.target.value)} placeholder="Source code" aria-label="Source code" />
          <input className="input flex-1" type="file" accept=".csv,text/csv" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
          <button className="btn-primary" disabled={!file || importer.isPending}>Import</button>
        </form>
        {importer.data && <p className="mt-3 text-sm text-ink-2">Created {importer.data.created}; skipped {importer.data.skipped}.</p>}
        {importer.isError && <p className="mt-3 text-sm text-quarantine">{importer.error.message}</p>}
      </section>

      {!stateId ? (
        <div className="rounded-card border border-line bg-surface p-8 text-center text-ink-2">Select a state to query the partitioned farmer registry.</div>
      ) : farmers.isLoading ? (
        <div className="rounded-card border border-line bg-surface p-8 text-center text-ink-2">Loading farmers…</div>
      ) : farmers.isError ? (
        <div className="rounded-card border border-quarantine/30 bg-quarantine/5 p-5 text-sm text-quarantine">The farmer registry could not be loaded.</div>
      ) : farmers.data?.results.length ? (
        <section className="overflow-hidden rounded-card border border-line bg-surface">
          <div className="flex items-center gap-3 border-b border-line px-5 py-4"><Sprout className="h-5 w-5 text-accent" /><strong>{farmers.data.count.toLocaleString('en-IN')}</strong><span className="text-sm text-ink-2">farmers</span></div>
          <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="bg-sunken text-xs uppercase tracking-wide text-ink-3"><tr><th className="px-5 py-3">Farmer</th><th className="px-5 py-3">Location</th><th className="px-5 py-3">Class</th><th className="px-5 py-3">Area</th><th className="px-5 py-3">Quality</th></tr></thead>
            <tbody className="divide-y divide-line">{farmers.data.results.map((row) => <tr key={`${row.state_id}-${row.id}`}><td className="px-5 py-3"><div className="font-medium text-ink">{[row.first_name, row.last_name].filter(Boolean).join(' ')}</div><div className="font-mono text-xs text-ink-3">{row.farmer_code ?? row.id.slice(0, 8)}</div></td><td className="px-5 py-3 text-ink-2">{[row.district?.name, row.state.name].filter(Boolean).join(', ')}</td><td className="px-5 py-3 capitalize text-ink-2">{row.farmer_class.replaceAll('_', ' ')}</td><td className="px-5 py-3 text-ink-2">{row.total_area_ha ? `${row.total_area_ha} ha` : '—'}</td><td className="px-5 py-3 capitalize text-ink-2">{row.quality_tier}</td></tr>)}</tbody>
          </table></div>
          <div className="flex items-center justify-between border-t border-line px-5 py-3"><button className="btn-secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button><span className="text-xs text-ink-3">{offset + 1}–{Math.min(offset + PAGE_SIZE, farmers.data.count)}</span><button className="btn-secondary" disabled={offset + PAGE_SIZE >= farmers.data.count} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button></div>
        </section>
      ) : (
        <div className="rounded-card border border-line bg-surface p-8 text-center text-ink-2">No farmers are stored for this state yet. The SFAC organisation scrape does not create farmer rows.</div>
      )}
    </div>
  )
}
