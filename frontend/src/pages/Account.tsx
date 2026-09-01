/**
 * Account — role, territory and permissions.
 *
 * This is the one screen that reads real backend data today, and it is worth
 * having beyond that: Doc 12 §3 scopes an agent to their districts in
 * PostgreSQL itself, so "which districts can I see" is a question with a
 * consequential answer. Showing it here means a misconfigured territory
 * surfaces as a visible fact rather than as mysteriously absent records.
 */

import { useMe } from '../api/auth'
import { PageHeader } from '../layout/AppShell'
import { roleLabel } from '../lib/roles'
import { MaskedValue } from '../components/MaskedValue'

export function AccountPage() {
  const { data: me, isLoading, error } = useMe()

  if (isLoading) {
    return (
      <>
        <PageHeader title="Account" />
        <div className="p-6 text-base text-ink-3">Loading…</div>
      </>
    )
  }

  if (error || !me) {
    return (
      <>
        <PageHeader title="Account" />
        <div className="p-6">
          <p className="text-base text-quarantine">
            Could not load your account. Try signing out and back in.
          </p>
        </div>
      </>
    )
  }

  return (
    <>
      <PageHeader eyebrow="Your account" title={me.full_name} description={me.email} />

      <div className="grid max-w-4xl gap-4 p-6 sm:grid-cols-2">
        <section className="card p-5">
          <h2 className="label">Role</h2>
          <p className="mt-2 text-xl text-ink">{roleLabel(me.role)}</p>
          <p className="mt-2 text-sm leading-relaxed text-ink-2">
            {me.is_cross_territory
              ? 'You can see records in every district. This role is exempt from territory scoping.'
              : 'You can see records in your assigned districts only. This is enforced in PostgreSQL, not just in the interface.'}
          </p>
        </section>

        <section className="card p-5">
          <h2 className="label">Second factor</h2>
          <p className="mt-2 text-xl text-ink">{me.mfa_enforced ? 'Required' : 'Not required'}</p>
          <p className="mt-2 text-sm leading-relaxed text-ink-2">
            {me.mfa_enforced
              ? 'Your role handles data where a mistake has legal consequences, so a second factor is mandatory at every sign-in.'
              : 'Your role does not require a second factor. You can still add one.'}
          </p>
        </section>

        <section className="card p-5 sm:col-span-2">
          <h2 className="label">Territory</h2>
          {me.district_ids.length > 0 ? (
            <>
              <ul className="mt-3 flex flex-wrap gap-1.5">
                {me.district_ids.map((id) => (
                  <li
                    key={id}
                    className="rounded-chip bg-sunken px-2 py-1 font-mono text-sm text-ink-2 ring-1 ring-inset ring-line"
                  >
                    {id}
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-sm text-ink-3">
                LGD district codes. Names resolve once the geography reference data loads in
                Phase 1.
              </p>
            </>
          ) : (
            <p className="mt-2 text-base text-ink-2">
              {me.is_cross_territory
                ? 'Not applicable — your role sees every district.'
                : 'No districts assigned, so you will not see any records. Ask an administrator to set your territory.'}
            </p>
          )}
        </section>

        <section className="card p-5 sm:col-span-2">
          <h2 className="label">Permissions</h2>
          {/*
            🔴 There is no per-user permission list, and this section must not
            imply there is one. Every capability in this system is derived from
            the role — `contact.view_full` is `{data_ops, compliance, admin}` in
            `domain/pii.py`, and the import and billing overrides are the same
            shape. An earlier version read `me.permissions`, which `/auth/me/`
            has never returned: the field was `undefined`, `.length` threw, and
            this whole page rendered blank for every user.
          */}
          <p className="mt-2 text-base text-ink-2">
            No individual permissions granted. Your access comes from your role.
          </p>
          <p className="mt-3 border-t border-line pt-3 text-sm text-ink-3">
            <code className="font-mono">contact.view_full</code> and{' '}
            <code className="font-mono">import.commit</code> are reviewed every quarter.
          </p>
        </section>

        <section className="card p-5 sm:col-span-2">
          <h2 className="label">How contact details appear to you</h2>
          <div className="mt-3 flex flex-wrap items-center gap-6">
            <MaskedValue masked="+91 98XXX XX210" kind="phone" canReveal={false} />
            <MaskedValue masked="r****h@example.in" kind="email" canReveal={false} />
          </div>
          <p className="mt-3 text-sm leading-relaxed text-ink-2">
            Phone numbers and emails are masked for everyone by default. Revealing one needs
            the <code className="font-mono">contact.view_full</code> permission, happens one
            record at a time, and is written to the access log. There is no bulk reveal.
          </p>
        </section>
      </div>
    </>
  )
}
