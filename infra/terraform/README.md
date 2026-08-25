# Infrastructure

Terraform 1.9+, AWS **ap-south-1 (Mumbai)** only.

🔴 Data residency is both a compliance position under DPDP and a sales argument
with cooperative banks and government-linked buyers (Doc 03 §7). No resource in
this directory may be provisioned in another region.

## Status

**Scaffolded, not yet applied.** `envs/staging/main.tf` declares the intended
topology with the guardrails encoded, but no state has been created and no
backend is configured. Applying it needs an AWS account, a state bucket, and
the credentials to create both — see "First apply" below.

Phase 0's exit gate is met by the local Docker environment plus CI. Standing up
real staging infrastructure is the first task of Phase 1, once someone with
billing access can run it.

## Topology (Doc 03 §7, Doc 04 §1)

```
Route53 → CloudFront → ALB → ECS Fargate
                              ├── api        2–8 tasks, autoscale on CPU + request count
                              ├── worker     2–6 tasks, autoscale on queue depth
                              ├── collector  1–2 tasks, isolated from messaging
                              ├── messaging  1–4 tasks, dedicated queue + throttle
                              └── beat       🔴 exactly 1 task, ever
                                     │
        RDS PostgreSQL 16 Multi-AZ (+ read replica from Phase 7)
        ElastiCache Redis
        S3 (documents, imports, exports — SSE-KMS, versioned)
        Secrets Manager
```

## Non-negotiable constraints

These belong in code review for every change here:

| Constraint | Why |
|---|---|
| 🔴 RDS and ElastiCache in **isolated subnets**, no route to the internet | Doc 12 §7. No public database endpoint, ever — including "just for a migration". |
| 🔴 Exactly one `beat` task | Two schedulers means every collector, decay pass and retention job runs twice (Doc 04 §2). |
| 🔴 Staging is single-AZ and scaled down, but never holds production PII | R11. Use `manage.py generate_synthetic_data`. |
| Security groups reference each other by group ID, not CIDR | Least privilege that survives a subnet change. |
| Encryption at rest with a **customer-managed** KMS key | Doc 12 §6. |
| Secrets only in Secrets Manager | Nothing in env files, nothing in the repo. |

## First apply

1. Create the state bucket and DynamoDB lock table out of band (chicken-and-egg).
2. Fill `envs/staging/backend.tf` with those names.
3. `terraform init && terraform plan` — **read the plan** before applying.
4. Apply, then confirm: RDS has no public endpoint, and the ECS task role can
   read only the secrets it needs.

Production is a copy of staging with Multi-AZ on, larger instance classes, and
a separate state file. Do not share state between environments.
