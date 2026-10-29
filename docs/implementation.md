# Implementation coverage and deployment boundaries

## Implemented

- FastAPI API, persistent SQLite database, durable transactional local job queue, separate worker.
- CSV parsing with strict schema, decimal amounts, statement-total verification, SHA-256 file deduplication and transaction fingerprints. One provider/company-code POC.
- R1 reference/amount, R2 card/date/amount, R3 split-reference totals, R4 AED rounding. Ambiguity is held for review; accepted SAP item claims are unique across batches.
- Batch results, job status, manual exception resolution, closure checks, JV proposal and SAP simulation, rejection comments, separate approver, configurable second approval threshold.
- Stored-content-only posting, 16-character SAP reference, durable posting claim, repeat calls return the original document, uncertain-outcome recovery without blind retry.
- Mock SAP and HTTP OData integration with managed identity, session cookies, CSRF and read retry.
- Governed OpenAI-compatible model adapter, endpoint allowlist, PAN masking, validated outcomes, bounded retry/fallback, master-data validation, evaluation CLI, second-admin provider and assignment approvals, usage in audit events.
- Next.js finance worklist, match decisions, manual exceptions, journal review/actions, audit view, provider management and rules. Fluent UI and TanStack Query. API role checks remain authoritative.
- Entra JWT verification, MSAL server-side BFF, cookie session ID, PKCE/state, Origin enforcement, Dockerfile, CI validation and examples.

## Required before production / not implemented

The following are explicit remaining deliverables, not implied by this POC:

- Azure SQL SQLAlchemy models, Alembic migrations and managed-identity SQL connections. Local persistence is SQLite with JSON aggregates, not the document's normalized cloud schema.
- Blob intake/processed/error lifecycle, Storage Queue or Service Bus adapter, leases, poison/dead-letter handling. Local uploads remain under `statements-in`; there is no automatic archival/move lifecycle.
- Full private Azure landing-zone Bicep/Terraform, private DNS, firewall policies, APIM deployment/policies, RBAC provisioning and deployment pipeline. No Azure resources have been deployed by this task.
- SAP SEGW/ABAP implementation and Basis validation, account/vendor determination, fiscal-period/document-type configuration, currency/date mappings confirmed against actual metadata, licensing approval.
- PDF/Document Intelligence, MT940 and mailbox/SFTP intake adapters. CSV is the only accepted format.
- Real tool-calling agent loop and provider-specific adapters beyond OpenAI-compatible JSON. Cost prices, latency analytics, token caps, task-specific assignments and a finance-approved evaluation corpus/threshold configuration.
- Operator evidence for security approval, model-edit versioning, independent security role/workflow and revocation. The current creation-time security attestation is reviewed by the second admin.
- Production session store, token refresh, role-aware UI visibility, generated OpenAPI TypeScript contracts, advanced grids, bulk decisions, accessible dialogs instead of browser prompts, audit export and full master-data administration.
- Dedicated outbox/posting worker, controlled uncertain-post retry procedure with SAP operator reconciliation, immutable/WORM audit retention, audit pagination and correlation across services.
- OpenTelemetry/Application Insights, alerts and alert drills, penetration testing, backup/restore, DR, production rollout approvals, three-month replay and parallel finance reconciliation.

No model, SAP or Azure credentials were available for live integration tests. Unit/API tests validate the local implementation only. Frontend build status is recorded in the task report; it must pass after dependency installation before deployment.

## Evaluation dataset

The evaluation CLI consumes a JSON array with at least 100 labelled cases:

```json
[
  {
    "input": {
      "transactions": [{"id":"T1","amount":"10.00","currency":"AED","merchant":"Sample","reference":"REF"}],
      "sap_items": [{"id":"I1","amount":"10.00","currency":"AED"}],
      "master_data": {"accounts":["400100","210000"],"cost_centres":["CC100"]}
    },
    "expected": {"T1":{"outcome":"match","sap_id":"I1"}}
  }
]
```

This example describes the shape only and is not an evaluation corpus. Finance must supply independently labelled historical cases.
