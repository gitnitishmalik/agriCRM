# 10 · Communication Engine — WhatsApp & Email

🔴 **The governing rule for this entire module:** every outbound message is sent to a recipient drawn from `comm.v_messageable_farmer` (or its person-equivalent), and consent is re-checked at dispatch time. There is no other path. Enforce it in code review and with a CI grep for direct queries against `core.farmer` inside `apps/communications`.

---

## 1. Consent architecture

### 1.1 The ledger

`comm.consent_event` is **append-only** — a trigger raises an exception on UPDATE or DELETE ([smoke test 5](./sql/smoke_test.sql)). Current state is materialised into `comm.consent_current` by trigger. The ledger is what you show a regulator; the materialised table is what you query.

Every event records:

| Field | Why it's there |
|---|---|
| `channel` | whatsapp / sms / email / voice / postal / in_app |
| `purpose` | transactional / service_update / advisory / marketing / survey / project_specific |
| `status` | never_asked / opted_in / opted_out / withdrawn / expired / bounced_out |
| `evidence_type` | signed_form / in_app_checkbox / whatsapp_optin / ivr_confirmation / mou_clause / sms_keyword |
| `evidence_ref`, `evidence_url` | The artefact — form ID, message ID, document key |
| `language` | 🔴 Which language the notice was shown in |
| `notice_version` | 🔴 Which version of the privacy notice they saw |
| `captured_by`, `captured_at`, `ip_address`, `device_id` | Who captured it, when, from where |
| `expires_at` | Optional time-limited consent |

`language` and `notice_version` are the two fields people omit and then cannot reconstruct. A consent whose notice text you cannot produce is a consent you cannot defend.

### 1.2 Purpose separation

**Consent is per purpose, not blanket.** A farmer who agreed to weather advisories has not agreed to insurance marketing. The `consent_purpose` enum makes conflation structurally impossible: a marketing campaign queries `purpose='marketing'` and simply will not return advisory-only subscribers.

This is not pedantry. Purpose limitation is an explicit DPDP obligation, and it is also the thing that keeps your opt-out rate low — people unsubscribe when they get messages they didn't ask for.

### 1.3 Suppression outranks everything

`comm.suppression` is keyed on the normalised phone or email, optionally per channel. It beats a fresh opt-in ([smoke test 8](./sql/smoke_test.sql)).

Reasons: `user_optout` · `complaint` · `hard_bounce` · `legal_request` · `deceased` · `wrong_number`

🔴 Suppression survives re-import. This is the guardrail that protects you when someone re-uploads a six-month-old list containing a number that complained.

### 1.4 Opt-out handling

| Trigger | Handling |
|---|---|
| WhatsApp reply: STOP / BAND / बंद / बंद करें / रोको / ROKO / निकालो | Immediate |
| WhatsApp "Stop promotions" button on a template | Immediate |
| Email unsubscribe link (one-click, no login) | Immediate |
| Told to a field agent | Agent records it in the app |
| Inbound call to the helpline | Ops records it |

**Sequence, all within 5 seconds:**
1. `comm.inbound_message` written with `intent='optout'`
2. `comm.consent_event` inserted with `status='opted_out'` (trigger updates `consent_current`)
3. `comm.suppression` row inserted
4. Any queued messages to that recipient cancelled
5. Confirmation reply sent in their language
6. `crm.activity` written to the timeline

Multilingual keyword list is configurable and must cover every language you operate in. Test it in each one before launch — an opt-out keyword that isn't recognised becomes a complaint.

---

## 2. WhatsApp integration

### 2.1 Setup checklist

- [ ] Meta Business Manager account, business verified under the Theta Analytics legal entity
- [ ] WhatsApp Business Account created
- [ ] Phone number registered (a dedicated number, never a personal one)
- [ ] Display name approved — must match your legal/trading name
- [ ] Green tick (official business account) applied for once volume justifies it
- [ ] Webhook endpoint registered and signature verification implemented
- [ ] Message templates submitted and approved
- [ ] Quality rating monitoring and alerting wired up

### 2.2 Categories and cost

Meta's India per-message rates effective **1 July 2026**:

| Category | Rate (INR) | Use for |
|---|---|---|
| **Utility** | ~₹0.115 | 🔴 Your primary category. Payment schedules, harvest slips, meeting notices, order updates, advisory tied to an existing relationship. |
| **Authentication** | ~₹0.115 | OTPs |
| **Marketing** | ~₹0.8631 | Promotional. **7.5× the cost and far more likely to draw blocks.** Use sparingly. |
| **Service** | Free until 30 Sep 2026, then ~₹0.115 | Free-form replies within the 24-hour customer service window |

From **1 October 2026**, service messages and in-window utility templates become billable at the utility rate.

**The cost lesson:** 200,000 utility messages/month ≈ ₹23,000. The same volume as marketing ≈ ₹1,72,620. Categorise correctly — and note that correct categorisation and low block rates are the same behaviour, because utility messages are the ones people actually want.

### 2.3 Template design

Templates must be approved before use. Rejection reasons are usually: promotional content submitted as utility, unclear variable usage, or missing context.

**Approved-style utility template:**

```
Name:     cane_payment_schedule_hi
Category: UTILITY
Language: hi

नमस्ते {{1}} जी,
{{2}} मिल में आपकी गन्ना पर्ची का भुगतान {{3}} को
आपके खाते में भेजा जाएगा।
राशि: ₹{{4}}

किसी सहायता के लिए: {{5}}

[Button: Stop promotions]
```

**Rules:**
- Variables are positional; validate count and order before sending
- Every marketing template carries a **Stop promotions** quick-reply button
- Maintain a version per language: `hi`, `en`, `mr`, `pa`, `te`, `kn`, `gu`, `ta`
- Store `provider_template_id` and `approval_status` in `comm.template`; sync status from Meta nightly
- Media headers (images, PDFs) require hosting the asset and passing a URL — S3 presigned URLs work

### 2.4 Sending

```python
# Simplified — real implementation lives in apps/communications/whatsapp.py
def send_template(recipient, template, variables, campaign=None):
    # 1. RE-CHECK at dispatch time — state may have changed since preview
    if not is_messageable(recipient, channel='whatsapp', purpose=campaign.purpose):
        record_message(state='suppressed'); return

    # 2. Quiet hours — no sends 21:00–08:00 IST
    if in_quiet_hours(): schedule_for_next_window(); return

    # 3. Throttle (per-second, Redis token bucket)
    rate_limiter.acquire('whatsapp')

    # 4. Send
    resp = meta_client.messages.create(
        to=recipient.phone_e164,
        type='template',
        template={'name': template.code,
                  'language': {'code': template.language},
                  'components': build_components(variables)})

    # 5. Persist + timeline
    record_message(state='sent', provider_message_id=resp['messages'][0]['id'])
    write_activity(recipient, 'whatsapp', direction='outbound')
```

### 2.5 Webhooks

Meta sends: `sent` · `delivered` · `read` · `failed` (with an error code) · inbound messages · template status changes · **quality rating changes**.

🔴 **Webhook handler rules:**
1. Verify the `X-Hub-Signature-256` HMAC before doing anything
2. Respond **200 within 5 seconds** — Meta retries on failure and disables endpoints that keep failing
3. Write the raw payload to Redis and return; process asynchronously
4. Handle duplicates — Meta redelivers

**Failure codes worth handling specifically:**

| Code | Meaning | Action |
|---|---|---|
| 131026 | Message undeliverable (not on WhatsApp) | Set `contact_point.is_whatsapp_capable=false` |
| 131047 | Re-engagement required (24h window closed) | Send a template instead of free-form |
| 131049 | Blocked for user's own experience | Do not retry; reduce frequency to that user |
| 132000–132xxx | Template errors | Fix the template; alert |
| 130472 | User in an experiment group | Skip |
| 368 | Account temporarily blocked (policy) | 🔴 **Pause all sending and escalate immediately** |

### 2.6 Quality rating — protect it above all else

Meta assigns Green / Yellow / Red based on block and report rates. Red leads to messaging limits and then account disablement.

| Practice | Effect |
|---|---|
| Utility over marketing | Largest single factor |
| Genuine opt-in | Second largest |
| Relevance (right crop, right district, right season) | Large |
| Frequency cap: **max 3 messages/week/recipient** | Large |
| Quiet hours 21:00–08:00 IST | Moderate |
| Local language | Moderate |
| Clear sender identity in the first line | Moderate |
| Easy visible opt-out | Moderate |

**Automated guardrails to build:**
- Alert the moment quality drops from Green
- Auto-pause all campaigns if a campaign's opt-out rate exceeds **1%**
- Auto-pause if failure rate exceeds **5%**
- Hard frequency cap enforced in the send path, not just in campaign config
- Weekly quality report to the campaign manager

### 2.7 Inbound and the 24-hour window

When someone messages you, a 24-hour service window opens in which you may send free-form replies. Outside it, only approved templates.

Route inbound messages to a shared team inbox with assignment, or to an FAQ auto-responder for common queries (payment status, weather, prices), with escalation to a human. If you need a full agent inbox on day one, this is the one strong argument for using a BSP initially.

---

## 3. Email

### 3.1 Setup

Amazon SES in `ap-south-1`. Before the first send:

- [ ] Domain verified; **SPF, DKIM and DMARC** configured (DMARC at `p=none` initially, tighten later)
- [ ] Dedicated IP only above ~500k/month; shared IP is better below that
- [ ] Production access requested (out of sandbox)
- [ ] SNS topics wired for bounces, complaints and deliveries
- [ ] Separate configuration sets for transactional and campaign traffic — 🔴 keep the reputations separate so a bad campaign cannot break your OTP delivery
- [ ] Domain warmed over 3–4 weeks: start at ~500/day, roughly double weekly

### 3.2 Bounce and complaint handling

🔴 This is not optional. SES suspends accounts over a ~5% bounce rate or ~0.1% complaint rate.

| Event | Action |
|---|---|
| **Hard bounce** | Immediate `comm.suppression` (`hard_bounce`); `contact_point.verification='invalid'` |
| **Soft bounce** | Increment `delivery_failures`; suppress after 3 |
| **Complaint** | Immediate suppression + `consent_event(status='opted_out')`; investigate the campaign |
| **Delivery** | Update `comm.message`; light verification signal |
| **Open** | Weak signal (proxies inflate it) — do not treat as verification |
| **Click** | Strong signal; confidence 0.85 |

### 3.3 Email use cases

**Transactional** (no marketing consent needed — these are service messages): OTP, password reset, export ready, report delivery, DSR response, meeting confirmation.

**Business/campaign** (to organisation contacts — MDs, cane managers): proposals, market intelligence newsletters, project updates, event invitations, seasonal advisories.

Email is far more useful for the **institutional** audience than the farmer audience. FPO CEOs and mill managers read email; most farmers do not have a working address. Plan channel mix accordingly: WhatsApp for farmers, email + WhatsApp for institutions.

---

## 4. Campaigns

### 4.1 Segment builder

A visual filter tree serialised to `comm.campaign.segment_definition` (JSONB).

**Filterable:** state / district / block / village · crop and season · land size band · farmer class · FPO or mill linkage · quality tier · last-contact age · consent status and purpose · language · tags · engagement history · project participation · **inside a specific mill's command area**

**Preview response — always shows the exclusion breakdown:**

```
Segment: Sugarcane farmers, Muzaffarnagar district, land > 1 ha

  Matched by attributes ............. 12,400
  ─────────────────────────────────────────
  Excluded — no consent ............. −2,100
  Excluded — suppressed .............   −890
  Excluded — 3+ delivery failures ...   −230
  ─────────────────────────────────────────
  ELIGIBLE .......................... 9,180

  Est. cost: 9,180 × ₹0.115 = ₹1,056
  Template: cane_payment_schedule_hi (UTILITY, approved)
```

🔴 **Always show the exclusions.** It builds the habit of thinking about consent as a first-class number, and it makes an unusual exclusion count visible immediately — a segment that's 60% excluded is telling you something about a data source.

### 4.2 Approval and launch

Campaigns above a configurable threshold (suggested: 5,000 recipients, or any marketing-category send) require approval by a Campaign Manager or above. `approved_by` and `approved_at` are recorded.

Launch enqueues a Celery chord. Progress is visible live: sent / delivered / read / failed / opted-out, with an abort button that actually stops the queue.

### 4.3 Campaign metrics

Per campaign: targeted · eligible · sent · delivered · read · failed · replied · opted-out · cost. Delivery rate, read rate, opt-out rate, cost per read.

**Benchmarks for a healthy consented agri audience on WhatsApp:**

| Metric | Healthy | Investigate |
|---|---|---|
| Delivery rate | >95% | <90% |
| Read rate | 60–85% | <40% |
| Opt-out rate | <0.3% | >1% 🔴 auto-pause |
| Failure rate | <3% | >5% 🔴 auto-pause |

Read rates on WhatsApp are dramatically higher than email — 70% vs 20% is typical. This is why WhatsApp is worth protecting so carefully, and why a scraped list that costs you the channel is such an expensive mistake.

---

## 5. Automated (triggered) messaging

Beyond campaigns, event-driven sends — all utility category, all consent-gated:

| Trigger | Message |
|---|---|
| Cane payment date announced by a partner mill | Payment schedule to that mill's consented growers |
| Weather alert for a district | Advisory to farmers in that district |
| Mandi price crosses a farmer's watch threshold | Price alert |
| Crop stage reached (from sowing date + variety maturity) | Stage-specific advisory |
| Field visit logged with `next_action` | Follow-up reminder to the farmer |
| FPO meeting scheduled | Meeting notice to members |
| Project milestone reached | Update to project contacts |
| Data verification needed | "Is this still your number?" with a reply button |

That last one is a verification loop disguised as a message: cheap, useful to the recipient, and it upgrades records to Gold ([Doc 07](./07-data-quality-organic.md) §5).

---

## 6. Compliance controls summary

| Control | Implementation |
|---|---|
| Consent required before send | `comm.v_messageable_farmer`; re-checked at dispatch |
| Purpose limitation | `consent_purpose` on both consent and campaign |
| Easy withdrawal | STOP keywords (multilingual), template buttons, one-click email unsubscribe, agent-recorded |
| Suppression permanence | `comm.suppression`, survives re-import, outranks opt-in |
| Quiet hours | 21:00–08:00 IST enforced in the send path |
| Frequency cap | Max 3/week/recipient, enforced in the send path |
| Audit trail | Every message in `comm.message` + `crm.activity`; 24-month retention |
| Approval gate | Large and marketing campaigns need a named approver |
| Auto-pause | Opt-out >1% or failure >5% |
| Notice versioning | `notice_version` on every consent event |
| Data residency | SES in ap-south-1; Meta is a processor — document it in your processor register |

---

## Sources

- [WhatsApp API Pricing 2026: Official Rates & Calculator — FlowCall](https://www.flowcall.co/blog/whatsapp-business-api-pricing)
- [WhatsApp Business API Pricing in India (2026) — AiSensy](https://aisensy.com/pricing)
- [WhatsApp Business API Pricing 2026: Conversation Categories, Costs, and What Changed — Blueticks](https://blueticks.co/blog/whatsapp-business-api-pricing-2026)
- [DPDP Rules 2025: India's Complete Compliance Guide — Seclore](https://www.seclore.com/fundamentals/dpdp-rules-2025-compliance-guide/)
