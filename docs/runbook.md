# Operations runbook

**Uncertain posting:** retain the database and audit trail. Do not change `posting` back to `approved`. Call `/jv/{id}/recover` as Approver to look up the reference. If found, the API stores the original document. If not found, SAP support must determine that the original request has ended and confirm whether any document exists. Controlled retry tooling is a production prerequisite; do not manually issue a fresh reference.

**SAP outage:** jobs fail with an integration error and batch status `failed`. Keep the uploaded file. Fix networking, APIM identity or Gateway availability, inspect the job/audit trail, and have an operator retry only after diagnosing the failure. This POC has no HTTP retry-job action; requeueing requires an operator and confirmation that no downstream effects were committed. Reads may retry; writes never retry blindly.

**Rejected CSV:** inspect job error and compare mandatory fields and billed AED total. Duplicate file hashes and duplicate transaction fingerprints are blocked. Correct source data through the finance provider's process; do not edit financial data merely to pass validation. Never delete a posted batch to reload a file.

**Model outage:** the worker retries once and attempts its approved fallback; unresolved lines become manual exceptions. Configure a replacement through the evaluated second-admin workflow. Never approve a provider by inserting a fabricated evaluation score. Merchant text and model output are untrusted.

**Session expiry:** sign in again. Local web token sessions do not survive restart or scaling; use a shared encrypted store for production.
