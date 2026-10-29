# Credit Card Reconciliation

An executable POC based on the supplied **Credit Card Reconciliation Agent – Solution Design and Implementation Guide**. The document's body describes a Next.js + Python architecture with a configurable model layer; it does not require Logic Apps or Foundry Agent Service as the orchestrator. The worker therefore owns orchestration.

This is a local reference implementation, not a completed Azure/SAP production rollout. See [implementation coverage](docs/implementation.md) for the production work still required.

## Run locally

Use Python 3.12+ and Node 22+. From the repository root:

```sh
python -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cd backend
AUTH_MODE=dev SAP_MODE=mock ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal, using the same `backend` working directory and data directory:

```sh
AUTH_MODE=dev SAP_MODE=mock ../.venv/bin/python -m app.worker
```

In a third terminal, from `frontend`:

```sh
npm install
AUTH_MODE=dev DEV_API_TOKEN=dev-preparer API_BASE_URL=http://127.0.0.1:8000 npm run dev
```

Upload `examples/statement.csv` with total **350.00 AED**. Two transactions match mock SAP (R1 and R2); the hotel charge becomes a manual exception. Prepare its balanced JV in the review screen. Restart the frontend with `DEV_API_TOKEN=dev-approver` to approve and post it. Resolve the exception with a comment and close the batch. Development tokens are accepted only with explicit backend `AUTH_MODE=dev`; the frontend disallows them in production mode. Never deploy dev mode to a shared environment.

The backend is available at port 8000; `/docs` exposes the OpenAPI UI. All business routes require authentication. Only `/health`, `/ready`, and API documentation are unauthenticated. State and mock SAP document references persist in `backend/data/`. Do not delete this directory when investigating a posting.

## Verify

```sh
cd backend
python -m unittest discover -s tests -v
cd ../frontend
npm run typecheck
npm run build
```

The tests exercise CSV intake, matching, duplicate detection, balance/master-data validation, role denial, self-approval denial, dual approval, uncertain posting recovery, idempotency, and model governance. They use real local persistence and mocked SAP, without contacting SAP or a model.

## Entra sign-in

Register separate API and confidential web applications. Configure API app roles `Preparer`, `Approver`, `Admin`, `Auditor` and assign groups. Supply environment variables from `.env.example` to each process (the backend does not automatically load `.env`). Set backend `AUTH_MODE=entra`, audience and tenant. Set web client ID/secret, API delegated scope, redirect URI, and a cryptographically random session password of at least 32 characters through your secret manager. Register `/api/auth/callback` as a web redirect URI.

The web BFF uses MSAL authorization-code flow with PKCE/state. Encrypted HTTP-only cookies carry an opaque session ID; access tokens stay in a server-side, process-local store and expire with the token. Sign in again after expiry or restart. Production needs a shared encrypted session store, logout UI, and token refresh. Mutating BFF requests require a matching Origin. The API validates token issuer, audience, signature, expiration and roles independently.

## SAP

Mock mode is the local default. For live SAP install `backend/requirements-azure.txt` and configure `SAP_MODE=live`, `SAP_BASE_URL`, `SAP_APIM_SCOPE`, `SAP_ACCOUNT`, company code and allowed master data. The worker/API use managed identity through `DefaultAzureCredential` to call APIM. The adapter preserves cookies, fetches CSRF tokens, retries reads only, simulates JVs, and looks up ClientReference before posting.

The ABAP team must implement the contract in [docs/sap-contract.md](docs/sap-contract.md), including **atomic deduplication in SAP**. Never claim local idempotency alone prevents duplicates across an external SAP timeout. The `recover` API can reconcile an existing document but deliberately refuses an automatic retry when lookup is empty.

## Governed models

Without an active assignment the worker produces manual exceptions. To enable model calls:

1. Install Azure optional dependencies if using managed identity or Key Vault.
2. Allowlist the model hostname in `LLM_ALLOWED_HOSTS` and the runtime network policy.
3. Create a provider with `/admin/llm-providers` (Admin). Supported endpoints implement OpenAI-compatible `chat/completions`; Azure base URLs include the deployment path and API version.
4. Test it. Run a finance-labelled dataset using `python -m app.evaluate PROVIDER_ID DATASET.json` from `backend`. At least 100 cases/outcomes and 95% outcome accuracy are required. Scores cannot be supplied through an HTTP endpoint.
5. A second admin approves the provider, then a task assignment request with `primary` and optional `fallback`; another admin approves the request. Internet models require recorded security approval.

The adapter sends only unmatched transactions, available SAP items and allowed master data. It validates JSON and references, retries once, tries the fallback, and otherwise sends work to manual review. No posting tool is exposed. Proposed JVs are checked again against master data and SAP simulation. This POC uses the document's JSON-prompt fallback rather than tool-calling loops. Accuracy evaluates outcomes, not accounting correctness of drafted JVs; finance must review every draft.

## Container

Build the backend from the repository root with `docker build -f backend/Dockerfile -t ccrecon-api .`. The same image starts the worker with `python -m app.worker`. Mount the same `/data` directory for this single-machine POC. Do not use SQLite on independently scaled Azure replicas; migrate to Azure SQL and a durable cloud queue first.
